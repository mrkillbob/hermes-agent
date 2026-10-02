#!/usr/bin/env node
import { execFileSync, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { delimiter, dirname, join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { parseArgs } from 'node:util'

// npm.cmd needs a shell. Use npm's JS entrypoint so paths remain argv on Windows.
export function npmCommand({ env = process.env } = {}) {
  const names = process.platform === 'win32' ? ['npm.cmd', 'npm'] : ['npm']
  const candidates = [env.npm_execpath]
  for (const dir of (env.PATH || env.Path || '').split(delimiter)) {
    for (const name of names) {
      const bin = join(dir, name)
      if (!existsSync(bin)) continue
      const prefix = dirname(realpathSync(bin))
      const lib = join(prefix, '../lib')
      candidates.push(
        join(prefix, 'node_modules/npm/bin/npm-cli.js'),
        join(prefix, '../lib/node_modules/npm/bin/npm-cli.js'),
        // Nix packages use lib/npm or a versioned lib/npm* directory.
        // Discover the installed JS entrypoint, never parse/execute its shell wrapper.
        ...(existsSync(lib) ? readdirSync(lib).filter(name => name.startsWith('npm')).sort()
          .map(name => join(lib, name, 'bin/npm-cli.js')) : []),
        realpathSync(bin),
      )
    }
  }
  const cli = candidates.find(path => path && path.endsWith('.js') && existsSync(path))
  if (!cli) throw new Error('npm CLI is required on PATH')
  return [process.execPath, cli]
}

// npm's own manifest states its version — no child spawn. A node-under-node spawn
// hits Windows Job-Object EBUSY (#123933), and the probe runs before the reuse
// short-circuit: a read-only version read must not abort an otherwise complete run.
// npm_execpath may point through a symlink; the manifest sits beside the resolved
// CLI, never beside the link. Layouts without a readable manifest return undefined
// so the caller falls back to the child probe, matching the pre-manifest behavior.
function npmManifestVersion(cli) {
  try {
    return JSON.parse(readFileSync(join(dirname(realpathSync(cli)), '..', 'package.json'), 'utf8')).version
  } catch {
    return undefined
  }
}

function completedInstallMatches({ source, receipt, hiddenLock, key, nativeKey }) {
  if (!existsSync(receipt) || !existsSync(hiddenLock)) return false
  const installed = readFileSync(hiddenLock)
  const expected = `${key}\n${createHash('sha256').update(installed).digest('hex')}\n`
  if (nativeKey !== undefined) {
    const nativeReceipt = `${receipt}.native-toolchain`
    if (!existsSync(nativeReceipt) || readFileSync(nativeReceipt, 'utf8') !== `${expected}${nativeKey}\n`) return false
  }
  return readFileSync(receipt, 'utf8') === expected && Object.keys(JSON.parse(installed).packages)
    .every(path => existsSync(join(source, path)))
}

// An interrupted Windows update can leave a nested .bin that npm ci's own rmdir
// cannot clear (ENOTEMPTY, #75584); only deleting node_modules recovers it. npm's
// debug log names the code while stdio stays on the terminal, so give each run
// its own logs dir and retry once only on that code. Other failures keep the tree.
function runNpmCi(node, npm, args, { source, env }) {
  const logsDir = mkdtempSync(join(tmpdir(), 'hermes-npm-logs-'))
  // Builders set CI=1, which turns npm's spinner off. Ask for it back: npm
  // still shows it only on a terminal. Kept out of `args`, which keys the receipt.
  const run = () => execFileSync(node, [npm, ...args, '--progress=true', `--logs-dir=${logsDir}`],
    { cwd: source, env, stdio: 'inherit' })
  try {
    run()
  } catch (error) {
    const logged = readdirSync(logsDir).some(name => readFileSync(join(logsDir, name), 'utf8').includes('ENOTEMPTY'))
    if (!logged) throw error
    console.log('node-deps: npm ci hit ENOTEMPTY; removing node_modules and retrying once...')
    rmSync(join(source, 'node_modules'), { recursive: true, force: true, maxRetries: 3 })
    run()
  } finally {
    rmSync(logsDir, { recursive: true, force: true })
  }
}

// Native source installation and packaging share the same compiler resolver.
const { getWindowsCompilerEnvironment } = createRequire(import.meta.url)('./get-windows-compiler.cjs')

function desktopCompilerPreload(source, selected) {
  if (!selected.includes('apps/desktop')) return undefined
  const desktop = JSON.parse(readFileSync(join(source, 'apps/desktop/package.json'), 'utf8'))
  if (!desktop.optionalDependencies?.['get-windows']) return undefined
  const path = join(source, 'scripts/build/get-windows-compiler.cjs')
  if (!existsSync(path)) throw new Error('Desktop compiler preload is missing from the prepared source')
  return path
}

function installNodeOptions(node, npm, source, env, preload) {
  // Read only this effective setting through npm's own config precedence. Avoid
  // copying user config or replacing user preload/memory/debugging options.
  const configured = execFileSync(node, [npm, 'config', 'get', 'node-options'],
    { cwd: source, env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 15000 }).trim()
  const existing = configured && configured !== 'null' ? configured : env.NODE_OPTIONS ?? ''
  if (typeof existing !== 'string') throw new Error('npm node-options must be a string')
  // Forward slashes avoid double-backslash ambiguity in Node's Windows option parser.
  const path = process.platform === 'win32' ? preload.replaceAll('\\', '/') : preload
  return `${existing} --require=${JSON.stringify(path)}`.trim()
}

export function desktopInstallNodeOptions(source, { env = process.env } = {}) {
  const [node, npm] = npmCommand({ env })
  return installNodeOptions(node, npm, source, env, join(source, 'scripts/build/get-windows-compiler.cjs'))
}

/** A swallowed optional install failure must never certify required Windows x64 inputs. */
export function verifyDesktopWindowsBinding(source, { env = process.env, platform = process.platform,
  arch = process.arch, spawn = spawnSync } = {}) {
  if (platform !== 'win32' || arch !== 'x64') return
  const requireFromDesktop = createRequire(join(source, 'apps/desktop/package.json'))
  const entry = requireFromDesktop.resolve('get-windows')
  const root = dirname(entry)
  const manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
  if (manifest.name !== 'get-windows' || manifest.version !== '9.3.0') {
    throw new Error('Required Windows get-windows supplier does not match 9.3.0')
  }
  getWindowsCompilerEnvironment(root, env)
  const preGyp = createRequire(entry)
  const find = preGyp('@mapbox/node-pre-gyp').find
  const binding = find(join(root, 'package.json'), { target_platform: 'win32', target_arch: arch, target_libc: 'unknown' })
  const bytes = readFileSync(binding)
  if (bytes.length < 2 || bytes.subarray(0, 2).toString() !== 'MZ') {
    throw new Error('Required get-windows binding is not a Windows PE binary')
  }
  const result = spawn(process.execPath, ['-e', `const addon = require(process.argv[1]);
    if (typeof addon.getActiveWindow !== 'function' || typeof addon.getOpenWindows !== 'function')
      throw new Error('Required get-windows native exports are missing');`, binding],
  { cwd: source, env, encoding: 'utf8', timeout: 30000, maxBuffer: 1024 * 1024 })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(`Required get-windows binding could not load: ${result.stderr || result.stdout}`)
}

/** Install the full requested workspace union in one strict, locked operation. */
export function prepareNodeDependencies({ source, workspaces, env = process.env, reuse = false, install = true, nativeToolchain }) {
  source = resolve(source)
  if (!Array.isArray(workspaces) || workspaces.length === 0) {
    throw new Error('Select at least one workspace; implicit all-workspace installation is not allowed')
  }
  const manifest = JSON.parse(readFileSync(join(source, 'package.json'), 'utf8'))
  const lock = JSON.parse(readFileSync(join(source, 'package-lock.json'), 'utf8'))
  const workspacePaths = Object.values(lock.packages || {})
    .filter(entry => entry.link)
    .map(entry => entry.resolved)
  const selected = [...new Set(workspaces.map(workspace => {
    const path = workspacePaths.find(path => path === workspace || lock.packages[path]?.name === workspace)
    if (!path || !existsSync(join(source, path, 'package.json'))) {
      throw new Error(`Unknown or missing locked workspace: ${workspace}`)
    }
    return path
  }))].sort()
  const [node, npm] = npmCommand({ env })
  const npmVersion = npmManifestVersion(npm) ?? execFileSync(
    node, [npm, '--version'], { cwd: source, env, encoding: 'utf8' }).trim()
  const { satisfies } = createRequire(npm)('semver')
  for (const [name, version] of [['node', process.versions.node], ['npm', npmVersion]]) {
    const range = manifest.engines?.[name]
    if (range && !satisfies(version, range)) throw new Error(`${name} ${version} violates ${range}`)
  }
  const preload = desktopCompilerPreload(source, selected)
  const nodeOptions = preload ? installNodeOptions(node, npm, source, env, preload) : undefined
  const args = ['ci', '--no-audit', '--no-fund', '--engine-strict', '--include=dev',
    '--include=optional', '--include-workspace-root=true',
    ...selected.flatMap(workspace => ['--workspace', workspace]),
    ...(nodeOptions === undefined ? [] : [`--node-options=${nodeOptions}`]),
  ]
  // This receipt certifies dependency preparation, never compiled product freshness.
  // Keep it inside the cached tree so a clean npm ci also removes the receipt.
  const receipt = join(source, 'node_modules/.hermes-node-deps')
  // Ordinary product builders consume the baseline receipt; preparation also
  // binds lifecycle outputs to its compiler/SDK identity. On a mismatch npm ci
  // removes arbitrary package lifecycle outputs, not just known node-pty paths.
  const nativeReceipt = `${receipt}.native-toolchain`
  const nativeKey = nativeToolchain === undefined ? undefined : JSON.stringify(nativeToolchain)
  const hiddenLock = join(source, 'node_modules/.package-lock.json')
  const inputs = createHash('sha256').update(JSON.stringify({
    node: process.versions.node, npm: npmVersion, platform: process.platform, arch: process.arch, args,
    // npm names are case-insensitive; Windows Python uppercases inherited keys.
    config: Object.entries(env).filter(([key]) => /^npm_config_/i.test(key) && !/^npm_config_(cache|offline|prefer_offline)$/i.test(key))
      .map(([key, value]) => [key.toLowerCase(), value]).sort(),
  }))
  const files = ['package-lock.json', '.npmrc', ...(preload ? ['scripts/build/get-windows-compiler.cjs'] : []), ...Object.keys(lock.packages)
    .filter(path => !path.split('/').includes('node_modules'))
    .map(path => join(path, 'package.json'))].sort()
  for (const file of files) {
    inputs.update(file).update('\0').update(existsSync(join(source, file)) ? readFileSync(join(source, file)) : '<missing>').update('\0')
  }
  const key = inputs.digest('hex')
  let reusable = reuse && completedInstallMatches({ source, receipt, hiddenLock, key, nativeKey })
  if (reusable && preload) {
    try {
      verifyDesktopWindowsBinding(source, { env })
    } catch (error) {
      if (!install) throw error
      reusable = false
    }
  }
  if (reusable) {
    console.log(`node-deps: reusing completed install (${selected.join(', ')})`)
    return { source, workspaces: selected }
  }
  if (!install) throw new Error('Workspace dependencies are stale or missing and lazy installs are disabled; run an explicit build/update')
  // npm can fail during validation before deleting node_modules. Invalidate first.
  rmSync(receipt, { force: true })
  rmSync(nativeReceipt, { force: true })
  console.log(`node-deps: installing workspace dependencies with npm ci (${selected.join(', ')})...`)
  runNpmCi(node, npm, args, { source, env })
  if (preload) verifyDesktopWindowsBinding(source, { env })
  if (reuse) {
    const completed = `${key}\n${createHash('sha256').update(readFileSync(hiddenLock)).digest('hex')}\n`
    writeFileSync(receipt, completed)
    if (nativeKey !== undefined) writeFileSync(nativeReceipt, `${completed}${nativeKey}\n`)
  }
  return { source, workspaces: selected }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  // The Python bundle driver uses this same resolver, not a second layout probe.
  if (process.argv[2] === '--npm') {
    const [node, ...command] = npmCommand()
    const child = spawnSync(node, [...command, ...process.argv.slice(3)], { stdio: 'inherit' })
    if (child.error) throw child.error
    process.exit(child.status ?? 1)
  }
  const { values } = parseArgs({ options: {
    source: { type: 'string' }, workspace: { type: 'string', multiple: true },
    reuse: { type: 'boolean', default: false },
    'no-install': { type: 'boolean', default: false },
    'native-toolchain': { type: 'string' },
  } })
  if (!values.source) throw new Error('--source is required')
  prepareNodeDependencies({ source: values.source, workspaces: values.workspace, reuse: values.reuse, install: !values['no-install'], nativeToolchain: values['native-toolchain'] })
}

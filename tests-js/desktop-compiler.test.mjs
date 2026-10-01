import { execFileSync, spawnSync } from 'node:child_process'
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, expect, test } from 'vitest'
import { npmCommand, prepareNodeDependencies, verifyDesktopWindowsBinding } from '../scripts/build/node-deps.mjs'

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const roots = []
afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true })
})
const json = (path, value) => {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, JSON.stringify(value))
}

function fixture() {
  const source = mkdtempSync(join(tmpdir(), 'Desktop compiler with spaces-'))
  roots.push(source)
  json(join(source, 'package.json'), {
    name: 'compiler-fixture',
    private: true,
    workspaces: ['apps/desktop'],
    devDependencies: { 'node-gyp': 'file:vendor/node-gyp' },
    dependencies: { unrelated: 'file:vendor/unrelated' },
    engines: { node: '>=22', npm: '>=10' }
  })
  json(join(source, 'apps/desktop/package.json'), {
    name: 'desktop-fixture',
    version: '1.0.0',
    optionalDependencies: { 'get-windows': 'file:../../vendor/get-windows' }
  })
  json(join(source, 'vendor/get-windows/package.json'), {
    name: 'get-windows',
    version: '9.3.0',
    main: 'index.cjs',
    scripts: { install: 'node probe.cjs' }
  })
  writeFileSync(join(source, 'vendor/get-windows/index.cjs'), '')
  json(join(source, 'vendor/node-gyp/package.json'), { name: 'node-gyp', version: '13.0.2' })
  mkdirSync(join(source, 'vendor/node-gyp/bin'))
  writeFileSync(join(source, 'vendor/node-gyp/bin/node-gyp.js'), '')
  json(join(source, 'vendor/unrelated/package.json'), {
    name: 'unrelated',
    version: '1.0.0',
    scripts: { install: 'node probe.cjs' }
  })
  const probe = `const fs=require('node:fs');const path=require('node:path');const compiler=process.env.npm_config_node_gyp;
    fs.writeFileSync('selected.json', JSON.stringify({compiler,version:JSON.parse(fs.readFileSync(path.join(path.dirname(compiler),'..','package.json'))).version,options:process.env.NODE_OPTIONS}));`
  for (const name of ['get-windows', 'unrelated']) writeFileSync(join(source, 'vendor', name, 'probe.cjs'), probe)
  mkdirSync(join(source, 'scripts/build'), { recursive: true })
  cpSync(join(repo, 'scripts/build/get-windows-compiler.cjs'), join(source, 'scripts/build/get-windows-compiler.cjs'))
  const env = {
    ...process.env,
    npm_config_offline: 'true',
    npm_config_cache: join(source, '.npm-cache'),
    NODE_OPTIONS: '--no-warnings'
  }
  const [node, npm] = npmCommand({ env })
  execFileSync(
    node,
    [npm, 'install', '--package-lock-only', '--ignore-scripts', '--offline', '--no-audit', '--no-fund'],
    { cwd: source, env, stdio: 'pipe' }
  )
  return { source, env }
}

function selected(source, name) {
  return JSON.parse(readFileSync(join(source, 'vendor', name, 'selected.json')))
}

// These dependency-free suppliers have no executable Windows addon. The required
// Windows CI case exercises the actual supplier and canonical receipt path.
test.skipIf(process.platform === 'win32')(
  'canonical lifecycle selects the pinned compiler, preserves options and unrelated selection, and never mutates parent env',
  () => {
    const { source, env } = fixture()
    const before = { ...env }
    prepareNodeDependencies({ source, env, workspaces: ['apps/desktop'], reuse: true })
    const target = selected(source, 'get-windows')
    const other = selected(source, 'unrelated')
    expect(target.version).toBe('13.0.2')
    expect(target.compiler).toContain('node-gyp')
    expect(other.compiler).not.toBe(target.compiler)
    expect(target.options).toContain('--no-warnings')
    expect(target.options).toContain('get-windows-compiler.cjs')
    expect(env).toEqual(before)
    prepareNodeDependencies({ source, env, workspaces: ['apps/desktop'], reuse: true, install: false })
    // Changing only the tracked helper invalidates otherwise complete receipts.
    writeFileSync(join(source, 'scripts/build/get-windows-compiler.cjs'), '\n// changed\n', { flag: 'a' })
    expect(() =>
      prepareNodeDependencies({ source, env, workspaces: ['apps/desktop'], reuse: true, install: false })
    ).toThrow(/disabled/)
    const runtime = execFileSync(process.execPath, ['-p', 'process.env.NODE_OPTIONS'], {
      cwd: source,
      env,
      encoding: 'utf8'
    }).trim()
    expect(runtime).toBe('--no-warnings')
  },
  30000
)

test.skipIf(process.platform === 'win32')(
  'effective npm Node options preserve configured precedence and case-insensitive input',
  () => {
    const { source, env } = fixture()
    const options = { ...env, NPM_CONFIG_NODE_OPTIONS: '--no-deprecation' }
    prepareNodeDependencies({ source, env: options, workspaces: ['apps/desktop'] })
    expect(selected(source, 'get-windows').options).toContain('--no-deprecation')
    expect(selected(source, 'get-windows').options).not.toContain('--no-warnings')
  },
  30000
)

test('the preload validates supplier identity and refuses a wrong compiler instead of falling through', () => {
  const { source } = fixture()
  const preload = join(source, 'scripts/build/get-windows-compiler.cjs')
  // Resolve through the supplier context without relying on npm extraction.
  mkdirSync(join(source, 'vendor/get-windows/node_modules/node-gyp/bin'), { recursive: true })
  json(join(source, 'vendor/get-windows/node_modules/node-gyp/package.json'), { name: 'node-gyp', version: '12.4.0' })
  writeFileSync(join(source, 'vendor/get-windows/node_modules/node-gyp/bin/node-gyp.js'), '')
  const env = {
    ...process.env,
    npm_package_name: 'get-windows',
    npm_lifecycle_event: 'install',
    npm_package_json: join(source, 'vendor/get-windows/package.json')
  }
  const child = () => spawnSync(process.execPath, ['--require', preload, '-e', ''], { env, encoding: 'utf8' })
  expect(child().stderr).toContain('unsupported get-windows source compiler: 12.4.0')
  json(env.npm_package_json, { name: 'different', version: '9.3.0' })
  expect(child().stderr).toContain('identity does not match')
  delete env.npm_package_json
  expect(child().stderr).toContain('identity is missing')
})

test('required Windows verification refuses absent, foreign and unloadable binding inputs while optional targets remain optional', () => {
  const { source } = fixture()
  expect(() => verifyDesktopWindowsBinding(source, { platform: 'linux', arch: 'x64' })).not.toThrow()
  expect(() => verifyDesktopWindowsBinding(source, { platform: 'win32', arch: 'arm64' })).not.toThrow()
  expect(() => verifyDesktopWindowsBinding(source, { platform: 'win32', arch: 'x64' })).toThrow()
  const supplier = join(source, 'node_modules/get-windows')
  cpSync(join(source, 'vendor/get-windows'), supplier, { recursive: true })
  cpSync(join(source, 'vendor/node-gyp'), join(supplier, 'node_modules/node-gyp'), { recursive: true })
  json(join(supplier, 'node_modules/@mapbox/node-pre-gyp/package.json'), {
    name: '@mapbox/node-pre-gyp',
    version: '2.0.3',
    main: 'index.cjs'
  })
  const binding = join(supplier, 'binding.node')
  writeFileSync(
    join(supplier, 'node_modules/@mapbox/node-pre-gyp/index.cjs'),
    `exports.find=()=>${JSON.stringify(binding)}`
  )
  const options = {
    platform: 'win32',
    arch: 'x64',
    env: { NODE_OPTIONS: '--no-warnings' },
    spawn: () => ({ status: 1, stderr: 'invalid native module' })
  }
  writeFileSync(binding, 'ELF')
  expect(() => verifyDesktopWindowsBinding(source, options)).toThrow(/not a Windows PE/)
  writeFileSync(binding, 'MZ')
  expect(() => verifyDesktopWindowsBinding(source, options)).toThrow(/could not load/)
  let passedEnvironment
  verifyDesktopWindowsBinding(source, {
    ...options,
    spawn: (_node, _args, child) => {
      passedEnvironment = child.env
      return { status: 0 }
    }
  })
  expect(passedEnvironment).toEqual(options.env)
})

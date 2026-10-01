import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { createServer } from 'node:net'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

import { test } from 'vitest'

import { getWindowsCompilerEnvironment } from './stage-native-deps.mjs'

const requireFromDesktop = createRequire(import.meta.url)

function resolveGetWindowsEntry() {
  try {
    return requireFromDesktop.resolve('get-windows')
  } catch {
    return null
  }
}

const getWindowsEntry = resolveGetWindowsEntry()

test.skipIf(!getWindowsEntry && process.env.HERMES_REQUIRE_GET_WINDOWS !== '1')(
  'node-pre-gyp 2 resolves get-windows NAPI-9 metadata and preserves its missing-binding fallback',
  async () => {
    assert.ok(getWindowsEntry, 'the required supplier qualification must install get-windows')
    const getWindowsRoot = path.dirname(getWindowsEntry)
    const getWindowsPackagePath = path.join(getWindowsRoot, 'package.json')
    const getWindowsManifest = JSON.parse(fs.readFileSync(getWindowsPackagePath, 'utf8'))
    const requireFromGetWindows = createRequire(getWindowsEntry)
    const preGypEntry = requireFromGetWindows.resolve('@mapbox/node-pre-gyp')
    const preGypRoot = path.dirname(path.dirname(preGypEntry))
    const preGypManifest = JSON.parse(fs.readFileSync(path.join(preGypRoot, 'package.json'), 'utf8'))

    assert.equal(preGypManifest.version, '2.0.3', 'exercise the exact supplier selected for get-windows')
    const compilerEnv = getWindowsCompilerEnvironment(getWindowsRoot, { npm_config_node_gyp: '/untrusted/compiler' })
    const preGypRequire = createRequire(preGypEntry)
    assert.equal(preGypRequire.resolve('node-gyp/bin/node-gyp.js'), compilerEnv.npm_config_node_gyp)
    assert.equal(requireFromGetWindows.resolve('node-gyp/bin/node-gyp.js'), compilerEnv.npm_config_node_gyp)
    const compilerOutput = execFileSync(
      process.execPath,
      [
        '-e',
        'require(process.argv[1]).run_gyp(["--version"], {}, error => { if (error) throw error })',
        path.join(preGypRoot, 'lib/util/compile.js')
      ],
      { cwd: getWindowsRoot, env: compilerEnv, encoding: 'utf8', timeout: 15_000 }
    )
    assert.match(compilerOutput, /v13\.0\.2/)

    assert.equal(getWindowsManifest.version, '9.3.0')
    assert.deepEqual(getWindowsManifest.binary.napi_versions, [9])

    const preGyp = requireFromGetWindows('@mapbox/node-pre-gyp')
    const target = { target_platform: 'win32', target_arch: 'x64', target_libc: 'unknown' }
    const foundPath = preGyp.find(getWindowsPackagePath, { ...target })
    const revealText = execFileSync(
      process.execPath,
      [
        path.join(preGypRoot, 'bin', 'node-pre-gyp'),
        '--loglevel=silent',
        'reveal',
        '--target_platform=win32',
        '--target_arch=x64',
        '--target_libc=unknown'
      ],
      { cwd: getWindowsRoot, encoding: 'utf8' }
    )
    const revealed = JSON.parse(revealText)

    assert.equal(revealed.napi_build_version, 9)
    assert.equal(revealed.node_napi_label, 'napi-v9')
    assert.equal(revealed.target_platform, target.target_platform)
    assert.equal(revealed.target_arch, target.target_arch)
    assert.equal(revealed.libc, target.target_libc)
    assert.equal(
      path.normalize(revealed.module),
      path.normalize(foundPath),
      'runtime find and CLI reveal must agree on the prebuilt path'
    )
    assert.match(revealed.hosted_tarball, /\/v9\.3\.0\/napi-9-win32-unknown-x64\.tar\.gz$/)

    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-node-pre-gyp-'))
    try {
      // Import the published get-windows resolver from an isolated package copy
      // with the real supplier linked in, but no native binding installed.
      const fixtureRoot = path.join(tmp, 'get-windows')
      const fixtureLib = path.join(fixtureRoot, 'lib')
      const fixturePreGyp = path.join(fixtureRoot, 'node_modules', '@mapbox', 'node-pre-gyp')
      fs.mkdirSync(fixtureLib, { recursive: true })
      fs.mkdirSync(path.dirname(fixturePreGyp), { recursive: true })
      fs.copyFileSync(getWindowsPackagePath, path.join(fixtureRoot, 'package.json'))
      fs.copyFileSync(path.join(getWindowsRoot, 'lib', 'windows.js'), path.join(fixtureLib, 'windows.js'))
      fs.symlinkSync(preGypRoot, fixturePreGyp, process.platform === 'win32' ? 'junction' : 'dir')

      const fixtureManifest = path.join(fixtureRoot, 'package.json')
      const runtimePath = preGyp.find(fixtureManifest)
      assert.equal(fs.existsSync(runtimePath), false, 'fixture must exercise the absent-binding branch')

      const windows = await import(`${pathToFileURL(path.join(fixtureLib, 'windows.js')).href}?fixture=${Date.now()}`)
      assert.equal(windows.activeWindowSync(), undefined)
      assert.equal(windows.openWindowsSync(), undefined)

      const malformed = path.join(tmp, 'malformed-package.json')
      fs.writeFileSync(
        malformed,
        JSON.stringify({
          name: 'malformed-addon',
          version: '1.0.0',
          main: 'index.js',
          binary: { module_name: 'addon' }
        })
      )
      assert.throws(() => preGyp.find(malformed), /not node-pre-gyp ready/)
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true })
    }
  }
)

// This opt-in leg mutates only the disposable hosted job's installed package.
// Normal workspace checks exercise metadata without downloading/building addons.
test.runIf(process.platform === 'win32' && process.env.HERMES_VERIFY_GET_WINDOWS_NATIVE === '1')(
  'the actual Windows supplier installs and loads its prebuilt and source-build fallback',
  async () => {
    assert.ok(getWindowsEntry, 'Windows native qualification requires get-windows')
    const packageRoot = path.dirname(getWindowsEntry)
    const manifestPath = path.join(packageRoot, 'package.json')
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
    const supplierRequire = createRequire(getWindowsEntry)
    const supplier = supplierRequire('@mapbox/node-pre-gyp')
    const cli = supplierRequire.resolve('@mapbox/node-pre-gyp/bin/node-pre-gyp')
    const binding = supplier.find(manifestPath)
    const runCli = (args, env = process.env) => {
      const result = spawnSync(process.execPath, [cli, ...args], {
        cwd: packageRoot,
        encoding: 'utf8',
        env,
        timeout: 300_000,
        maxBuffer: 4 * 1024 * 1024
      })
      assert.ifError(result.error)
      const output = `${result.stdout}\n${result.stderr}`
      assert.equal(result.status, 0, output)
      return output
    }
    // Load in a child that exits before clean() so Windows never retains a DLL lock.
    const assertLoads = () => {
      assert.equal(fs.readFileSync(binding).subarray(0, 2).toString(), 'MZ')
      execFileSync(
        process.execPath,
        [
          '-e',
          "const a=require('node:assert/strict');const b=require(process.argv[1]);a.equal(typeof b.getActiveWindow,'function');a.equal(typeof b.getOpenWindows,'function')",
          binding
        ],
        { timeout: 15_000 }
      )
    }

    runCli(['clean'])
    assert.equal(fs.existsSync(binding), false)
    runCli(['install', '--fallback-to-build=false'])
    assertLoads()

    runCli(['clean'])
    assert.equal(fs.existsSync(binding), false, 'fallback must produce a new binary')
    // Reserve then close a loopback port to make the prebuilt fetch fail promptly.
    // Its automatic fallback must use the real Windows compiler/source inputs;
    // this does not contact a deliberately broken external URL or alter TLS checks.
    const unavailable = createServer()
    await new Promise((resolve, reject) => {
      unavailable.once('error', reject)
      unavailable.listen(0, '127.0.0.1', resolve)
    })
    const port = unavailable.address().port
    await new Promise((resolve, reject) => unavailable.close(error => (error ? reject(error) : resolve())))
    const mirrorKey = `npm_config_${manifest.binary.module_name.replace('-', '_')}_binary_host_mirror`
    const env = getWindowsCompilerEnvironment(packageRoot, {
      ...process.env,
      [mirrorKey]: `https://127.0.0.1:${port}/`
    })
    const output = runCli(['install', '--fallback-to-build'], env)
    assert.match(output, /falling back to source compile/)
    assert.match(output, /using node-gyp@13\.0\.2/)
    assertLoads()
  },
  660_000
)

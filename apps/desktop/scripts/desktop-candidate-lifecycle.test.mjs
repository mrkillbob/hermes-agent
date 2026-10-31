import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

import { Arch, Platform, build } from 'electron-builder'
import { test } from 'vitest'

import { candidateFromEnvironment, createCandidateAfterSign } from './desktop-candidate.mjs'
import { candidateDigest } from './desktop-candidate-policy.mjs'
import { publishPackagingInputs, treeDigest } from './prepared-packaging.mjs'
import { isMachO } from './sign-nested-chromium.mjs'

const source = path.resolve(import.meta.dirname, '../../..')
const require = createRequire(import.meta.url)
const builderRequire = createRequire(require.resolve('app-builder-lib'))

test.runIf(process.platform === 'darwin' && process.arch === 'arm64')(
  'pinned public builder reaches the candidate once after real fuse mutation without identity discovery',
  async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'candidate-lifecycle-'))
    try {
      const projectDir = path.join(root, 'app')
      fs.mkdirSync(projectDir)
      fs.writeFileSync(path.join(projectDir, 'package.json'), JSON.stringify({
        name: 'candidate-lifecycle-fixture', version: '0.0.0', main: 'index.js',
        description: 'Packaging-only fixture; never launched', author: 'Fixture'
      }))
      fs.writeFileSync(path.join(projectDir, 'index.js'), 'throw new Error("fixture must never be launched")\n')
      const electronVersion = JSON.parse(fs.readFileSync(path.join(source, 'apps/desktop/package.json'), 'utf8')).devDependencies.electron
      const originalElectron = path.join(path.dirname(require.resolve('electron/package.json')), 'dist')
      assert.ok(fs.existsSync(path.join(originalElectron, 'Electron.app')), 'prepare the locked Electron runtime first')
      const originalDigest = treeDigest(originalElectron)
      const electronDist = path.join(root, 'electron-inputs')
      fs.cpSync(originalElectron, electronDist, { recursive: true, verbatimSymlinks: true })
      const inputDigest = treeDigest(electronDist)
      const packaging = path.join(root, 'packaging')
      fs.mkdirSync(packaging)
      const archive = path.join(packaging, 'electron.zip')
      fs.writeFileSync(archive, 'owned supplier receipt fixture; build consumes the exact copied dist')
      const toolsets = { sevenZip: path.join(packaging, 'sevenZip'), icons: path.join(packaging, 'icons') }
      for (const directory of Object.values(toolsets)) fs.mkdirSync(directory)
      const manifest = await publishPackagingInputs({
        source, out: packaging, target: 'darwin-arm64', formats: ['dir'], electron: archive, toolsets
      })
      const commit = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: source, encoding: 'utf8' }).trim()
      const request = {
        source: fs.realpathSync(source), work: root, cache: path.join(root, 'cache'),
        commit, tag: null, version: '0.0.0', variant: 'bundled', target: 'darwin-arm64',
        bundle_env: {}, channel_request: null, release_epoch: null, development_candidate: true
      }
      const inputs = JSON.parse(fs.readFileSync(manifest, 'utf8'))
      inputs.desktopCandidate = { request, requestDigest: candidateDigest(request) }
      fs.writeFileSync(manifest, JSON.stringify(inputs))
      const env = { ...process.env, HERMES_PREPARED_PACKAGING: manifest,
        HERMES_BUILD_COMMIT: commit, HERMES_DESKTOP_VARIANT: 'bundled' }
      assert.deepEqual(candidateFromEnvironment(source, env), request)
      const declared = execFileSync(process.execPath, ['-e',
        'const c=require("./apps/desktop/electron-builder.config.cjs");console.log(JSON.stringify({sign:c.mac.sign,afterSign:typeof c.afterSign,publish:c.publish,macPublish:c.mac.publish}))'],
      { cwd: source, env, encoding: 'utf8' })
      assert.deepEqual(JSON.parse(declared.trim().split('\n').at(-1)),
        { sign: null, afterSign: 'function', publish: null, macPublish: null })
      const { getCurrentFuseWire, FuseV1Options, FuseState } = await import(
        pathToFileURL(builderRequire.resolve('@electron/fuses')).href)
      const policy = {
        entitlements: path.join(source, 'apps/desktop/electron/entitlements.mac.plist'),
        entitlementsInherit: path.join(source, 'apps/desktop/electron/entitlements.mac.inherit.plist'),
        hardenedRuntime: true,
        ignore: file => fs.statSync(file).isFile() && !isMachO(file)
      }
      let skippedSigningCalls = 0
      let candidateCalls = 0
      const events = []
      const afterSign = createCandidateAfterSign(policy, {
        source, env,
        sign: async opts => {
          candidateCalls++
          assert.equal(opts.identity, '-')
          assert.equal(opts.keychain, undefined)
          assert.equal(opts.provisioningProfile, undefined)
          assert.equal(opts.preEmbedProvisioningProfile, false)
          assert.equal(opts.preAutoEntitlements, false)
          const wire = await getCurrentFuseWire(opts.app)
          assert.equal(wire[FuseV1Options.RunAsNode], FuseState.DISABLE)
          const options = await opts.optionsForFile(opts.app)
          assert.equal(options.timestamp, 'none')
          assert.equal(options.hardenedRuntime, true)
          assert.equal(options.entitlements, policy.entitlements)
          events.push('candidate signer')
        }
      })
      await build({
        projectDir, targets: Platform.MAC.createTarget(['dir'], Arch.arm64), publish: 'never',
        config: {
          electronVersion, electronDist, appId: 'test.hermes.candidate-lifecycle',
          productName: 'Candidate Lifecycle Fixture', asar: false,
          directories: { output: path.join(root, 'output') },
          files: ['package.json', 'index.js'], electronFuses: { runAsNode: false },
          // The maintained API uses false to refuse dependency installation/rebuilding.
          beforeBuild: async () => false,
          mac: { sign: null, notarize: false },
          afterPack: async context => {
            const app = path.join(context.appOutDir, context.packager.appInfo.productFilename + '.app')
            const wire = await getCurrentFuseWire(app)
            assert.equal(wire[FuseV1Options.RunAsNode], FuseState.ENABLE)
            events.push('afterPack')
            const normalSign = context.packager.sign.bind(context.packager)
            context.packager.sign = async (...args) => {
              assert.equal(context.packager.getPlatformConfig(args[3]).config.sign, null)
              const signed = await normalSign(...args)
              assert.equal(signed, false)
              skippedSigningCalls++
              events.push('ordinary signing skipped')
              return signed
            }
            Object.defineProperty(context.packager, 'codeSigningInfo', {
              configurable: true, get: () => { assert.fail('candidate must not resolve a keychain after afterPack') }
            })
          },
          afterSign
        }
      })
      assert.deepEqual(events, ['afterPack', 'ordinary signing skipped', 'candidate signer'])
      assert.equal(skippedSigningCalls, 1)
      assert.equal(candidateCalls, 1)
      assert.equal(treeDigest(electronDist), inputDigest)
      assert.equal(treeDigest(originalElectron), originalDigest)
      // This fixture's signer is inert. Never export it as a usable candidate.
    } finally {
      fs.rmSync(root, { recursive: true, force: true })
    }
  }, 120000
)

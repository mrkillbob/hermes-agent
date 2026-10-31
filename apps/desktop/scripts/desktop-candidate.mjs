// Candidate signing consumes only the existing owned/fingerprinted packaging receipt.
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { createMacSigner } from './mac-sign.mjs'
import { readPackagingInputs } from './prepared-packaging.mjs'
import { validateCandidateReceipt } from './desktop-candidate-policy.mjs'

/**
 * Read-only admission: assets are deliberately overlaid during packaging.
 * @param {string} source
 * @param {NodeJS.ProcessEnv} [env]
 * @returns {import('./desktop-candidate-policy.mjs').CandidateRequest | null}
 */
export function candidateFromEnvironment(source, env = process.env) {
  const manifest = env.HERMES_PREPARED_PACKAGING
  if (!manifest) return null
  const inputs = JSON.parse(fs.readFileSync(manifest, 'utf8'))
  if (inputs.desktopCandidate == null) return null
  source = fs.realpathSync(source)
  const target = process.platform + '-' + process.arch
  if (inputs.source !== source || inputs.target !== target) {
    throw new Error('development candidate packaging source or native target changed')
  }
  const commit = execFileSync('git', ['-C', source, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()
  if (env.HERMES_BUILD_COMMIT !== commit || env.HERMES_DESKTOP_VARIANT !== 'bundled' ||
      env.HERMES_PAYLOAD_TAG || env._HERMES_CHANNEL_REQUEST_JSON) {
    throw new Error('development candidate build identity does not match the prepared commit selection')
  }
  const admitted = readPackagingInputs(manifest, source, target, env)
  return validateCandidateReceipt(admitted.desktopCandidate, { source, commit, target }, env)
}

/**
 * alpha.6 emits afterSign even when mac.sign=null: all framework/fuse mutations
 * have completed, and normal certificate signer has returned before keychain
 * resolution. The pinned lifecycle test and ZIP gate must fail if this changes.
 * @param {Parameters<typeof createMacSigner>[0]} policy
 * @param {{ source: string, env?: NodeJS.ProcessEnv, sign?: typeof import('@electron/osx-sign').sign }} options
 * @returns {(context: import('app-builder-lib').AfterPackContext) => Promise<void>}
 */
export function createCandidateAfterSign(policy, { source, env = process.env, sign }) {
  return async context => {
    const { Arch } = await import('app-builder-lib')
    if (!source || !candidateFromEnvironment(source, env) ||
        context.electronPlatformName !== 'darwin' || context.arch !== Arch.arm64 ||
        context.packager.config.mac?.sign !== null) {
      throw new Error('development candidate final signing requires its admitted native packaging context')
    }
    const packager = /** @type {import('app-builder-lib').MacPackager} */ (context.packager)
    const app = path.join(context.appOutDir, packager.appInfo.productFilename + '.app')
    /** @type {Parameters<typeof createMacSigner>[0]} */
    const candidatePolicy = { ...policy, timestamp: 'none' }
    const opts = await packager.helper.buildSignOptions(
      app, { name: '-', hash: undefined }, candidatePolicy, undefined, Arch.arm64, 'mac')
    const signer = createMacSigner(candidatePolicy, { env, sign })
    await signer({
      ...opts, identity: '-', identityValidation: false, keychain: undefined,
      provisioningProfile: undefined, preEmbedProvisioningProfile: false, preAutoEntitlements: false
    }, packager)
  }
}

import assert from 'node:assert/strict'
import { test } from 'vitest'
import { createMacSigner } from './mac-sign.mjs'

const policy = {
  entitlements: '/fixture/app.plist',
  entitlementsInherit: '/fixture/child.plist',
  hardenedRuntime: true,
  ignore: () => false,
  timestamp: 'none'
}

const packager = {
  helper: {
    getOptionsForFile: async (_app, _target, selected) => () => ({
      entitlements: selected.entitlements,
      hardenedRuntime: selected.hardenedRuntime,
      timestamp: selected.timestamp
    })
  }
}

test.each([
  { identity: 'Developer ID Application: Fixture' },
  { identity: '-', keychain: '/fixture/login.keychain' },
  { identity: '-', cache: '/fixture/signatures' }
])('timestamp-none candidate policy refuses ambient signing authority %j', async ({ cache, ...opts }) => {
  let called = false
  let authorityCalls = 0
  const signer = createMacSigner(policy, {
    env: cache ? { HERMES_MAC_SIGNATURE_CACHE: cache } : {},
    sign: async () => { called = true },
    team: async () => { authorityCalls++; return undefined },
    makeCache: () => { authorityCalls++; return null }
  })
  await assert.rejects(
    signer({ app: '/nonexistent/Hermes.app', platform: 'darwin', ...opts }, packager),
    /development candidate/
  )
  assert.equal(called, false)
  assert.equal(authorityCalls, 0)
})

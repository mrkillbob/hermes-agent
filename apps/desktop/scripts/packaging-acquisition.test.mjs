import assert from 'node:assert/strict'
import { test } from 'vitest'
import * as acquisition from './packaging-acquisition.mjs'

test('a packaging failure identifies the supplier and network cause without emitting URL credentials', async () => {
  const cause = Object.assign(new Error('request https://user:password@example.com/secret?token=private'), {
    code: 'ECONNRESET', statusCode: 503, url: 'https://user:password@example.com/secret?token=private',
  })
  const failure = new TypeError('fetch failed', { cause })
  assert.equal(typeof acquisition.acquirePackagingInput, 'function')
  await assert.rejects(acquisition.acquirePackagingInput('Electron archive', async () => { throw failure }), error => {
    assert.match(error.message, /Electron archive/)
    assert.match(error.message, /ECONNRESET/)
    assert.match(error.message, /status=503/)
    assert.match(error.message, /host=example.com/)
    assert.doesNotMatch(error.message, /private|password|secret|user:|https:/)
    assert.equal(error.cause, undefined)
    return true
  })
})

test('successful suppliers preserve their result without retries', async () => {
  let calls = 0
  const result = await acquisition.acquirePackagingInput('icon tools', async () => { calls++; return '/verified/tool' })
  assert.equal(result, '/verified/tool')
  assert.equal(calls, 1)
})

test('unknown failures stay fatal and untrusted cause fields never become log text', async () => {
  const failure = Object.assign(new Error('token=private'), { code: 'token=private', status: 'secret', url: 'malformed private' })
  let calls = 0
  await assert.rejects(acquisition.acquirePackagingInput('7zip tools', async () => { calls++; throw failure }), error => {
    assert.equal(error.message, 'Failed preparing 7zip tools (Error)')
    return true
  })
  assert.equal(calls, 1)
})

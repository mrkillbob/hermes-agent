import assert from 'node:assert/strict'
import { test, vi } from 'vitest'
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

test.each([
  { code: 'ERR_CHECKSUM_MISMATCH' },
  { statusCode: 401 },
  { statusCode: 403 },
])('integrity and authorization failures remain fatal: %j', async fields => {
  let calls = 0
  await assert.rejects(acquisition.acquirePackagingInput('7zip tools', async () => {
    calls++
    throw Object.assign(new Error('private supplier diagnostic'), fields)
  }), /Failed preparing 7zip tools/)
  assert.equal(calls, 1)
})

test('a transient connection timeout can recover without restarting packaging preparation', async () => {
  vi.useFakeTimers()
  try {
    let calls = 0
    const pending = acquisition.acquirePackagingInput('7zip tools', async () => {
      calls++
      if (calls === 1) {
        throw new TypeError('fetch failed', {
          cause: Object.assign(new Error('private supplier URL'), { code: 'UND_ERR_CONNECT_TIMEOUT' }),
        })
      }
      return '/verified/7za'
    })
    const result = pending.then(value => ({ value }), error => ({ error }))
    await vi.runAllTimersAsync()
    assert.deepEqual(await result, { value: '/verified/7za' })
    assert.equal(calls, 2)
  } finally {
    vi.useRealTimers()
  }
})

test('repeated connection timeouts exhaust a bounded retry budget and keep diagnostics sanitized', async () => {
  vi.useFakeTimers()
  try {
    let calls = 0
    const pending = acquisition.acquirePackagingInput('7zip tools', async () => {
      calls++
      throw new TypeError('private URL', {
        cause: Object.assign(new Error('token=secret'), { code: 'UND_ERR_CONNECT_TIMEOUT' }),
      })
    })
    const result = assert.rejects(pending, error => {
      assert.equal(error.message, 'Failed preparing 7zip tools (TypeError; Error; code=UND_ERR_CONNECT_TIMEOUT)')
      assert.equal(error.cause, undefined)
      return true
    })
    await vi.runAllTimersAsync()
    await result
    assert.equal(calls, 3)
  } finally {
    vi.useRealTimers()
  }
})

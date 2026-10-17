import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { EventEmitter } from 'node:events'

import { afterEach, test, vi } from 'vitest'

import { createBackendConnectionState } from './backend-connection-state'
import { execProbe } from './backend-probes'

vi.mock('node:child_process', () => ({ spawn: vi.fn() }))

afterEach(() => vi.resetAllMocks())

for (const cancellation of ['shutdown', 'reset'] as const) {
  test(`a timed-out probe cannot spawn its internal retry after ${cancellation}`, async () => {
    const controller = new AbortController()
    const state = createBackendConnectionState<unknown, unknown>()
    const attempt = state.startAttempt()
    vi.mocked(spawn).mockImplementation(() => {
      const child = Object.assign(new EventEmitter(), {
        killed: true,
        exitCode: null,
        signalCode: null,
        kill: vi.fn()
      })

      queueMicrotask(() => {
        if (cancellation === 'shutdown') {
          controller.abort(new Error('quitting'))
        } else {
          state.invalidate()
        }

        child.emit('close', null, 'SIGTERM')
      })

      return child as unknown as ReturnType<typeof spawn>
    })

    await assert.rejects(
      execProbe('inert-version-fixture', ['--version'], {
        stdio: 'ignore',
        timeout: 15_000,
        signal: controller.signal,
        assertCurrentAttempt: () => state.assertCurrentAttempt(attempt)
      }),
      cancellation === 'shutdown' ? /quitting/ : /superseded/
    )
    assert.equal(vi.mocked(spawn).mock.calls.length, 1)
  })
}

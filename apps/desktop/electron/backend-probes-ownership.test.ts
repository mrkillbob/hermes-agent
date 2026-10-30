import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { EventEmitter } from 'node:events'

import { afterEach, test, vi } from 'vitest'

import { createBackendConnectionState } from './backend-connection-state'
import { execProbe } from './backend-probes'

vi.mock('node:child_process', () => ({ spawn: vi.fn() }))

afterEach(() => {
  vi.useRealTimers()
  vi.resetAllMocks()
})

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

test('shutdown terminates and reaps an owned hung probe within the teardown budget', async () => {
  vi.useFakeTimers()
  const controller = new AbortController()

  const child = Object.assign(new EventEmitter(), {
    killed: false,
    exitCode: null,
    signalCode: null as string | null,
    kill: vi.fn((signal: string) => {
      child.killed = true

      if (signal === 'SIGKILL') {
        child.signalCode = signal
        child.emit('close', null, signal)
      }

      return true
    })
  })

  vi.mocked(spawn).mockReturnValue(child as unknown as ReturnType<typeof spawn>)
  let settled = false

  const pending = execProbe('inert-hung-version', ['--version'], {
    stdio: 'ignore',
    timeout: 15_000,
    signal: controller.signal
  }).then(
    () => {
      settled = true

      return null
    },
    error => {
      settled = true

      return error
    }
  )

  controller.abort(new Error('quitting'))
  assert.deepEqual(child.kill.mock.calls, [['SIGTERM']])
  assert.equal(settled, false, 'wait for the owned child to close before settling')
  await vi.advanceTimersByTimeAsync(1_000)
  assert.deepEqual(child.kill.mock.calls, [['SIGTERM'], ['SIGKILL']])
  assert.match(String(await pending), /quitting/)
  assert.equal(vi.mocked(spawn).mock.calls.length, 1)
  assert.equal(vi.getTimerCount(), 0)
})

test('a completed probe removes its abort listener and is never signalled later', async () => {
  const controller = new AbortController()
  const remove = vi.spyOn(controller.signal, 'removeEventListener')

  const child = Object.assign(new EventEmitter(), {
    killed: false,
    exitCode: 0,
    signalCode: null,
    kill: vi.fn()
  })

  vi.mocked(spawn).mockReturnValue(child as unknown as ReturnType<typeof spawn>)

  const pending = execProbe('inert-completed-version', ['--version'], {
    stdio: 'ignore',
    timeout: 15_000,
    signal: controller.signal
  })

  child.emit('close', 0, null)
  await pending
  controller.abort(new Error('later shutdown'))
  assert.equal(child.kill.mock.calls.length, 0)
  assert.ok(remove.mock.calls.some(([event]) => event === 'abort'))
})

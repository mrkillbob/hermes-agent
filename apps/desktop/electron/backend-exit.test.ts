import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'

import { test, vi } from 'vitest'

import { waitForBackendExit } from './backend-child'

test('backend exit escalation rejects within its bound when no exit or close arrives', async (): Promise<void> => {
  vi.useFakeTimers()

  try {
    const child = Object.assign(new EventEmitter(), { exitCode: null, signalCode: null, kill: vi.fn() })

    const waiting = assert.rejects(
      waitForBackendExit(child, { forceKillProcessTree: (): void => {} }, 20),
      /did not exit/
    )

    await Promise.all([waiting, vi.advanceTimersByTimeAsync(1020)])
    assert.equal(child.exitCode, null)
    assert.equal(child.signalCode, null)
    assert.equal(child.kill.mock.calls.length, 1)
    assert.equal(child.kill.mock.calls[0][0], 'SIGKILL')
    assert.equal(child.listenerCount('exit'), 0)
    assert.equal(vi.getTimerCount(), 0)
  } finally {
    vi.useRealTimers()
  }
})

test('a failed escalation is not evidence that the child exited', async () => {
  vi.useFakeTimers()

  try {
    const child = Object.assign(new EventEmitter(), {
      exitCode: null,
      signalCode: null,
      kill: () => {
        throw new Error('signal denied')
      }
    })

    const outcome = waitForBackendExit(child, { forceKillProcessTree: () => {} }, 20).then(
      () => null,
      error => error
    )

    await vi.advanceTimersByTimeAsync(1020)
    assert.match(String(await outcome), /did not exit/)
    assert.equal(child.listenerCount('exit'), 0)
    assert.equal(vi.getTimerCount(), 0)
  } finally {
    vi.useRealTimers()
  }
})

test('a dead launcher does not hide a live backend process group', async (): Promise<void> => {
  vi.useFakeTimers()

  try {
    const child = Object.assign(new EventEmitter(), {
      pid: 4242,
      exitCode: 0,
      signalCode: null,
      kill: vi.fn()
    })
    let groupAlive = true
    const groupSignals: Array<[number, NodeJS.Signals]> = []

    const waiting = waitForBackendExit(
      child,
      {
        forceKillProcessTree: (): void => {},
        isWindows: false,
        isProcessGroupAlive: (): boolean => groupAlive,
        killGroup: (pgid, signal) => {
          groupSignals.push([pgid, signal])
          groupAlive = false
        }
      },
      20
    )

    await Promise.all([waiting, vi.advanceTimersByTimeAsync(20)])
    assert.deepEqual(groupSignals, [[-4242, 'SIGKILL']])
    assert.equal(child.kill.mock.calls.length, 0)
    assert.equal(vi.getTimerCount(), 0)
  } finally {
    vi.useRealTimers()
  }
})

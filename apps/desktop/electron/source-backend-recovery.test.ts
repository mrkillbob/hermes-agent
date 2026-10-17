import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { afterEach, test, vi } from 'vitest'

import { createBackendConnectionState } from './backend-connection-state'
import { execProbe } from './backend-probes'
import { createInstalledRuntimeGate } from './backend-resolution'
import { createFirstRunSetupGate } from './first-run-setup-gate'
import { runPrimaryBackendStartup } from './primary-backend-startup'
import { resolveSourceInstallationBackend } from './source-backend'

vi.mock('./backend-probes', () => ({ execProbe: vi.fn(), PROBE_TIMEOUT_MS: 15_000 }))

const homes: string[] = []

afterEach(() => {
  vi.useRealTimers()
  vi.resetAllMocks()

  for (const home of homes.splice(0)) {
    fs.rmSync(home, { recursive: true, force: true })
  }
})

function installation() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'desktop-probe-recovery-'))
  homes.push(root)
  fs.mkdirSync(path.join(root, 'hermes_cli'))
  fs.writeFileSync(path.join(root, 'hermes_cli', 'main.py'), '')
  // Only resolver validation runs: execProbe is inert, and no launcher executes.
  fs.mkdirSync(path.join(root, '.hermes', 'bin'), { recursive: true })
  fs.writeFileSync(path.join(root, '.hermes', 'bin', 'hermes'), '', { mode: 0o755 })

  return root
}

function startup(root: string, signal?: AbortSignal) {
  const state = createBackendConnectionState<unknown, unknown>()
  const attempt = state.startAttempt()
  const assertCurrentAttempt = () => state.assertCurrentAttempt(attempt)
  const gate = createFirstRunSetupGate({ stuckAfterMs: 0 })
  const logs: string[] = []
  const ensureLocalRuntime = vi.fn(async backend => backend)
  const waitForDecision = vi.fn(gate.wait)

  const pending = runPrimaryBackendStartup({
    signal,
    assertCurrentAttempt,
    connectRemote: async () => {
      throw new Error('Unexpected remote connect')
    },
    ensureLocalRuntime,
    prepareLocalBackend: async () =>
      (await resolveSourceInstallationBackend(root, ['gateway', 'ensure', '--json'], {
        isWindows: false,
        hermesHome: root,
        signal,
        assertCurrentAttempt,
        log: message => logs.push(message)
      })) || { kind: 'bootstrap-needed' },
    resolveRemote: async () => null,
    waitForDecision,
    waitForLocalStart: async () => {}
  })

  return { gate, logs, ensureLocalRuntime, waitForDecision, pending, state }
}

test('healthy installed launcher is accepted immediately without a recovery delay', async () => {
  vi.mocked(execProbe).mockResolvedValue(undefined)
  const root = installation()

  const backend = await resolveSourceInstallationBackend(root, ['gateway', 'ensure', '--json'], {
    isWindows: false,
    hermesHome: root
  })

  assert.equal(backend?.command, path.join(root, '.hermes', 'bin', 'hermes'))
  assert.deepEqual(backend?.args, ['gateway', 'ensure', '--json'])
  assert.equal(backend?.bootstrap, false)
  assert.equal(vi.mocked(execProbe).mock.calls.length, 1)
})

test('a missing installation launcher remains unusable without invoking recovery', async () => {
  const root = installation()
  fs.unlinkSync(path.join(root, '.hermes', 'bin', 'hermes'))

  assert.equal(await resolveSourceInstallationBackend(root, [], { isWindows: false, hermesHome: root }), null)
  assert.equal(vi.mocked(execProbe).mock.calls.length, 0)
})

test('connection reset fences recovery without aborting the permanent shutdown signal', async () => {
  vi.useFakeTimers()
  const controller = new AbortController()
  vi.mocked(execProbe).mockRejectedValueOnce(new Error('failed')).mockResolvedValueOnce(undefined)
  const run = startup(installation(), controller.signal)
  const rejected = assert.rejects(run.pending, /superseded/)
  await vi.advanceTimersByTimeAsync(1)
  run.state.invalidate()
  await vi.runAllTimersAsync()
  await rejected

  assert.equal(controller.signal.aborted, false)
  assert.equal(vi.mocked(execProbe).mock.calls.length, 1)
  assert.equal(run.waitForDecision.mock.calls.length, 0)
  assert.equal(run.ensureLocalRuntime.mock.calls.length, 0)
})

test('transient installed probe failure recovers before first-run choice without confirming bootstrap', async () => {
  vi.useFakeTimers()
  vi.mocked(execProbe)
    .mockRejectedValueOnce(Object.assign(new Error('private command/token'), { code: 1 }))
    .mockResolvedValueOnce(undefined)
  const root = installation()
  const run = startup(root)
  await vi.runAllTimersAsync()
  assert.equal(run.gate.hasWaiter(), false, 'a recovered installed launcher must avoid the setup gate')
  const result = await run.pending

  assert.equal(result.kind, 'local')
  assert.equal(result.kind === 'local' && result.backend.local, 'installed')
  assert.equal(run.gate.hasWaiter(), false)
  assert.equal(run.gate.isLocalBootstrapConfirmed(), false)
  assert.deepEqual(
    vi.mocked(execProbe).mock.calls.map(call => call[1]),
    [['--version'], ['--version']]
  )
  assert.equal(run.ensureLocalRuntime.mock.calls.length, 1)
})

test('exhausted recovery stays at explicit first-run choice and logs only safe failure metadata', async () => {
  vi.useFakeTimers()
  vi.mocked(execProbe).mockRejectedValue(Object.assign(new Error('private command/token'), { killed: true }))
  const run = startup(installation())
  await vi.runAllTimersAsync()

  assert.equal(run.gate.hasWaiter(), true)
  assert.equal(run.ensureLocalRuntime.mock.calls.length, 0)
  assert.equal(vi.mocked(execProbe).mock.calls.length, 2)
  assert.ok(vi.mocked(execProbe).mock.calls[1][2].timeout <= 5_000)
  assert.match(run.logs.join('\n'), /timeout.*retry/i)
  assert.match(run.logs.join('\n'), /retry.*exhausted/i)
  assert.ok(!run.logs.join('\n').includes('private'))
  run.gate.resetForRetry()
  await assert.rejects(run.pending, /First-run setup was reset/)
})

test('cancelled startup never probes again or resumes a first-run choice', async () => {
  vi.useFakeTimers()
  const controller = new AbortController()
  vi.mocked(execProbe).mockRejectedValue(Object.assign(new Error('failed'), { code: 1 }))
  const run = startup(installation(), controller.signal)
  const rejected = assert.rejects(run.pending, /superseded/)
  await vi.advanceTimersByTimeAsync(1)
  controller.abort(new Error('superseded'))
  await vi.runAllTimersAsync()
  await rejected

  assert.equal(vi.mocked(execProbe).mock.calls.length, 1)
  assert.equal(run.waitForDecision.mock.calls.length, 0)
  assert.equal(run.ensureLocalRuntime.mock.calls.length, 0)
})

test('late successful recovery cannot advance a superseded startup', async () => {
  vi.useFakeTimers()
  const controller = new AbortController()

  let finish: () => void = () => {}
  vi.mocked(execProbe)
    .mockRejectedValueOnce(new Error('failed'))
    .mockImplementationOnce(
      () =>
        new Promise<void>(resolve => {
          finish = resolve
        })
    )
  const run = startup(installation(), controller.signal)
  const rejected = assert.rejects(run.pending, /superseded/)
  await vi.runAllTimersAsync()
  controller.abort(new Error('superseded'))
  finish()
  await rejected
  await vi.runAllTimersAsync()

  assert.equal(run.waitForDecision.mock.calls.length, 0)
  assert.equal(run.ensureLocalRuntime.mock.calls.length, 0)
})

for (const cancellation of ['connection reset', 'shutdown']) {
  test(`post-install re-resolution fences recovery after ${cancellation}`, async () => {
    vi.useFakeTimers()
    const root = installation()
    const controller = new AbortController()
    const state = createBackendConnectionState<unknown, unknown>()
    const attempt = state.startAttempt()
    const gate = createInstalledRuntimeGate({ HERMES_DESKTOP_IGNORE_EXISTING: '1' }, () => {})

    const control = {
      signal: controller.signal,
      assertCurrentAttempt: () => state.assertCurrentAttempt(attempt)
    }

    vi.mocked(execProbe).mockRejectedValueOnce(new Error('failed')).mockResolvedValueOnce(undefined)

    const pending = gate.afterInstall(
      probeControl =>
        gate.resolve(root, () =>
          resolveSourceInstallationBackend(root, [], {
            isWindows: false,
            hermesHome: root,
            ...probeControl
          })
        ),
      control
    )

    // Observe settlement immediately: the old path resolves instead of rejecting.
    const settled = pending.then(
      value => ({ value, error: null }),
      error => ({ value: null, error })
    )

    await vi.advanceTimersByTimeAsync(1)

    if (cancellation === 'connection reset') {
      state.invalidate()
    } else {
      controller.abort(new Error('shutdown'))
    }

    await vi.runAllTimersAsync()
    const result = await settled
    assert.ok(result.error, 'a superseded post-install resolution must reject')
    assert.equal(result.value, null)
    assert.equal(vi.mocked(execProbe).mock.calls.length, 1)
  })
}

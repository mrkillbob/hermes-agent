import { EventEmitter } from 'node:events'
import { expect, test, vi } from 'vitest'
import { observeElectronLaunch } from '../../tests/install/e2e-assets/electron-launch-diagnostics.mjs'

function fixture() {
  const child = Object.assign(new EventEmitter(), {
    pid: 42, exitCode: null, signalCode: null,
    stdout: new EventEmitter(), stderr: new EventEmitter(),
  })
  const app = Object.assign(new EventEmitter(), { process: () => child, windows: () => [] })
  const log = vi.fn()
  return { app, child, log }
}

test('retains early child output and distinguishes an exited app at probe failure', () => {
  const { app, child, log } = fixture()
  const snapshot = observeElectronLaunch(app, log)
  child.stdout.emit('data', Buffer.from('boot message\n'))
  child.stderr.emit('data', Buffer.from('singleton lock lost\n'))
  child.exitCode = 0
  child.emit('exit', 0, null)
  app.emit('close')
  expect(log.mock.calls.map(([message]) => message)).toEqual([
    '[electron stdout] boot message\n', '[electron stderr] singleton lock lost\n',
    '[electron exit] code=0 signal=null', '[electron close]',
  ])
  expect(snapshot()).toEqual({ pid: 42, exitCode: 0, signalCode: null, appClosed: true, windows: 0 })
})

test('reports a live inspector failure without closing, restarting or repairing the app', () => {
  const { app, child, log } = fixture()
  const snapshot = observeElectronLaunch(app, log)
  expect(snapshot()).toEqual({ pid: 42, exitCode: null, signalCode: null, appClosed: false, windows: 0 })
  expect(log).not.toHaveBeenCalled()
  expect(child.listenerCount('exit')).toBe(1)
})

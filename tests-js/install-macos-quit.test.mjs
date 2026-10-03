import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { test, vi } from 'vitest'

const helper = createRequire(import.meta.url)('../tests/install/e2e-assets/macos-app-quit.cjs')

function fixture(rows) {
  let time = 0
  const requests = []
  return { requests, options: {
    list: () => rows(time),
    terminate: app => { requests.push(app.pid); return true },
    now: () => time,
    delay: seconds => { time += seconds * 1000 },
  } }
}

test('closes exact installed instances and a late automatic successor before smoke owns the backend', () => {
  const installed = '/isolated/install/Hermes.app/Contents/MacOS/Hermes'
  const other = { pid: 90, executable: '/other/Hermes.app/Contents/MacOS/Hermes' }
  const first = { pid: 10, executable: installed }
  const successor = { pid: 20, executable: installed }
  const state = fixture(time => [other, ...(time < 400 ? [first] : time < 800 ? [] : time < 1200 ? [successor] : [])])
  assert.equal(typeof helper.quitInstalledApps, 'function')
  helper.quitInstalledApps(installed, state.options)
  assert.deepEqual(state.requests, [10, 20])
})

test('a refused normal quit fails without forcing termination', () => {
  const app = { pid: 10, executable: '/isolated/Hermes' }
  const state = fixture(() => [app])
  state.options.terminate = () => false
  assert.throws(() => helper.quitInstalledApps(app.executable, state.options), /normal Quit refused/)
})

test('a stuck installed app fails with process readiness evidence and a bounded wait', () => {
  const app = { pid: 10, executable: '/isolated/Hermes', finishedLaunching: false, active: true }
  const state = fixture(() => [app])
  assert.throws(() => helper.quitInstalledApps(app.executable, { ...state.options, timeoutMs: 600 }), /pid=10.*finishedLaunching=false/)
  assert.deepEqual(state.requests, [10])
})

test('the JXA adapter sends normal terminate to the exact binary and re-reads its native inventory', () => {
  const installed = '/isolated/Hermes.app/Contents/MacOS/Hermes'
  let time = 0
  let quits = 0
  const app = {
    processIdentifier: 10, executableURL: { path: installed }, terminated: false,
    finishedLaunching: true, active: false,
    get terminate() { quits++; return true },
  }
  const other = {
    processIdentifier: 90, executableURL: { path: '/unrelated/Hermes' }, terminated: false,
    finishedLaunching: true, active: false,
    get terminate() { throw new Error('unrelated app must remain untouched') },
  }
  vi.spyOn(Date, 'now').mockImplementation(() => time)
  vi.stubGlobal('delay', seconds => { time += seconds * 1000; if (time >= 400) app.terminated = true })
  vi.stubGlobal('ObjC', { import() {}, unwrap: value => value })
  vi.stubGlobal('$', { NSWorkspace: { sharedWorkspace: {
    get runningApplications() {
      const rows = [app, other]
      return { count: rows.length, objectAtIndex: i => rows[i] }
    },
  } } })
  try {
    helper.run([installed])
    assert.equal(quits, 1)
    assert.equal(app.terminated, true)
  } finally {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  }
})

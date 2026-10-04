import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { test } from 'vitest'

const { collectQuitEvidence } = createRequire(import.meta.url)('../tests/install/e2e-assets/macos-network-prompt.cjs')
const executable = '/isolated/Hermes.app/Contents/MacOS/Hermes'
const app = { pid: 200, executable, finishedLaunching: true, active: true }
const snapshot = {
  apps: [app, { pid: 999, executable: '/other/Hermes' }],
  windows: [{ pid: 300, id: 20, layer: 8, bounds: { X: 382, Y: 119, Width: 260, Height: 250 },
    title: 'credential-sentinel', env: 'credential-sentinel' }],
}
const processes = `100 1 /opt/hca/hosted-compute-agent\n200 100 ${executable}\n300 1 /System/Library/CoreServices/UserNotificationCenter.app/Contents/MacOS/UserNotificationCenter\n`

test('diagnostics sample only the revalidated installed app and retain no window content or command arguments', async () => {
  const commands = []
  const report = await collectQuitEvidence(executable, {
    execute: async (command, args, options) => {
      commands.push({ command, args })
      assert.ok(options.timeout > 0 && options.timeout <= 2000)
      if (command === '/usr/bin/osascript') return JSON.stringify(snapshot)
      if (command === '/bin/ps') return processes
      assert.equal(command, '/usr/bin/sample')
      assert.equal(args[0], '200')
      return 'Call graph:\n  Thread_1 DispatchQueue_1: com.apple.main-thread\n  + node::SyncProcessRunner::Spawn\n  Thread_2 worker\n  + CFUserNotification credential-sentinel\n'
    },
  })
  assert.equal(report.status, 'captured')
  assert.deepEqual(commands.map(call => call.command), ['/usr/bin/osascript', '/bin/ps', '/usr/bin/osascript', '/usr/bin/sample'])
  assert.equal(report.runnerAgent.pid, 100)
  assert.equal(report.visibleWindows[0].ownerExecutable, '/System/Library/CoreServices/UserNotificationCenter.app/Contents/MacOS/UserNotificationCenter')
  assert.deepEqual(report.stack.frameMarkers, ['node::SyncProcessRunner'])
  assert.equal(JSON.stringify(report).includes('credential-sentinel'), false)
})

test('changed identity, ambiguous targets, denied observation and budget expiry produce evidence without sampling or raw errors', async () => {
  for (const scenario of ['changed', 'ambiguous', 'fresh-ambiguous', 'denied', 'expired']) {
    const commands = []
    let observations = 0
    let time = 0
    const report = await collectQuitEvidence(executable, {
      now: () => time,
      execute: async (command) => {
        commands.push(command)
        if (scenario === 'denied') throw Object.assign(new Error('credential-sentinel'), { code: 'EACCES' })
        if (scenario === 'expired') time = 9000
        if (command === '/bin/ps') return processes
        assert.equal(command, '/usr/bin/osascript', 'a failed identity/deadline must never reach sample')
        observations++
        const apps = scenario === 'ambiguous' || scenario === 'fresh-ambiguous' && observations === 2 ? [app, { ...app, pid: 201 }] :
          scenario === 'changed' && observations === 2 ? [{ ...app, executable: '/other/Hermes' }] : [app]
        return JSON.stringify({ ...snapshot, apps })
      },
    })
    assert.equal(report.status, { changed: 'identity-changed', ambiguous: 'ambiguous-target', 'fresh-ambiguous': 'ambiguous-target', denied: 'observation-denied', expired: 'budget-expired' }[scenario])
    assert.equal(commands.includes('/usr/bin/sample'), false)
    assert.ok(commands.length <= 3)
    assert.equal(JSON.stringify(report).includes('credential-sentinel'), false)
  }
})

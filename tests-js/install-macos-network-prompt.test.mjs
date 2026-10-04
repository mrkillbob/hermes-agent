import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
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

test.skipIf(process.platform === 'win32')('selected and physical file identities allow only the same revalidated executable', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-quit-identity-'))
  try {
    const physical = path.join(root, 'physical')
    const suffix = 'Hermes.app/Contents/MacOS/Hermes'
    fs.mkdirSync(path.dirname(path.join(physical, suffix)), { recursive: true })
    fs.writeFileSync(path.join(physical, suffix), 'non-executable fixture', { mode: 0o600 })
    const alias = path.join(root, 'selected')
    fs.symlinkSync(physical, alias)
    const selected = path.join(alias, suffix)
    const canonical = fs.realpathSync(selected)
    const other = path.join(root, 'other', suffix)
    fs.mkdirSync(path.dirname(other), { recursive: true })
    fs.writeFileSync(other, 'non-executable fixture', { mode: 0o600 })
    for (const [appPath, psPath] of [[selected, canonical], [canonical, selected]]) {
      const report = await collectQuitEvidence(selected, { execute: async (command, args) => {
        if (command === '/usr/bin/osascript') return JSON.stringify({ apps: [{ ...app, executable: appPath }, { ...app, pid: 201, executable: other }], windows: [] })
        if (command === '/bin/ps') return `200 1 ${psPath}\n201 1 ${other}\n`
        assert.equal(command, '/usr/bin/sample'); assert.equal(args[0], '200')
        return 'Thread_1 com.apple.main-thread\n + CFRunLoopRun\n'
      } })
      assert.equal(report.status, 'captured')
      assert.equal(report.target.pid, 200)
      assert.deepEqual(report.executableIdentity, { selected, canonical })
      assert.deepEqual(report.installedProcessIds, [200])
      assert.ok(report.observedAt.initial <= report.observedAt.processTable)
      assert.ok(report.observedAt.processTable <= report.observedAt.revalidated)
    }
    if (process.platform === 'darwin') {
      const { resolveExecutablePath } = createRequire(import.meta.url)('../tests/install/e2e-assets/macos-app-quit.cjs')
      const script = `ObjC.import('Foundation');\n${resolveExecutablePath.toString()}\nfunction run(args) { return resolveExecutablePath(args[0]); }`
      const { stdout } = await promisify(execFile)('/usr/bin/osascript', ['-l', 'JavaScript', '-e', script, selected], {
        timeout: 3000, killSignal: 'SIGKILL', maxBuffer: 65536, env: { PATH: '/usr/bin:/bin' },
      })
      assert.equal(stdout.trim(), canonical)
    }
    let samples = 0
    const changed = await collectQuitEvidence(selected, { execute: async command => {
      if (command === '/bin/ps') {
        fs.unlinkSync(alias); fs.symlinkSync(path.join(root, 'other'), alias)
        return `200 1 ${canonical}\n`
      }
      if (command === '/usr/bin/osascript') return JSON.stringify({ apps: [{ ...app, executable: canonical }], windows: [] })
      samples++; throw new Error('changed file identity must not be sampled')
    } })
    assert.equal(changed.status, 'identity-changed')
    assert.equal(samples, 0)
  } finally { fs.rmSync(root, { recursive: true, force: true }) }
}, 5000)

test.skipIf(process.platform !== 'darwin')('native read-only observation reports an absent app without sampling it', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-observer-absent-'))
  const executable = path.join(root, 'Hermes.app/Contents/MacOS/Hermes')
  fs.mkdirSync(path.dirname(executable), { recursive: true })
  fs.writeFileSync(executable, 'non-executable fixture', { mode: 0o600 })
  let report
  try { report = await collectQuitEvidence(executable) }
  finally { fs.rmSync(root, { recursive: true, force: true }) }
  assert.equal(report.status, 'target-absent')
  assert.ok(Array.isArray(report.visibleWindows))
  assert.ok(report.visibleWindows.length <= 6)
  assert.equal(report.target, undefined)
  assert.equal(report.stack, undefined)
  assert.deepEqual(report.installedProcessIds, [])
}, 10000)

test('diagnostics sample only the revalidated installed app and retain no window content or command arguments', async () => {
  const commands = []
  const report = await collectQuitEvidence(executable, {
    resolveExecutable: value => value,
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
      resolveExecutable: value => value,
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

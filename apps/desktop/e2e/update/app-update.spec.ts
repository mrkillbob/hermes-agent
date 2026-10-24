/**
 * Failure class: APP-DRIVEN UPDATE completes.
 *
 * A user on a healthy local install (scripts/install.sh + `hermes desktop`)
 * sees "Update now" in Settings → About when upstream main moves, clicks it,
 * and the app hands off to the updater, quits, updates, and comes back:
 *   - the backend checkout is on the new upstream commit, clean;
 *   - the app relaunched by itself (a new Desktop process from the rebuilt
 *     package, with its own backend) and logged the update as finished OK;
 *   - the updated app boots to chat and a chat turn completes;
 *   - the updated app reports itself current (no update offered again).
 *
 * Real entry points only: the About panel's button and the app's own
 * hand-off (scripts/desktop-update/posix.sh → `hermes update`). Fakes: the git
 * server (local bare origin) and the LLM provider.
 */

import { type ChildProcess, fork } from 'node:child_process'
import * as fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { recordWebSockets, send, waitForInteractive } from '../core/harness'

import { gatedOn, KNOWN } from './gates'
import {
  canAcknowledgeHandoffDriverDisposal,
  type HandoffDriverCommand,
  type HandoffDriverMessage,
  markerPrecedesAppExit,
  processIdentityExited
} from './handoff-driver-protocol'
import {
  backendServeProcesses,
  closeQuietly,
  currentAs,
  desktopLog,
  desktopProcesses,
  diagnostics,
  git,
  handoffLog,
  isAlive,
  launchInstalledApp,
  publishUpstream,
  startInstallSession,
  waitFor
} from './harness'

const RUN = Date.now().toString(36)
const U = (n: number) => `U${n}-${RUN}`
const A = (n: number) => `A${n}-${RUN}`

interface LinuxProcStat {
  pid: number
  comm: string
  state: string
  ppid: number
  userTicks: number
  systemTicks: number
  threadCount: number
  startTicks: string
}

function linuxProcStat(pid: number, statPath = `/proc/${pid}/stat`): LinuxProcStat | null {
  try {
    const raw = fs.readFileSync(statPath, 'utf8')
    const close = raw.lastIndexOf(')')

    const fields = raw
      .slice(close + 2)
      .trim()
      .split(/\s+/)

    if (close < 0 || fields.length < 20) {
      return null
    }

    return {
      pid,
      comm: raw.slice(raw.indexOf('(') + 1, close),
      state: fields[0],
      ppid: Number(fields[1]),
      userTicks: Number(fields[11]),
      systemTicks: Number(fields[12]),
      threadCount: Number(fields[17]),
      startTicks: fields[19]
    }
  } catch {
    return null
  }
}

function desktopMainProcessesForInstall(
  facts: Parameters<typeof desktopProcesses>[0]
): ReturnType<typeof desktopProcesses> {
  return desktopProcesses(facts).filter(proc => {
    if (/--type=/.test(proc.cmdline)) {
      return false
    }

    return process.platform !== 'linux' || linuxProcStat(proc.pid)?.comm === 'Hermes'
  })
}

function linuxWaitChannel(pid: number, waitChannelPath = `/proc/${pid}/wchan`): string {
  try {
    return fs.readFileSync(waitChannelPath, 'utf8').trim() || 'unknown'
  } catch (error) {
    return `unreadable:${(error as NodeJS.ErrnoException).code ?? 'error'}`
  }
}

function linuxSignalBits(pid: number): string {
  try {
    const status = fs.readFileSync(`/proc/${pid}/status`, 'utf8')
    const field = (name: string) => new RegExp(`^${name}:\\s*([0-9a-f]+)$`, 'mi').exec(status)?.[1]
    const bit = 1n << BigInt(os.constants.signals.SIGTERM - 1)

    const hasSignal = (name: string) => {
      const value = field(name)

      return value === undefined ? 'unknown' : String((BigInt(`0x${value}`) & bit) !== 0n)
    }

    return `SIGTERM(blocked=${hasSignal('SigBlk')},ignored=${hasSignal('SigIgn')},caught=${hasSignal('SigCgt')},pending=${hasSignal('ShdPnd')})`
  } catch (error) {
    return `signals=unreadable:${(error as NodeJS.ErrnoException).code ?? 'error'}`
  }
}

function linuxThreadSummary(pid: number): string {
  try {
    const taskDir = fs.opendirSync(`/proc/${pid}/task`)
    const sampledTids = new Set([pid])

    try {
      for (let entry = taskDir.readSync(); entry && sampledTids.size < 64; entry = taskDir.readSync()) {
        if (/^\d+$/.test(entry.name)) {
          sampledTids.add(Number(entry.name))
        }
      }
    } finally {
      taskDir.closeSync()
    }

    const threads = [...sampledTids]
      .flatMap(tid => {
        const stat = linuxProcStat(tid, `/proc/${pid}/task/${tid}/stat`)

        return stat
          ? [
              {
                tid,
                stat,
                wchan: linuxWaitChannel(tid, `/proc/${pid}/task/${tid}/wchan`),
                cpuTicks: stat.userTicks + stat.systemTicks
              }
            ]
          : []
      })
      .sort((left, right) => right.cpuTicks - left.cpuTicks)
      .slice(0, 12)

    return threads.length
      ? threads
          .map(
            thread =>
              `tid=${thread.tid} comm=${thread.stat.comm} state=${thread.stat.state} cpuTicks=${thread.cpuTicks} wchan=${thread.wchan}`
          )
          .join(' | ')
      : 'threads=unavailable'
  } catch (error) {
    return `threads=unreadable:${(error as NodeJS.ErrnoException).code ?? 'error'}`
  }
}

function linuxProcessSnapshot(
  pid: number,
  expectedStartTicks: string | undefined,
  relatedProcesses: ReturnType<typeof desktopProcesses>
): string {
  if (process.platform !== 'linux') {
    return `pid=${pid} diagnostics=unavailable-platform`
  }

  try {
    const stat = linuxProcStat(pid)

    if (!stat) {
      return `pid=${pid} identity=unavailable (process exited or procfs unreadable)`
    }

    if (expectedStartTicks !== undefined && stat.startTicks !== expectedStartTicks) {
      return `pid=${pid} identity=changed expected-start=${expectedStartTicks} current-start=${stat.startTicks}`
    }

    const bootId = fs.readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim()
    const ancestry: string[] = []
    let parentPid = stat.ppid

    for (let depth = 0; depth < 5 && parentPid > 0; depth += 1) {
      const parent = linuxProcStat(parentPid)

      if (!parent) {
        ancestry.push(`pid=${parentPid} unavailable`)

        break
      }

      ancestry.push(
        `pid=${parent.pid} ppid=${parent.ppid} comm=${parent.comm} state=${parent.state} start=${parent.startTicks}`
      )
      parentPid = parent.ppid
    }

    const children: string[] = []
    const selected = new Set<number>([pid])
    let frontier = [pid]

    for (let depth = 0; depth < 3 && frontier.length > 0 && children.length < 16; depth += 1) {
      const next = relatedProcesses
        .filter(proc => frontier.includes(proc.ppid) && !selected.has(proc.pid))
        .slice(0, 16 - children.length)

      for (const child of next) {
        selected.add(child.pid)
        const childStat = linuxProcStat(child.pid)

        if (childStat) {
          children.push(
            `pid=${child.pid} ppid=${childStat.ppid} comm=${childStat.comm} state=${childStat.state} start=${childStat.startTicks} wchan=${linuxWaitChannel(child.pid)}`
          )
        }
      }

      frontier = next.map(child => child.pid)
    }

    const finalStat = linuxProcStat(pid)

    if (!finalStat || finalStat.startTicks !== stat.startTicks) {
      return `pid=${pid} identity=changed-during-snapshot start=${stat.startTicks} current=${finalStat?.startTicks ?? 'exited'}`
    }

    return [
      `pid=${pid} identity=${bootId}:${stat.startTicks} comm=${stat.comm} state=${stat.state} ppid=${stat.ppid} threads=${stat.threadCount} cpuTicks=${stat.userTicks + stat.systemTicks} wchan=${linuxWaitChannel(pid)} ${linuxSignalBits(pid)}`,
      `ancestry=${ancestry.join(' <- ') || 'none'}`,
      `packaged-desktop-descendants=${children.join(' | ') || 'none in current census'}`,
      `threads=${linuxThreadSummary(pid)}`
    ].join('\n')
  } catch (error) {
    return `pid=${pid} diagnostics=unreadable (${(error as NodeJS.ErrnoException).code ?? 'error'})`
  }
}

function shutdownBreadcrumbs(facts: { hermesHome: string }): string {
  const logsDir = path.join(facts.hermesHome, 'logs')

  const desktopEvents = [
    { pattern: /\[updates\] launched posix hand-off:/i, label: 'update hand-off launched' },
    { pattern: /\[updates\] hand-off script took the update marker/i, label: 'update hand-off marker adopted' },
    { pattern: /\[updates\] detached update finished OK/i, label: 'detached update finished OK' },
    { pattern: /\[updates\] detached update FAILED/i, label: 'detached update failed' },
    { pattern: /\[quit\]/i, label: 'quit breadcrumb present' },
    { pattern: /\[close-stop\]/i, label: 'close-stop breadcrumb present' },
    { pattern: /\[gpu\]/i, label: 'GPU breadcrumb present' }
  ]

  const chromiumEvents = [
    { pattern: /GPU process launch failed: error_code=(\d+)/i, label: 'GPU process launch failed' },
    {
      pattern: /Failed to send GetTerminationStatus message to zygote/i,
      label: 'zygote termination-status request failed'
    },
    { pattern: /Network service crashed or was terminated, restarting service/i, label: 'network service restarted' }
  ]

  const safeLogTail = (name: string): string => {
    try {
      const file = path.join(logsDir, name)
      const size = fs.statSync(file).size
      const length = Math.min(size, 64 * 1024)
      const buffer = Buffer.alloc(length)
      const fd = fs.openSync(file, 'r')

      try {
        fs.readSync(fd, buffer, 0, length, Math.max(0, size - length))
      } finally {
        fs.closeSync(fd)
      }

      return buffer.toString('utf8')
    } catch {
      return ''
    }
  }

  const normalize = (name: string, text: string, events: Array<{ pattern: RegExp; label: string }>): string[] =>
    text
      .split('\n')
      .flatMap(line => {
        const event = events.find(candidate => candidate.pattern.test(line))

        if (!event) {
          return []
        }

        const timestamp = /^(\S+\s+\S+)/.exec(line)?.[1] ?? 'timestamp unavailable'

        return [`${name}: ${timestamp} ${event.label}`]
      })
      .slice(-12)

  const lines = [
    ...normalize('desktop.log', safeLogTail('desktop.log'), desktopEvents),
    ...normalize('desktop-chromium.log', safeLogTail('desktop-chromium.log'), chromiumEvents)
  ]

  return lines.join('\n') || '(no matching shutdown or GPU breadcrumbs in captured logs)'
}

function sendDriverCommand(driver: ChildProcess, command: HandoffDriverCommand): Promise<void> {
  if (!driver.connected) {
    return Promise.reject(new Error(`handoff driver IPC closed before ${command.type}`))
  }

  return new Promise((resolve, reject) => {
    driver.send(command, error => (error ? reject(error) : resolve()))
  })
}

function driverMessageQueue(driver: ChildProcess): {
  next: (timeoutMs: number) => Promise<HandoffDriverMessage>
  stderr: () => string
} {
  const queued: HandoffDriverMessage[] = []
  let wake: (() => void) | undefined
  let hasExited = false
  let stderr = ''
  driver.stderr?.on('data', chunk => {
    stderr = `${stderr}${chunk.toString('utf8')}`.slice(-8_000)
  })
  driver.on('message', message => {
    queued.push(message as HandoffDriverMessage)
    wake?.()
    wake = undefined
  })
  driver.once('exit', () => {
    hasExited = true
    wake?.()
    wake = undefined
  })

  return {
    async next(timeoutMs) {
      const deadline = Date.now() + timeoutMs

      while (queued.length === 0) {
        const remaining = deadline - Date.now()

        if (hasExited) {
          throw new Error(`handoff driver exited before its next message; stderr: ${stderr || '(empty)'}`)
        }

        if (remaining <= 0) {
          throw new Error(`timed out waiting for handoff driver message; stderr: ${stderr || '(empty)'}`)
        }

        await new Promise<void>((resolve, reject) => {
          const timer = setTimeout(() => {
            wake = undefined
            reject(new Error('handoff driver message timeout'))
          }, remaining)

          wake = () => {
            clearTimeout(timer)
            resolve()
          }
        })
      }

      return queued.shift()!
    },
    stderr: () => stderr
  }
}

async function settleWithin(promise: Promise<unknown>, timeoutMs: number): Promise<void> {
  let timer: NodeJS.Timeout | undefined

  try {
    await Promise.race([
      promise.then(
        () => undefined,
        () => undefined
      ),
      new Promise<void>(resolve => {
        timer = setTimeout(resolve, timeoutMs)
      })
    ])
  } finally {
    if (timer) {
      clearTimeout(timer)
    }
  }
}

function markerTimestampAfter(text: string, offset: number): number | undefined {
  const match = text
    .slice(offset)
    .split('\n')
    .map(line =>
      /^([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2},[0-9]{3}).*\[updates\] hand-off script took the update marker(?: at the deadline)? \(pid \d+\)/.exec(
        line
      )
    )
    .find(Boolean)

  if (!match) {
    return undefined
  }

  // desktop.log uses local time; Date parses this timestamp in the same
  // runner-local zone that produced it.
  const timestamp = Date.parse(match[1].replace(' ', 'T').replace(',', '.'))

  return Number.isFinite(timestamp) ? timestamp : undefined
}

function originalAppIdentityAbsent(pid: number, startTicks: string | undefined): boolean {
  if (process.platform === 'linux') {
    const current = linuxProcStat(pid)

    return !current || processIdentityExited(current.startTicks, current.state, startTicks!)
  }

  return !isAlive(pid)
}

async function disposeHandoffDriverAfterUpdate(
  facts: Parameters<typeof desktopProcesses>[0],
  env: Record<string, string>,
  targetSha: string
): Promise<number> {
  const driver = fork(path.join(import.meta.dirname, 'handoff-driver.ts'), [], {
    cwd: path.resolve(import.meta.dirname, '../..'),
    execArgv: ['--import', 'tsx/esm'],
    env: process.env,
    stdio: ['ignore', 'ignore', 'pipe', 'ipc']
  })

  const messages = driverMessageQueue(driver)
  let driverExited = false

  const exited = new Promise<void>(resolve =>
    driver.once('exit', () => {
      driverExited = true
      resolve()
    })
  )

  const exitedWithin = async (timeoutMs: number): Promise<boolean> => {
    let timer: NodeJS.Timeout | undefined

    const timedOut = new Promise<boolean>(resolve => {
      timer = setTimeout(() => resolve(false), timeoutMs)
    })

    try {
      return await Promise.race([exited.then(() => true), timedOut])
    } finally {
      if (timer) {
        clearTimeout(timer)
      }
    }
  }

  const stopDriverOnly = async () => {
    if (driverExited) {
      return
    }

    await settleWithin(sendDriverCommand(driver, { type: 'abort' }), 500)
    await exitedWithin(2_500)

    if (!driverExited) {
      // This PID belongs to the isolated Playwright driver, never the app or updater.
      driver.kill('SIGKILL')
      await exitedWithin(2_000)
    }
  }

  try {
    const offset = desktopLog(facts).length
    await sendDriverCommand(driver, { type: 'start', facts, env, targetSha })
    const launched = await messages.next(30_000)

    if (launched.type === 'driver-error') {
      throw new Error(`handoff driver failed during ${launched.stage}: ${launched.error}; ${messages.stderr()}`)
    }

    if (launched.type !== 'launched') {
      throw new Error(`expected handoff driver launch identity, received ${launched.type}`)
    }

    const pid = launched.pid
    const original = linuxProcStat(pid)
    const startTicks = launched.startTicks

    if (!pid || (process.platform === 'linux' && (!startTicks || !original || original.startTicks !== startTicks))) {
      throw new Error(`could not establish original app process identity for PID ${pid}`)
    }

    await sendDriverCommand(driver, { type: 'identity-ack' })
    const ready = await messages.next(180_000)

    if (ready.type !== 'ready' || ready.pid !== pid) {
      throw new Error(`expected handoff driver ready for original PID ${pid}, received ${ready.type}`)
    }

    if (process.platform === 'linux' && linuxProcStat(pid)?.startTicks !== startTicks) {
      throw new Error(`original app identity changed before readiness for PID ${pid}`)
    }

    await sendDriverCommand(driver, { type: 'click' })
    let appExit: Extract<HandoffDriverMessage, { type: 'app-exit' }> | undefined
    const clickDeadline = Date.now() + 120_000
    let handoffFailure: string | undefined

    while (!appExit && !handoffFailure && Date.now() < clickDeadline) {
      let message: HandoffDriverMessage | undefined

      try {
        message = await messages.next(Math.min(1_000, clickDeadline - Date.now()))
      } catch (error) {
        if (!String(error).includes('handoff driver message timeout')) {
          throw error
        }
      }

      if (message?.type === 'driver-error') {
        throw new Error(`handoff driver failed during ${message.stage}: ${message.error}; ${messages.stderr()}`)
      }

      if (message?.type === 'click-result') {
        if (!message.ok) {
          throw new Error(`Update now click failed: ${message.error}`)
        }
      }

      if (message?.type === 'app-exit') {
        appExit = message
      }

      const newLog = desktopLog(facts).slice(offset)

      if (!appExit && /\[updates\] .*(fail|cancel|refus|could not|already running)/i.test(newLog)) {
        handoffFailure = newLog
      }
    }

    if (!appExit) {
      if (handoffFailure) {
        await gatedOn(KNOWN.preflightPython, () => {
          expect(
            false,
            `"Update now" hands off to the updater and the app quits for it; the app logged:\n${handoffFailure}`
          ).toBe(true)
        })
      }

      throw new Error(`the app did not quit for updater hand-off within 120 seconds\n${diagnostics(facts)}`)
    }

    // The marker's own timestamp must predate the ChildProcess exit report,
    // and procfs must independently show that this exact PID/start-time is gone.
    await waitFor(
      'the original app process identity to disappear after updater hand-off',
      () => originalAppIdentityAbsent(pid, startTicks) || undefined,
      { timeout: 10_000, interval: 50 }
    )
    const markerAt = markerTimestampAfter(desktopLog(facts), offset)

    const evidence = {
      markerAdoptedBeforeAppExit: markerPrecedesAppExit(markerAt, appExit.observedAt),
      originalAppExitReported: appExit.pid === pid,
      originalAppPidMatches: appExit.pid === pid,
      originalAppIdentityAbsent: originalAppIdentityAbsent(pid, startTicks)
    }

    if (!canAcknowledgeHandoffDriverDisposal(evidence)) {
      throw new Error(
        `handoff disposal evidence incomplete: ${JSON.stringify({ ...evidence, markerAt, appExit, pid, startTicks })}`
      )
    }

    await sendDriverCommand(driver, { type: 'dispose' })
    const disposed = await messages.next(5_000)

    if (disposed.type !== 'disposed') {
      throw new Error(`expected driver disposal acknowledgment, received ${disposed.type}`)
    }

    if (!(await exitedWithin(5_000))) {
      throw new Error('driver did not exit after disposal')
    }

    if (!originalAppIdentityAbsent(pid, startTicks)) {
      throw new Error('original app identity reappeared after driver disposal')
    }

    return pid
  } catch (error) {
    await stopDriverOnly()
    throw error
  }
}

test('clicking Update now moves the backend to the new commit, relaunches the app, and chat works after', async () => {
  test.setTimeout(20 * 60_000)
  const session = await startInstallSession()
  const { facts, provider, env } = session
  const before = git(facts.checkout, 'rev-parse', 'HEAD')
  const target = publishUpstream('release: next upstream main', 'UPSTREAM_RELEASE.md', `next release ${RUN}\n`)
  const explain = (extra = '') => diagnostics(facts, extra)

  try {
    let firstMainPid = 0

    await test.step('the running app offers the new upstream commit and hands off on click', async () => {
      firstMainPid = await disposeHandoffDriverAfterUpdate(facts, env, target)
    })

    await test.step('the update lands and the app relaunches itself', async () => {
      await waitFor(
        'the updater to finish `hermes update`',
        () => /hermes update exit code: \d+/.test(handoffLog(facts)),
        {
          timeout: 8 * 60_000,
          interval: 2_000,
          explain: () => explain(`handoff log:\n${handoffLog(facts)}`)
        }
      )
      expect(handoffLog(facts), `the updater's hermes update succeeded\n${explain()}`).toMatch(
        /hermes update exit code: 0\s*$|retry exit code: 0/m
      )
      expect(git(facts.checkout, 'rev-parse', 'HEAD'), 'the backend checkout is on the new upstream commit').toBe(
        target
      )

      // The updater's progress window can be the same Electron binary (--app=http://127.0.0.1:...): not a relaunch.
      const relaunched = await waitFor(
        'the app to relaunch itself',
        () => {
          const proc = desktopMainProcessesForInstall(facts).find(
            candidate => candidate.pid !== firstMainPid && !/--app=|https?:\/\/127\.0\.0\.1/.test(candidate.cmdline)
          )

          const startTicks = proc ? linuxProcStat(proc.pid)?.startTicks : undefined

          return proc && (process.platform !== 'linux' || startTicks) ? { ...proc, startTicks } : undefined
        },
        { timeout: 3 * 60_000, interval: 1_000, explain: () => explain(`handoff log:\n${handoffLog(facts)}`) }
      )

      await waitFor(
        'the relaunched app to log the update result',
        () => /\[updates\] detached update (finished|FAILED)/.test(desktopLog(facts)),
        {
          timeout: 3 * 60_000,
          explain
        }
      )
      expect(desktopLog(facts), `the relaunched app reports the update as finished OK\n${explain()}`).toMatch(
        /\[updates\] detached update finished OK/
      )
      await waitFor('the relaunched app to start its backend', () => backendServeProcesses(facts).length > 0, {
        timeout: 3 * 60_000,
        explain
      })
      expect(
        git(facts.checkout, 'status', '--porcelain', '--untracked-files=no'),
        'the updated checkout is clean'
      ).toBe('')

      // Keep stable identities for the packaged process family before signaling;
      // a later process with the same PID must not be mistaken for this app.
      const mainBeforeSignal = desktopMainProcessesForInstall(facts)
      const processesBeforeSignal = desktopProcesses(facts)

      const beforeStartTicks = new Map(
        processesBeforeSignal.map(proc => [proc.pid, linuxProcStat(proc.pid)?.startTicks])
      )

      const processStateBefore = mainBeforeSignal
        .slice(0, 4)
        .map(proc => linuxProcessSnapshot(proc.pid, undefined, processesBeforeSignal))

      // Signal the relaunched main process and let Chromium tear down its own
      // helpers. Simultaneously signaling the GPU process makes Chromium report
      // a fatal GPU-child failure during the very shutdown this test exercises.
      const relaunchedStartTicks = relaunched.startTicks
      const liveRelaunchedMain = desktopMainProcessesForInstall(facts).find(proc => proc.pid === relaunched.pid)

      if (
        !liveRelaunchedMain ||
        (process.platform === 'linux' &&
          (!relaunchedStartTicks ||
            beforeStartTicks.get(relaunched.pid) !== relaunchedStartTicks ||
            linuxProcStat(relaunched.pid)?.startTicks !== relaunchedStartTicks))
      ) {
        throw new Error('the relaunched Desktop process identity changed before the SIGTERM check')
      }

      try {
        process.kill(relaunched.pid, 'SIGTERM')
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ESRCH') {
          throw error
        }
      }

      try {
        await expect
          .poll(() => desktopProcesses(facts).map(p => `${p.pid} ${p.cmdline.slice(0, 160)}`), {
            timeout: 60_000,
            message: 'the relaunched app quits cleanly on SIGTERM (its gateway outlives it)'
          })
          .toEqual([])
      } catch (error) {
        const processesAfterSignal = desktopProcesses(facts)

        const diagnosticPids = new Set([
          ...mainBeforeSignal.slice(0, 4).map(proc => proc.pid),
          ...processesAfterSignal.slice(0, 8).map(proc => proc.pid)
        ])

        const processStateAfter = [...diagnosticPids].map(pid =>
          linuxProcessSnapshot(pid, beforeStartTicks.get(pid), processesAfterSignal)
        )

        throw new Error(
          `${(error as Error).message}\nSIGTERM process snapshot before:\n${processStateBefore.join('\n') || 'main process absent'}\n` +
            `SIGTERM process snapshot after:\n${processStateAfter.join('\n---\n') || 'test-owned app process exited'}\n` +
            `Shutdown breadcrumbs:\n${shutdownBreadcrumbs(facts)}`
        )
      }
    })

    await test.step('the updated app boots to chat, a turn completes, and it reports itself current', async () => {
      const { app, page, logTail } = await launchInstalledApp(facts, env)

      try {
        const ws = recordWebSockets(page)
        await waitForInteractive(app, page).catch(error => {
          throw new Error(`${(error as Error).message}\n${explain(logTail())}`)
        })
        provider.script(U(1), [{ text: [`${A(1)} `, 'after ', 'update'] }])
        await send(page, `${U(1)} hello after the update`, 'Enter', ws)
        await expect(page.getByText(`${A(1)} after update`)).toBeVisible({ timeout: 120_000 })
        expect(await currentAs(page), `the updated app is current (was ${before})`).toEqual({
          currentSha: target,
          updateAvailable: false
        })
      } finally {
        await closeQuietly(app)
      }
    })

    expect(fs.existsSync(facts.checkout)).toBe(true)
  } finally {
    await session.close()
  }
})

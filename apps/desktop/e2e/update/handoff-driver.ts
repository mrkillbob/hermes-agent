import { waitForInteractive } from '../core/harness'

import type { HandoffDriverCommand, HandoffDriverMessage } from './handoff-driver-protocol'
import { type InstallFacts, launchInstalledApp, openAbout, waitForUpdateOffer } from './harness'

interface StartCommand extends Extract<HandoffDriverCommand, { type: 'start' }> {
  facts: InstallFacts
}

const commands: HandoffDriverCommand[] = []
let commandWaiter: ((command: HandoffDriverCommand) => void) | undefined
let launched: Awaited<ReturnType<typeof launchInstalledApp>> | undefined

process.on('message', message => {
  const command = message as HandoffDriverCommand
  const waiter = commandWaiter

  if (waiter) {
    commandWaiter = undefined
    waiter(command)
  } else {
    commands.push(command)
  }
})

function receiveCommand(): Promise<HandoffDriverCommand> {
  const command = commands.shift()

  if (command) {
    return Promise.resolve(command)
  }

  return new Promise(resolve => {
    commandWaiter = resolve
  })
}

function send(message: HandoffDriverMessage): Promise<void> {
  if (!process.send) {
    return Promise.reject(new Error('handoff driver started without an IPC channel'))
  }

  return new Promise((resolve, reject) => {
    process.send!(message, error => (error ? reject(error) : resolve()))
  })
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

async function abortDriver(): Promise<never> {
  // The parent owns install cleanup. In particular, never call Playwright's
  // ElectronApplication.close() here: it can issue app.quit() and interfere
  // with an updater hand-off. SIGKILL only this driver PID so Playwright's
  // process-exit hook cannot turn a protocol failure into an app signal.
  process.disconnect()
  process.kill(process.pid, 'SIGKILL')
  throw new Error('unreachable after terminating the isolated handoff driver')
}

async function main(): Promise<void> {
  let stage = 'waiting for start command'

  try {
    const start = await receiveCommand()

    if (start.type !== 'start') {
      throw new Error(`expected start command, received ${start.type}`)
    }

    const init = start as StartCommand
    stage = 'launching packaged app'
    launched = await launchInstalledApp(init.facts as InstallFacts, init.env)
    const pid = launched.pid

    if (pid === undefined) {
      throw new Error('Playwright did not report the original Electron process PID')
    }

    const startTicks = launched.startTicks

    if (process.platform === 'linux' && !startTicks) {
      throw new Error(`could not capture original app start-time identity for PID ${pid}`)
    }

    await send({ type: 'launched', pid, ...(startTicks ? { startTicks } : {}) })
    const identityAck = await receiveCommand()

    if (identityAck.type === 'abort') {
      await abortDriver()
    }

    if (identityAck.type !== 'identity-ack') {
      throw new Error(`expected parent identity acknowledgment, received ${identityAck.type}`)
    }

    const appExit = new Promise<{ code: number | null; signal: NodeJS.Signals | null; observedAt: number }>(resolve => {
      launched!.app.process().once('exit', (code, signal) => {
        resolve({ code, signal, observedAt: Date.now() })
      })
    })

    stage = 'waiting for app readiness'
    await waitForInteractive(launched.app, launched.page)
    await openAbout(launched.page)
    await waitForUpdateOffer(launched.page, init.targetSha)
    await send({ type: 'ready', pid })

    const clickCommand = await receiveCommand()

    if (clickCommand.type === 'abort') {
      await abortDriver()
    }

    if (clickCommand.type !== 'click') {
      throw new Error(`expected click command, received ${clickCommand.type}`)
    }

    stage = 'clicking Update now'

    const click = launched.page
      .getByRole('button', { name: /^update now$/i })
      .first()
      .click()
      .then(
        () => send({ type: 'click-result', ok: true }),
        error => send({ type: 'click-result', ok: false, error: String(error) })
      )

    const terminal = await Promise.race([
      appExit.then(exit => ({ kind: 'app-exit' as const, exit })),
      receiveCommand().then(command => ({ kind: 'command' as const, command }))
    ])

    if (terminal.kind === 'command') {
      if (terminal.command.type === 'abort') {
        await abortDriver()
      }

      throw new Error(`received ${terminal.command.type} before the original app exited`)
    }

    await send({ type: 'app-exit', pid, ...terminal.exit })
    stage = 'waiting for parent disposal acknowledgment'
    const disposition = await receiveCommand()

    if (disposition.type === 'abort') {
      await abortDriver()
    }

    if (disposition.type !== 'dispose') {
      throw new Error(`expected dispose command after app exit, received ${disposition.type}`)
    }

    await send({ type: 'disposed' })
    process.exit(0)

    // The click promise may remain pending while Chromium closes the window.
    // The parent has already verified the app exit and acknowledged disposal.
    void click
  } catch (error) {
    await send({ type: 'driver-error', stage, error: String(error) }).catch(() => undefined)

    // The parent owns failure cleanup and will send abort. Keep the driver
    // alive briefly so the error can cross IPC before its bounded teardown.
    await Promise.race([receiveCommand(), sleep(5_000).then(() => ({ type: 'abort' as const }))])

    // Any terminal command here means the parent is ending a failed protocol;
    // if it disappeared, the bounded timeout reaches the same driver-only exit.
    await abortDriver()
  }
}

void main()

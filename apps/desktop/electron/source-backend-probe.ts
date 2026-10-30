import { execProbe, PROBE_TIMEOUT_MS } from './backend-probes'
import { runBackendStartStep } from './backend-start-cancellation'

interface InstalledProbeOptions {
  cwd: string
  env: NodeJS.ProcessEnv
  shell: boolean
  log?: (message: string) => void
  signal?: AbortSignal
  assertCurrentAttempt?: () => void
}

function failureKind(error: unknown): string {
  if (!error || typeof error !== 'object') {
    return 'probe error'
  }

  const { code, killed, signal } = error as { code?: unknown; killed?: boolean; signal?: string }

  if (killed || code === 'ETIMEDOUT' || signal === 'SIGTERM') {
    return 'timeout'
  }

  if (typeof code === 'number' && Number.isInteger(code)) {
    return `exit ${code}`
  }

  return ['ENOENT', 'EACCES', 'EPERM', 'EBUSY'].includes(String(code)) ? String(code) : 'probe error'
}

/** A transient PM/cold-start failure must not permanently classify an install as absent. */
export async function probeInstalledSource(command: string, options: InstalledProbeOptions): Promise<boolean> {
  const assertCurrent = () => {
    options.signal?.throwIfAborted()
    options.assertCurrentAttempt?.()
  }

  for (let attempt = 0; attempt < 2; attempt++) {
    assertCurrent()

    try {
      await execProbe(command, ['--version'], {
        cwd: options.cwd,
        env: options.env,
        shell: options.shell,
        stdio: 'ignore',
        // execProbe already retries timeouts once. Bound the additional recovery
        // to at most two five-second probes instead of restarting its full budget.
        timeout: attempt === 0 ? PROBE_TIMEOUT_MS : Math.min(PROBE_TIMEOUT_MS, 5_000),
        windowsHide: true,
        signal: options.signal,
        assertCurrentAttempt: options.assertCurrentAttempt
      })
      assertCurrent()

      return true
    } catch (error) {
      assertCurrent()
      const failure = failureKind(error)

      if (attempt === 1) {
        options.log?.(
          `[bootstrap] Installed runtime probe ${failure}; recovery retry exhausted. Retry startup or repair the installation.`
        )

        return false
      }

      options.log?.(`[bootstrap] Installed runtime probe ${failure}; retrying once before first-run setup.`)
      await runBackendStartStep(options.signal, () => new Promise<void>(resolve => setTimeout(resolve, 500)))
    }
  }

  return false
}

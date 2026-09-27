/**
 * backend-child.ts
 *
 * Windows-aware teardown for the desktop's managed backend child process.
 *
 * Node's `child.kill()` only signals the direct child. On Windows a backend
 * that spawned its own grandchildren (a `hermes` REPL, a pty terminal
 * session, the gateway) survives a plain SIGTERM and keeps files (e.g. the
 * venv shim) locked. So on Windows we tree-kill via `forceKillProcessTree`.
 *
 * On POSIX the backend IS spawned into its own session/process-group
 * (start_new_session=True), so `child.kill('SIGTERM')` would only reach the
 * backend and orphan its MCP grandchildren (the leak in #serve-orphans). We
 * signal the whole group via `process.kill(-pid, ...)` instead, falling back
 * to the direct child if the group send fails.
 *
 * Extracted into its own dependency-free module (no electron import) so the
 * tree-kill / group-kill branching can be asserted directly with a fake child
 * object and spy kill functions, instead of grepping main.ts source text for
 * the function body.
 */

export interface StopBackendChildDeps {
  /** Defaults to the real platform check; injectable for tests. */
  isWindows?: boolean
  /** Windows tree-kill implementation (real: taskkill /T /F via execFileSync). */
  forceKillProcessTree: (pid: number) => void
  /**
   * POSIX group-signal implementation. Real: process.kill(-pgid, signal).
   * Injectable so the negative-pid group send is asserted in tests without a
   * live process group. Defaults to process.kill.
   */
  killGroup?: (pgid: number, signal: NodeJS.Signals) => void
  /** True while the owned POSIX process group still has a live member. */
  isProcessGroupAlive?: (pgid: number) => boolean
}

export interface BackendProcessRoot {
  pid?: number | null
}

export interface KillableChild extends BackendProcessRoot {
  killed?: boolean
  kill: (signal: NodeJS.Signals) => void
}

export interface WaitableChild extends KillableChild {
  exitCode: number | null
  signalCode: string | null
  once: (event: 'exit' | 'error', listener: () => void) => unknown
  removeListener: (event: 'exit' | 'error', listener: () => void) => unknown
}

/** Graceful exit, SIGKILL escalation, then a bounded wait for the escalation. */
export async function waitForBackendExit(
  child: WaitableChild | null | undefined,
  deps: StopBackendChildDeps,
  timeoutMs: number = 5000
): Promise<void> {
  if (!child) {
    return
  }

  const exited = (): boolean => child.exitCode !== null || child.signalCode !== null
  const isWindows = deps.isWindows ?? process.platform === 'win32'
  const hasPid = Number.isInteger(child.pid)
  const groupAlive = (): boolean => {
    if (isWindows || !hasPid) {
      return false
    }

    if (deps.isProcessGroupAlive) {
      return deps.isProcessGroupAlive(child.pid as number)
    }

    try {
      process.kill(-(child.pid as number), 0)

      return true
    } catch (error) {
      return (error as NodeJS.ErrnoException).code === 'EPERM'
    }
  }

  const waitForGroupExit = (delay: number): Promise<void> =>
    new Promise<void>(resolve => {
      const deadline = Date.now() + delay
      const poll = (): void => {
        if (!groupAlive() || Date.now() >= deadline) {
          resolve()

          return
        }

        setTimeout(poll, Math.min(50, Math.max(1, deadline - Date.now())))
      }

      poll()
    })

  const wait = (delay: number): Promise<void> =>
    new Promise<void>((resolve: () => void): void => {
      if (exited()) {
        resolve()

        return
      }

      const finish = (): void => {
        clearTimeout(timer)
        child.removeListener('exit', finish)
        resolve()
      }

      const timer = setTimeout(finish, delay)
      child.once('exit', finish)
    })

  await Promise.all([wait(timeoutMs), waitForGroupExit(timeoutMs)])

  if (exited() && !groupAlive()) {
    return
  }

  try {
    if (isWindows && hasPid) {
      deps.forceKillProcessTree(child.pid as number)
    } else if (hasPid && groupAlive()) {
      try {
        const killGroup = deps.killGroup ?? ((pid: number, signal: NodeJS.Signals): boolean => process.kill(pid, signal))
        killGroup(-(child.pid as number), 'SIGKILL')
      } catch {
        if (!exited()) {
          child.kill('SIGKILL')
        }
      }
    } else if (!exited()) {
      child.kill('SIGKILL')
    }
  } catch {
    // A failed signal may mean the child is gone, but only exit proves it.
  }

  await Promise.all([wait(1000), waitForGroupExit(1000)])

  if (!exited() || groupAlive()) {
    throw new Error(
      `Backend child${child.pid ? ` (PID ${child.pid})` : ''} or its process group did not exit after SIGKILL; retaining ownership.`
    )
  }
}

/**
 * Stop a managed child process, choosing the right strategy for the platform.
 * No-ops silently if `child` is falsy, already killed, or the kill attempt
 * throws (the process may already be gone) -- mirrors the original inline
 * best-effort semantics in main.ts.
 */
export function stopBackendChild(child: KillableChild | null | undefined, deps: StopBackendChildDeps): void {
  if (!child || child.killed) {
    return
  }

  const isWindows = deps.isWindows ?? process.platform === 'win32'
  const killGroup = deps.killGroup ?? ((pgid: number, signal: string): boolean => process.kill(pgid, signal))

  try {
    if (isWindows && Number.isInteger(child.pid)) {
      deps.forceKillProcessTree(child.pid as number)
    } else if (Number.isInteger(child.pid)) {
      // POSIX: pgid == pid (start_new_session). Signal the whole group so MCP
      // grandchildren die too; fall back to the direct child on failure.
      try {
        killGroup(-(child.pid as number), 'SIGTERM')
      } catch {
        child.kill('SIGTERM')
      }
    } else {
      child.kill('SIGTERM')
    }
  } catch {
    // Already gone.
  }
}

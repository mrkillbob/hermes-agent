type MainWindowLike = {
  isDestroyed: () => boolean
}

type EnsureMainWindowOptions<T extends MainWindowLike> = {
  isReady: boolean
  createWindow: () => unknown
  focusWindow: (window: T) => unknown
  focusExisting?: boolean
}

/** The desktop owns its local backend, so it must exit with its last window. */
export function shouldQuitAfterLastWindowCloses(): boolean {
  return true
}

export function ensureMainWindow<T extends MainWindowLike>(
  window: T | null | undefined,
  { isReady, createWindow, focusWindow, focusExisting = true }: EnsureMainWindowOptions<T>
) {
  if (!window || window.isDestroyed()) {
    // a closed electron window stays truthy, so replace it before invoking native methods.
    if (isReady) {
      createWindow()
    }

    return
  }

  if (focusExisting) {
    focusWindow(window)
  }
}

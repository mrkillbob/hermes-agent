import { resolveElectronBinary } from '../electron-binary'

import { DESKTOP_ROOT, REPO_ROOT } from './harness'

/** Resolve Electron serially before Playwright starts parallel test workers. */
export default function globalSetup(): void {
  const electron = resolveElectronBinary([DESKTOP_ROOT, REPO_ROOT])
  console.log(`[core-e2e] Electron binary ready: ${electron}`)
}

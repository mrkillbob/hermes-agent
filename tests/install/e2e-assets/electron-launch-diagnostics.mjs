/**
 * Preserve launch evidence before the first inspector evaluation can fail.
 * Observation only: an exited or unreachable app still fails its journey.
 * @param {import('@playwright/test').ElectronApplication} app
 * @param {(message: string) => void} log
 */
export function observeElectronLaunch(app, log) {
  const child = app.process();
  let appClosed = false;
  child.stdout?.on('data', data => log(`[electron stdout] ${data}`));
  child.stderr?.on('data', data => log(`[electron stderr] ${data}`));
  child.once('exit', (code, signal) => log(`[electron exit] code=${code} signal=${signal}`));
  app.once('close', () => {
    appClosed = true;
    log('[electron close]');
  });
  return () => ({
    pid: child.pid, exitCode: child.exitCode, signalCode: child.signalCode,
    appClosed, windows: app.windows().length,
  });
}

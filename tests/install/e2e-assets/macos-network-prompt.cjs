// Read-only evidence after a failed normal quit. No input events, permission
// changes, window text, process arguments, environment or app termination.
const { execFile } = require('node:child_process');
const fs = require('node:fs');
const { resolveExecutablePath } = require('./macos-app-quit.cjs');

const AGENT = '/opt/hca/hosted-compute-agent';
const OBSERVER = `
ObjC.import('AppKit');
ObjC.import('CoreGraphics');
${resolveExecutablePath.toString()}
function run(args) {
  var resolvedExecutable = resolveExecutablePath(args[0]);
  var apps = [];
  var nativeApps = $.NSWorkspace.sharedWorkspace.runningApplications;
  for (var i = 0; i < nativeApps.count; i++) {
    var app = nativeApps.objectAtIndex(i);
    if (!app.executableURL || app.terminated) continue;
    var executable = ObjC.unwrap(app.executableURL.path);
    if (executable === args[0] || executable === args[1]) apps.push({ pid: Number(app.processIdentifier), executable: executable,
      finishedLaunching: Boolean(app.finishedLaunching), active: Boolean(app.active) });
  }
  // kCGNullWindowID is a C macro (0), unavailable in the JXA bridge. Convert
  // the returned CFArrayRef to an Objective-C object before deep-unwrapping;
  // outside a GUI session CoreGraphics can return NULL.
  var windowList = ObjC.castRefToObject($.CGWindowListCopyWindowInfo(
    $.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements, 0));
  var raw = windowList.isNil() ? [] : ObjC.deepUnwrap(windowList);
  // Retain only geometry and owner IDs, never titles or other window content.
  var windows = (raw || []).slice(0, 6).map(function (w) {
    return { pid: w.kCGWindowOwnerPID, id: w.kCGWindowNumber, layer: w.kCGWindowLayer, bounds: w.kCGWindowBounds };
  });
  return JSON.stringify({ apps: apps, windows: windows, resolvedExecutable: resolvedExecutable, observedAt: Date.now() });
}`;

function executeNative(command, args, options) {
  return new Promise((resolve, reject) => {
    execFile(command, args, { ...options, encoding: 'utf8', killSignal: 'SIGKILL', maxBuffer: 262144 },
      (error, stdout) => error ? reject(error) : resolve(stdout));
  });
}

function failureCategory(error) {
  if (error.code === 'EACCES' || error.code === 'EPERM') return 'observation-denied';
  if (error.killed || error.code === 'ETIMEDOUT') return 'command-deadline';
  if (error.code === 'ENOENT') return 'missing-tool';
  if (error.code === 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER') return 'output-limit';
  return 'observation-failed';
}

function processTable(text) {
  const rows = new Map();
  for (const line of text.split('\n')) {
    const match = /^\s*(\d+)\s+(\d+)\s+(\/[^\r\n]+)$/.exec(line);
    if (match) rows.set(Number(match[1]), { pid: Number(match[1]), parentPid: Number(match[2]), executable: match[3] });
  }
  return rows;
}

function stackMarkers(text) {
  const lines = text.split('\n');
  const start = lines.findIndex(line => line.includes('Thread_') && line.includes('com.apple.main-thread'));
  if (start < 0) return { mainThreadFound: false, frameMarkers: [] };
  let end = start + 1;
  while (end < lines.length && !lines[end].includes('Thread_')) end++;
  const main = lines.slice(start + 1, end).join('\n');
  const markers = ['node::SyncProcessRunner', 'uv_spawn', 'uv_run', 'wait4', 'kevent',
    'CFRunLoopRun', 'runModal', 'NSRunModal', 'CFUserNotification', 'LocalNetwork'];
  return { mainThreadFound: true, frameMarkers: markers.filter(marker => main.includes(marker)) };
}

async function collectQuitEvidence(executable, { execute = executeNative, now = Date.now, resolveExecutable = fs.realpathSync } = {}) {
  const report = { schema: 1, status: 'unavailable', alertClientAttribution: 'unverified' };
  if (typeof executable !== 'string' || !executable.startsWith('/') ||
      !executable.endsWith('/Hermes.app/Contents/MacOS/Hermes') || /[\r\n\0]/.test(executable)) {
    return { ...report, status: 'invalid-installed-executable' };
  }
  let canonical;
  try { canonical = resolveExecutable(executable); }
  catch { return { ...report, status: 'invalid-installed-executable' }; }
  if (typeof canonical !== 'string' || !canonical.startsWith('/') ||
      !canonical.endsWith('/Hermes.app/Contents/MacOS/Hermes') || /[\r\n\0]/.test(canonical)) {
    return { ...report, status: 'invalid-installed-executable' };
  }
  report.executableIdentity = { selected: executable, canonical };
  // Only these two spellings of the caller's exact installed file qualify.
  // Never resolve unrelated app paths or accept a basename/PID-only match.
  function matchesExecutable(path) {
    if (path !== executable && path !== canonical) return false;
    try { return resolveExecutable(path) === canonical; }
    catch { return false; }
  }
  const deadline = now() + 8000;
  async function read(command, args) {
    const remaining = deadline - now();
    if (remaining <= 0) throw Object.assign(new Error('budget expired'), { code: 'EVIDENCE_BUDGET' });
    return execute(command, args, { timeout: Math.min(2000, remaining) });
  }
  const observe = async () => JSON.parse(await read('/usr/bin/osascript', ['-l', 'JavaScript', '-e', OBSERVER, executable, canonical]));
  try {
    const initial = await observe();
    report.observedAt = { initial: Number.isSafeInteger(initial.observedAt) ? initial.observedAt : now() };
    const table = processTable(await read('/bin/ps', ['-axo', 'pid=,ppid=,comm=']));
    report.observedAt.processTable = now();
    report.installedProcessIds = [...table.values()].filter(row => matchesExecutable(row.executable)).map(row => row.pid).slice(0, 6);
    const agents = [...table.values()].filter(row => row.executable === AGENT);
    report.runnerAgent = agents.length === 1 ? agents[0] : { status: 'unverified' };
    report.visibleWindows = (initial.windows || []).slice(0, 6).map(window => {
      const owner = table.get(Number(window.pid));
      const path = owner?.executable;
      const allowedPath = path && (path === executable || path === canonical || path === AGENT || path.startsWith('/System/'));
      const bounds = {};
      for (const key of ['X', 'Y', 'Width', 'Height']) {
        const value = Number(window.bounds?.[key]);
        if (Number.isFinite(value) && Math.abs(value) <= 32768) bounds[key] = value;
      }
      return { ownerPid: Number(window.pid), windowId: Number(window.id), layer: Number(window.layer), bounds,
        ownerExecutable: allowedPath ? path : 'outside-observation-scope' };
    });
    if (!matchesExecutable(executable) || initial.resolvedExecutable && initial.resolvedExecutable !== canonical) {
      return { ...report, status: 'identity-changed' };
    }
    const apps = (initial.apps || []).filter(app => Number.isSafeInteger(app.pid) && app.pid > 0 && matchesExecutable(app.executable));
    if (apps.length !== 1) return { ...report, status: apps.length ? 'ambiguous-target' : 'target-absent' };
    const target = apps[0];
    const fresh = await observe();
    report.observedAt.revalidated = Number.isSafeInteger(fresh.observedAt) ? fresh.observedAt : now();
    const current = (fresh.apps || []).filter(app => Number.isSafeInteger(app.pid) && app.pid > 0 && matchesExecutable(app.executable));
    if (current.length > 1) return { ...report, status: 'ambiguous-target' };
    if (!matchesExecutable(executable) || fresh.resolvedExecutable && fresh.resolvedExecutable !== canonical ||
        current.length !== 1 || current[0].pid !== target.pid || !matchesExecutable(table.get(target.pid)?.executable)) {
      return { ...report, status: 'identity-changed' };
    }
    report.target = { pid: target.pid, executable: canonical, finishedLaunching: Boolean(current[0].finishedLaunching), active: Boolean(current[0].active) };
    // sample reads symbolic stacks of this revalidated app for one second.
    // Its raw output is never retained; only known main-thread frame markers.
    report.stack = stackMarkers(await read('/usr/bin/sample', [String(target.pid), '1', '10', '-file', '/dev/stdout']));
    report.status = 'captured';
  } catch (error) {
    report.status = error.code === 'EVIDENCE_BUDGET' ? 'budget-expired' : failureCategory(error);
  }
  return report;
}

if (require.main === module) {
  (async () => {
    if (process.platform !== 'darwin') return { schema: 1, status: 'unsupported-host' };
    if (process.argv.length !== 3) return { schema: 1, status: 'invalid-arguments' };
    try { return await collectQuitEvidence(process.argv[2]); }
    catch { return { schema: 1, status: 'invalid-installed-executable' }; }
  })().then(report => process.stdout.write(JSON.stringify(report) + '\n'));
}

module.exports = { collectQuitEvidence };

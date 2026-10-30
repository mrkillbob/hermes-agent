// Shared by Node regression tests and JXA's AppKit adapter below. Never send
// signals or forceTerminate: the installed app must complete its normal Quit.
function quitInstalledApps(executable, options) {
  var deadline = options.now() + (options.timeoutMs === undefined ? 120000 : options.timeoutMs);
  var quietSince = null;
  var requested = {};
  for (;;) {
    var apps = options.list().filter(function (app) { return app.executable === executable; });
    var observedAt = options.now();
    if (!apps.length) {
      if (quietSince === null) quietSince = options.now();
      // The updater publishes its receipt before launching the successor.
      // Re-query rather than retaining the pre-update process inventory.
      if (options.now() - quietSince >= 2000) return;
    } else {
      quietSince = null;
      apps.forEach(function (app) {
        if (requested[app.pid]) return;
        if (!options.terminate(app)) throw new Error('normal Quit refused: pid=' + app.pid);
        requested[app.pid] = true;
      });
    }
    if (options.now() >= deadline) {
      throw new Error('installed app did not quit normally: ' + apps.map(function (app) {
        return 'pid=' + app.pid + ' finishedLaunching=' + app.finishedLaunching + ' active=' + app.active;
      }).join('; ') + ' observedAt=' + observedAt);
    }
    options.delay(0.2);
  }
}

// osascript -l JavaScript macos-app-quit.cjs /exact/installed/executable
function waitForWorkspaceRefresh(seconds) {
  // NSWorkspace's list and NSRunningApplication's dynamic state only refresh
  // when the main run loop runs in a common mode. JXA delay does not do that.
  $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(seconds));
}

function resolveExecutablePath(executable) {
  // NSURL preserves aliases such as /var; use the same physical resolution as
  // Node realpath. The caller-owned buffer is macOS PATH_MAX (1024 bytes).
  ObjC.bindFunction('realpath', ['char *', ['char *', 'void *']]);
  var buffer = $.NSMutableData.dataWithLength(1024);
  var physical = $.realpath(executable, buffer.mutableBytes);
  if (typeof physical !== 'string' || physical.charAt(0) !== '/' || /[\r\n\0]/.test(physical)) {
    throw new Error('cannot resolve installed executable');
  }
  return physical;
}

function run(args) {
  if (args.length !== 1 || args[0].charAt(0) !== '/') throw new Error('expected one absolute installed executable');
  ObjC.import('AppKit');
  var selected = args[0];
  var canonical = resolveExecutablePath(selected);
  quitInstalledApps(canonical, {
    now: function () { return Date.now(); },
    delay: waitForWorkspaceRefresh,
    list: function () {
      if (resolveExecutablePath(selected) !== canonical) throw new Error('installed executable changed during normal Quit');
      var nativeApps = $.NSWorkspace.sharedWorkspace.runningApplications;
      var apps = [];
      for (var i = 0; i < nativeApps.count; i++) {
        var app = nativeApps.objectAtIndex(i);
        if (app.executableURL && !app.terminated) {
          var executable = ObjC.unwrap(app.executableURL.path);
          apps.push({
            pid: Number(app.processIdentifier), executable: executable === selected ? canonical : executable,
            finishedLaunching: Boolean(app.finishedLaunching), active: Boolean(app.active), native: app
          });
        }
      }
      return apps;
    },
    terminate: function (app) { return Boolean(app.native.terminate); }
  });
}

if (typeof module !== 'undefined') module.exports = { quitInstalledApps, run, waitForWorkspaceRefresh, resolveExecutablePath };

// Shared by Node regression tests and JXA's AppKit adapter below. Never send
// signals or forceTerminate: the installed app must complete its normal Quit.
function quitInstalledApps(executable, options) {
  var deadline = options.now() + (options.timeoutMs === undefined ? 120000 : options.timeoutMs);
  var quietSince = null;
  var requested = {};
  for (;;) {
    var apps = options.list().filter(function (app) { return app.executable === executable; });
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
      }).join('; '));
    }
    options.delay(0.2);
  }
}

// osascript -l JavaScript macos-app-quit.cjs /exact/installed/executable
function run(args) {
  if (args.length !== 1 || args[0].charAt(0) !== '/') throw new Error('expected one absolute installed executable');
  ObjC.import('AppKit');
  quitInstalledApps(args[0], {
    now: function () { return Date.now(); },
    delay: function (seconds) { delay(seconds); },
    list: function () {
      var nativeApps = $.NSWorkspace.sharedWorkspace.runningApplications;
      var apps = [];
      for (var i = 0; i < nativeApps.count; i++) {
        var app = nativeApps.objectAtIndex(i);
        if (app.executableURL && !app.terminated) apps.push({
          pid: Number(app.processIdentifier), executable: ObjC.unwrap(app.executableURL.path),
          finishedLaunching: Boolean(app.finishedLaunching), active: Boolean(app.active), native: app
        });
      }
      return apps;
    },
    terminate: function (app) { return Boolean(app.native.terminate); }
  });
}

if (typeof module !== 'undefined') module.exports = { quitInstalledApps, run };

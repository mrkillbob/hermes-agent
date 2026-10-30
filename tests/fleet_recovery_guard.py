"""Early pytest plugin: source-only recovery qualification cannot do live I/O.

Load with -p tests.fleet_recovery_guard before production test-module imports.
Audit hooks cannot be removed by replacing socket/Popen attributes in a test.
The canonical runner, not this plugin, owns worker startup and teardown.
"""

import os
from pathlib import Path
import sys
import socket

_SOURCE = Path(__file__).resolve().parents[1]
_SCRATCH = Path(os.environ["HERMES_TEST_SCRATCH_ROOT"]).resolve()
_HOME = _SCRATCH / ("audit-home-" + str(os.getpid()))
_HOME.mkdir(parents=True, exist_ok=True)
os.environ["HOME"] = str(_HOME)
os.environ["HERMES_HOME"] = str(_HOME / ".hermes")
os.environ["HERMES_RUNTIME_DIR"] = str(_HOME / "runtime")


def _owned(path):
    if isinstance(path, int):
        return True
    value = Path(os.fsdecode(path)).resolve()
    return (
        value == Path("/dev/null")
        or value.is_relative_to(_SOURCE)
        or value.is_relative_to(_SCRATCH)
    )


def _audit(event, args):
    if event == "socket.__new__" and args[1] == socket.AF_UNIX:
        # pytest's autouse asyncio fixture uses an anonymous local socketpair as
        # its self-pipe. No bind/connect/listen route is permitted below.
        return
    if event.startswith("socket.") or event in {
        "subprocess.Popen",
        "os.system",
        "os.posix_spawn",
        "os.spawn",
        "os.kill",
    }:
        raise PermissionError("source-only fleet recovery test forbids live transport/executor")
    if event == "sqlite3.connect" and args[0] != ":memory:" and not _owned(args[0]):
        raise PermissionError("SQLite outside owned test roots")
    if event == "open":
        path, mode, flags = args
        writes = (mode is not None and any(value in mode for value in "wax+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        )
        if writes and not _owned(path):
            raise PermissionError("write outside owned test roots")
    if event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.rename"}:
        count = 2 if event == "os.rename" else 1
        if any(not _owned(path) for path in args[:count]):
            raise PermissionError("filesystem mutation outside owned test roots")


sys.addaudithook(_audit)

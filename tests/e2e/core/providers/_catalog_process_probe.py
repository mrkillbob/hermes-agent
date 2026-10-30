"""Fixture-owned, payload-free thread evidence for a synthetic catalog client.

The CLI argv, timeout and exit path stay real. The independent faulthandler
watchdog can observe a Python shutdown join even after the main loop returned.
Raw dumps remain inside the temporary fixture and are never attached to failures.
"""
from __future__ import annotations

from contextlib import contextmanager
import faulthandler
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import sysconfig
import tempfile
import time

_REPO = Path(__file__).resolve().parents[4]
_probe_stream = None
_FRAME = re.compile(r'^\s*File "([^"]+)", line (\d+) in ([\w.<>]+)$')
_KNOWN_FILES = frozenset({
    "threading.py", "concurrent/futures/thread.py", "logging/__init__.py", "logging/handlers.py",
    "subprocess.py", "asyncio/base_events.py", "asyncio/runners.py", "asyncio/selector_events.py",
    "selectors.py", "queue.py", "socket.py", "ssl.py", "http/client.py", "urllib/request.py",
    "hermes_logging.py", "hermes_cli/main.py", "hermes_cli/gateway_chat.py", "hermes_cli/gateway_client.py",
    "hermes_cli/observability/shared_metrics.py", "hermes_cli/observability/shared_metrics_gateway.py",
    "hermes_cli/observability/shared_metrics_process.py", "hermes_cli/observability/relay_shared_metrics.py",
    "agent/auxiliary_client.py", "agent/models_dev.py", "agent/relay_runtime.py", "model_tools.py",
    "requests/sessions.py", "requests/adapters.py", "httpx/_client.py", "httpcore/_sync/connection_pool.py",
})
_KNOWN_FUNCTIONS = frozenset({
    "_shutdown", "_python_exit", "join", "_wait_for_tstate_lock", "stop", "shutdown", "flush", "acquire",
    "_stop_queue_listener", "_monitor", "emit", "doRollover", "_open", "wait", "select", "poll", "run",
    "run_forever", "_run_once", "_run", "_worker", "_bootstrap", "_bootstrap_inner", "call", "communicate",
    "_wait", "waitpid", "getaddrinfo", "get", "put", "read", "readline", "write", "close", "__exit__",
    "__enter__", "main", "_run_oneshot_from_args", "record", "info", "handle", "callHandlers", "_log",
})


def arm_probe(sink: str, delay: float) -> None:
    """Arm only this interpreter; no signals, process attachment or exit changes."""
    global _probe_stream
    _probe_stream = open(sink, "w", encoding="utf-8")
    _probe_stream.write(json.dumps({"pid": os.getpid(), "delay_s": delay,
                                    "probe_monotonic_s": round(time.monotonic(), 3)}) + "\n")
    _probe_stream.flush()
    faulthandler.dump_traceback_later(delay, file=_probe_stream, repeat=False)


def arm_if_client(home: str, sink: str, capture_at: float, argv: list[str], parent_pid: int) -> None:
    """Inherited gateway/tool children must not overwrite the client's evidence."""
    if (sys.orig_argv == argv and os.getppid() == parent_pid
            and os.environ.get("HERMES_HOME") == str(Path(home) / ".hermes")):
        arm_probe(sink, max(0.1, capture_at - time.monotonic()))


@contextmanager
def client_probe(home: Path, env: dict[str, str], timeout: float, args: tuple[str, ...]):
    from tests.e2e.core.providers._openai_helpers import write_sitecustomize_shim

    with tempfile.TemporaryDirectory(prefix="catalog-probe-", dir=home) as raw:
        directory = Path(raw)
        sink = directory / "threads.txt"
        # At the normal75s deadline this fires at70s. No extra wait is added to
        # the parent's kill path; short diagnostic contracts retain a positive delay.
        capture_at = time.monotonic() + timeout - min(5.0, timeout / 3)
        argv = [sys.executable, "-m", "hermes_cli.main", *args]
        body = ("from tests.e2e.core.providers._catalog_process_probe import arm_if_client\n"
                f"arm_if_client({str(home)!r}, {str(sink)!r}, {capture_at!r}, {argv!r}, {os.getpid()!r})\n")
        shim = write_sitecustomize_shim(directory / "shim", body)
        child_env = {**env, "PYTHONPATH": str(shim) + os.pathsep + env.get("PYTHONPATH", str(_REPO))}
        yield child_env, sink


def _frame_path(raw: str) -> str | None:
    path = raw.replace("\\", "/")
    if ".." in path.split("/"):
        return None
    # Trust actual interpreter roots, not a substring resembling a library path.
    roots = [("repo", str(_REPO)),
             ("dependency", sysconfig.get_path("purelib")), ("dependency", sysconfig.get_path("platlib")),
             ("stdlib", sysconfig.get_path("stdlib"))]
    for kind, value in roots:
        prefix = value.replace("\\", "/").rstrip("/") + "/"
        if path.startswith(prefix):
            relative = path[len(prefix):]
            # Unknown source names may themselves contain payload. A digest keeps
            # distinct locations correlatable without emitting arbitrary names.
            label = relative if relative in _KNOWN_FILES else "sha256:" + hashlib.sha256(relative.encode()).hexdigest()
            return kind + "/" + label
    return None  # no fixture/user-home paths or function names from unknown sources


def read_probe(sink: Path, pid: int) -> dict:
    """Allowlist frame locations, never source text, locals, argv or raw dump lines."""
    try:
        with sink.open(encoding="utf-8", errors="replace") as stream:
            header = json.loads(stream.readline(1025))
            raw = stream.read(131072)
    except (OSError, ValueError):
        return {"status": "unavailable"}
    if not isinstance(header, dict) or header.get("pid") != pid:
        return {"status": "identity_mismatch"}
    numeric = {}
    for key in ("delay_s", "probe_monotonic_s"):
        value = header.get(key)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            numeric[key] = value
    threads = []
    current = None
    frames_truncated = False
    for line in raw.splitlines():
        if re.match(r"^(?:Current thread|Thread) 0x[0-9a-f]+ ", line):
            current = {"frames": []}
            threads.append(current)
        elif current is not None:
            match = _FRAME.fullmatch(line)
            if match and len(match[1]) <= 1024 and len(match[2]) <= 10 and len(match[3]) <= 128:
                location = _frame_path(match[1])
                if location:
                    if len(current["frames"]) < 12:
                        function = match[3] if match[3] in _KNOWN_FUNCTIONS else "other"
                        current["frames"].append({"file": location, "line": int(match[2]), "function": function})
                    else:
                        frames_truncated = True
    # Keep the final thread too (the shutdown/main thread is normally last).
    bounded = threads if len(threads) <= 16 else threads[:15] + threads[-1:]
    return {"status": "captured" if threads else "armed", **numeric,
            "threads": bounded, "truncated": frames_truncated or len(threads) > 16 or len(raw) == 131072}

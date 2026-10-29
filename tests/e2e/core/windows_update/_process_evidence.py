"""Bounded fixture-owned cleanup observations; never wait, retry, or admit a launch."""
import json
import math
import time

_ERRORS = {"AccessDenied", "NoSuchProcess", "ZombieProcess", "OSError", "PermissionError", "TimeoutExpired"}
_MAX_BYTES = 262144
_PHASES = {"cleanup_scan", "kill_requested", "kill_returned", "kill_error", "terminal_sample",
           "root_kill_requested", "root_wait_returned", "root_wait_error", "cleanup_capture_truncated",
           "tracked_poll", "tracked_taskkill_returned", "cleanup_file_truncated"}


def error_type(error):
    name = type(error).__name__
    return name if name in _ERRORS else "other"


def _birth(value):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value < 1e12 else None


def note_identity(scan, proc, birth):
    try:
        if scan is not None:
            pid = proc.pid
            scan.setdefault("identities", {})[id(proc)] = {
                "pid": pid if type(pid) is int and 0 < pid < 2**32 else None,
                "recorded_birth": _birth(birth), "identity_source": "owned_scan"}
    except Exception:  # health: allow BLE001 -- Evidence cannot change which processes the fixture owns.
        pass


def note_unclassified(scan, error):
    try:
        if scan is not None:
            counts = scan.setdefault("unclassified", {})
            key = error_type(error)
            counts[key] = min(counts.get(key, 0) + 1, 1_000_000)
    except Exception:  # health: allow BLE001 -- An enumeration failure is evidence, not a new cleanup failure.
        pass


def _safe_event(event):
    if not isinstance(event, dict) or event.get("phase") not in _PHASES:
        return {}
    result = {"phase": event["phase"]}
    pid = event.get("pid")
    if type(pid) is int and 0 < pid < 2**32:
        result["pid"] = pid
    if "recorded_birth" in event:
        result["recorded_birth"] = _birth(event["recorded_birth"])
    for key, allowed in (("identity_source", {"owned_scan", "popen_handle", "unknown"}),
                         ("birth_state", {"not_observed"}),
                         ("operation_binding", {"pid", "existing_wait", "existing_poll"}),
                         ("settlement", {"unknown_binding", "pending", "identity_not_running", "unknown"}),
                         ("error_type", _ERRORS | {"other"})):
        if event.get(key) in allowed:
            result[key] = event[key]
    if type(event.get("pid_reused")) is bool or event.get("pid_reused") == "unknown":
        result["pid_reused"] = event["pid_reused"]
    for key in ("utc_s", "monotonic_s"):
        if _birth(event.get(key)) is not None:
            result[key] = event[key]
    if event.get("process_state") in {"running", "exited"}:
        result["process_state"] = event["process_state"]
    for key in ("matched", "returncode", "capture_limit", "byte_limit"):
        if type(event.get(key)) is int and -(2**31) <= event[key] < 2**31:
            result[key] = event[key]
    counts = event.get("unclassified")
    if isinstance(counts, dict):
        result["unclassified"] = {key: min(value, 1_000_000) for key, value in counts.items()
                                  if key in _ERRORS | {"other"} and type(value) is int and value >= 0}
    return result


def append_event(path, event):
    try:
        event = _safe_event(event)
        if not event:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        size = path.stat().st_size if path.exists() else 0
        if size >= _MAX_BYTES:
            return
        row = {"schema": 1, "utc_s": time.time(), "monotonic_s": time.monotonic(), **event}
        line = json.dumps(row, sort_keys=True, allow_nan=False) + "\n"
        if len(line.encode()) <= 4096:
            if size + len(line.encode()) > _MAX_BYTES - 256:
                marker = {"schema": 1, "utc_s": time.time(), "monotonic_s": time.monotonic(),
                          "phase": "cleanup_file_truncated", "byte_limit": _MAX_BYTES}
                tail = json.dumps(marker, sort_keys=True, allow_nan=False)
                padding = _MAX_BYTES - size - len(tail.encode()) - 1
                if padding < 0:
                    return
                line = tail + " " * padding + "\n"  # Valid trailing JSON whitespace seals the byte cap.
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line)
    except Exception:  # health: allow BLE001 -- Failed diagnostic I/O never replaces a kill or first-launch assertion.
        pass


class CleanupEvidence:
    def __init__(self, path, scan):
        self.path, self.scan = path, scan
        self.identities = scan.get("identities", {})
        self.captured = set()
        self.samples = []
        self.truncated = False
        self.events = [_stamp({"phase": "cleanup_scan", "matched": len(self.identities),
                               "capture_limit": 64, "unclassified": scan.get("unclassified", {})})]

    def observe(self, proc, phase, error=None):
        """A post-kill identity sample is not a settlement barrier or proof about a reused PID."""
        if id(proc) not in self.captured and len(self.captured) >= 64:
            if not self.truncated:
                self.events.append(_stamp({"phase": "cleanup_capture_truncated", "capture_limit": 64}))
                self.truncated = True
            return
        self.captured.add(id(proc))
        identity = self.identities.get(id(proc), {"identity_source": "unknown", "recorded_birth": None})
        row = {"phase": phase, **identity}
        if error is not None:
            row["error_type"] = error_type(error)
        self.events.append(_stamp(row))
        if phase not in {"kill_returned", "kill_error"}:
            return
        self.samples.append((proc, identity))

    def tracked(self, proc, phase, code):
        row = root_observation(proc, phase, returncode=code)
        if phase == "tracked_poll":
            row["process_state"] = "running" if code is None else "exited"
        self.events.append(row)

    def _sample(self, proc, identity):
        sample = {"phase": "terminal_sample", **identity, "settlement": "unknown_binding"}
        if identity.get("pid") is not None and identity.get("recorded_birth") is not None:
            try:
                running = proc.is_running()  # psutil binds this to the original Process birth identity.
                sample["settlement"] = ("pending" if running is True else
                                         "identity_not_running" if running is False else "unknown")
                reused = getattr(proc, "_pid_reused", None)
                sample["pid_reused"] = reused if type(reused) is bool else "unknown"
            except Exception as failure:  # health: allow BLE001 -- A failed observation remains unknown and does not change cleanup.
                sample.update(settlement="unknown", error_type=error_type(failure))
        return _stamp(sample)

    def finish(self):
        """Sample only after every original kill call; never wait for the sample to change."""
        try:
            self.events.extend(self._sample(proc, identity) for proc, identity in self.samples)
            for event in self.events:
                append_event(self.path, event)
        except Exception:  # health: allow BLE001 -- Neither evidence storage nor sampling may replace cleanup's outcome.
            pass


def _stamp(event):
    return {"utc_s": time.time(), "monotonic_s": time.monotonic(), **event}


def root_observation(proc, phase, *, returncode=None, error=None):
    try:
        pid = proc.pid
        if type(pid) is int and 0 < pid < 2**32:
            event = {"phase": phase, "pid": pid, "identity_source": "popen_handle",
                     "recorded_birth": None, "birth_state": "not_observed"}
            event["operation_binding"] = ("pid" if phase in {"root_kill_requested", "tracked_taskkill_returned"}
                                          else "existing_poll" if phase == "tracked_poll" else "existing_wait")
            if type(returncode) is int:
                event["returncode"] = returncode
            if error is not None:
                event["error_type"] = error_type(error)
            return _stamp(event)
    except Exception:  # health: allow BLE001 -- Root wait/kill behavior must survive missing diagnostic storage.
        pass
    return {}


def record_root(machine, proc, phase, *, returncode=None, error=None, request=None):
    try:
        path = machine.logs / "process-cleanup.jsonl"
        if request:
            append_event(path, request)
        append_event(path, root_observation(proc, phase, returncode=returncode, error=error))
    except Exception:  # health: allow BLE001 -- Root receipt I/O occurs after wait and cannot replace its outcome.
        pass

"""Payload-free observations of the probes that actually decided an update fence."""
from dataclasses import replace
import json
import logging
import math
import time

logger = logging.getLogger(__name__)
PREFIX = "gateway_update_fence="
_ERRORS = {"OSError", "PermissionError", "FileNotFoundError", "ProcessLookupError", "ValueError"}
_REASONS = {"empty_marker_in_flight", "marker_read_error", "marker_stat_error", "marker_live", "checkout_held"}
_LOCK_REASONS = {"owned", "sealed_missing_invalid", "missing", "open_error", "sealed_identity_invalid",
                 "lock_rejected", "free", "probe_error"}


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and abs(value) < 1e15 else None


def error_fields(error):
    result = {"error_type": type(error).__name__ if type(error).__name__ in _ERRORS else "other"}
    for key in ("errno", "winerror"):
        value = getattr(error, key, None)
        if type(value) is int and -(2**31) <= value < 2**31:
            result[key] = value
    return result


def observe_marker(marker, world, output):
    """Wrap existing callbacks; do not obtain any additional process-table observation."""
    def identity(pid, birth):
        return {"pid": pid if type(pid) is int and 0 <= pid < 2**32 else None,
                "recorded_birth": number(birth), "alive_probe": "not_probed",
                "birth_probe": "not_probed", "observed_birth": None}

    output.update(owner=identity(marker.pid, marker.create_time),
                  delegate=identity(marker.delegate_pid, marker.delegate_create_time),
                  age_seconds=number(marker.age(world)), probes=[],
                  self_process={"pid": world.pid, "birth": number(world.ct)})

    def record(kind, pid, value, error=None):
        try:
            roles = [output[key] for key in ("owner", "delegate") if output[key]["pid"] == pid]
            row = {"kind": kind, "pid": pid, "result": value}
            if error is not None:
                row.update(error_fields(error))
            if len(output["probes"]) < 8:
                output["probes"].append(row)
            for role in roles:
                if kind == "alive":
                    role["alive_probe"] = value if len(roles) == 1 else "ambiguous_role"
                else:
                    role["birth_probe"] = ("error" if error is not None else
                                           "observed" if value is not None else "unavailable")
                    role["observed_birth"] = value if len(roles) == 1 else None
                    if len(roles) > 1:
                        role["birth_probe"] = "ambiguous_role"
        except Exception:  # health: allow BLE001 -- Observations cannot alter the underlying identity probe.
            return

    def alive(pid):
        try:
            value = world.alive(pid)
        except Exception as error:
            record("alive", pid, "error", error)
            raise
        record("alive", pid, value if type(value) is bool else "unknown")
        return value

    def birth(pid):
        try:
            value = world.ct_of(pid)
        except Exception as error:
            record("birth", pid, None, error)
            raise
        record("birth", pid, number(value))
        return value

    def finish(result):
        try:
            output.update(verdict=result[0], selected_pid=result[1])
        except Exception:  # health: allow BLE001 -- A diagnostic sink never changes the marker verdict.
            return result
        return result

    return replace(world, alive=alive, ct_of=birth), finish


def checkout_result(output, held, reason, error=None):
    try:
        if output is not None:
            output.update(result=held, reason=reason)
            if error is not None:
                output.update(error_fields(error))
    except Exception:  # health: allow BLE001 -- The existing lock result wins over diagnostic failures.
        return held
    return held


def lock_attempt(output, arm, result, detail=None, error=None):
    try:
        row = {"arm": arm, "result": result}
        if isinstance(detail, dict):
            if detail.get("reason") in {"seek_error", "locking_error", "byte_acquired"}:
                row["reason"] = detail["reason"]
            row.update(_errors(detail))
        if error is not None:
            row.update(error_fields(error))
        attempts = output.setdefault("attempts", [])
        if len(attempts) < 3:
            attempts.append(row)
    except Exception:  # health: allow BLE001 -- Record an attempt only; never perform a replacement probe.
        return


def _pid(value):
    return value if type(value) is int and 0 <= value < 2**32 else None


def _errors(value):
    result = {}
    if value.get("error_type") in _ERRORS | {"other"}:
        result["error_type"] = value["error_type"]
    for key in ("errno", "winerror"):
        if type(value.get(key)) is int and -(2**31) <= value[key] < 2**31:
            result[key] = value[key]
    return result


def _identity(value):
    value = value if isinstance(value, dict) else {}
    alive = value.get("alive_probe", "not_probed")
    birth = value.get("birth_probe", "not_probed")
    return {"pid": _pid(value.get("pid")), "recorded_birth": number(value.get("recorded_birth")),
            "alive_probe": alive if type(alive) is bool or alive in {
                "not_probed", "unknown", "error", "ambiguous_role"} else "unknown",
            "birth_probe": birth if birth in {
                "observed", "unavailable", "not_probed", "error", "ambiguous_role"} else "unknown",
            "observed_birth": number(value.get("observed_birth"))}


def _marker(value):
    value = value if isinstance(value, dict) else {}
    result = {"verdict": value.get("verdict") if value.get("verdict") in {
        "live", "ours", "dead", "malformed"} else "unknown", "selected_pid": _pid(value.get("selected_pid")),
        "owner": _identity(value.get("owner")), "delegate": _identity(value.get("delegate")),
        "age_seconds": number(value.get("age_seconds")), "probes": []}
    own = value.get("self_process")
    if isinstance(own, dict):
        result["self_process"] = {"pid": _pid(own.get("pid")), "birth": number(own.get("birth"))}
    for key in ("empty_age_seconds", "empty_grace_seconds"):
        if key in value:
            result[key] = number(value[key])
    for probe in value.get("probes", [])[:8]:
        kind = probe.get("kind")
        if kind not in {"alive", "birth"}:
            continue
        answer = probe.get("result")
        if kind == "birth":
            answer = number(answer)
        elif type(answer) is not bool and answer not in {"unknown", "error"}:
            answer = "unknown"
        result["probes"].append({"kind": kind, "pid": _pid(probe.get("pid")),
                                  "result": answer, **_errors(probe)})
    return result


def _checkout(value):
    value = value if isinstance(value, dict) else {}
    answer = value.get("result", "not_probed")
    result = {"result": answer if type(answer) is bool or answer == "not_probed" else "unknown"}
    if value.get("reason") in _LOCK_REASONS:
        result["reason"] = value["reason"]
    result.update(_errors(value))
    if isinstance(value.get("attempts"), list):
        result["attempts"] = []
        for attempt in value["attempts"][:3]:
            if attempt.get("arm") not in {"windows_primary", "windows_leases", "posix_flock"}:
                continue
            answer = attempt.get("result")
            row = {"arm": attempt["arm"], "result": answer if type(answer) is bool or answer == "raised" else "unknown"}
            if attempt.get("reason") in {"seek_error", "locking_error", "byte_acquired"}:
                row["reason"] = attempt["reason"]
            result["attempts"].append({**row, **_errors(attempt)})
    return result


def record_fence(reason, home_index, *, marker=None, checkout=None, error=None):
    """Only internally built numeric facts/fixed labels; never marker bytes, paths or run IDs."""
    try:
        if reason not in _REASONS or type(home_index) is not int or not 0 <= home_index < 1024:
            return
        event = {"reason": reason, "home_index": home_index, "monotonic_s": round(time.monotonic(), 3),
                 "checkout": _checkout(checkout)}
        if marker is not None:
            event["marker"] = _marker(marker)
        if error is not None:
            event.update(error_fields(error))
        logger.info("%s%s", PREFIX, json.dumps(event, sort_keys=True, allow_nan=False))
    except Exception:  # health: allow BLE001 -- A diagnostic handler must not replace an admission decision.
        return

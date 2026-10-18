"""Read-only Windows job facts for diagnosing process-creation refusals."""
from __future__ import annotations

import ctypes
import sys


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
        ("limit_flags", ctypes.c_uint32),
        ("minimum_working_set", ctypes.c_size_t), ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_uint32), ("affinity", ctypes.c_size_t),
        ("priority_class", ctypes.c_uint32), ("scheduling_class", ctypes.c_uint32),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits), ("io_counters", ctypes.c_uint64 * 6),
        ("process_memory_limit", ctypes.c_size_t), ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
    ]


def _query_current_job(api, last_error) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "scope": "immediate_job", "in_job": None, "limit_flags": None,
        "membership_error": None, "query_error": None,
    }
    in_job = ctypes.c_int32()
    if not api.IsProcessInJob(api.GetCurrentProcess(), None, ctypes.byref(in_job)):
        snapshot["membership_error"] = last_error()
        return snapshot
    snapshot["in_job"] = bool(in_job.value)
    if in_job.value:
        limits = _ExtendedLimits()
        if api.QueryInformationJobObject(None, 9, ctypes.byref(limits), ctypes.sizeof(limits), None):
            snapshot["limit_flags"] = int(limits.basic.limit_flags)
        else:
            snapshot["query_error"] = last_error()
    return snapshot


def current_process_job_limits() -> dict[str, object] | None:
    """Query the caller's immediate job, never modify policy or launch a probe child.

    A NULL job handle queries the immediate job only, including under nested jobs.
    Flags 0x800/0x1000 describe explicit/silent breakaway permission. Unknown results
    carry numeric API errors instead of being mistaken for permission to break away.
    No process paths, job names, handles, environment or identities leave this query.
    """
    if sys.platform != "win32":
        return None
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, args, result in (
        ("GetCurrentProcess", [], ctypes.c_void_p),
        ("IsProcessInJob", [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32)], ctypes.c_int32),
        ("QueryInformationJobObject", [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                       ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)], ctypes.c_int32),
    ):
        fn = getattr(api, name)
        fn.argtypes = args
        fn.restype = result
    return _query_current_job(api, ctypes.get_last_error)

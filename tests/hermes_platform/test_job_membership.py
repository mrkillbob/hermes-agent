"""Job PID lists expose only supplied identities and never turn truncation into absence."""
from __future__ import annotations

import ctypes

import pytest


@pytest.mark.parametrize('success,assigned,returned,error,expected', [
    (True, 2, 2, 0, [True, False]),
    (True, 5000, 2, 0, [True, None]),
    (False, 5000, 2, 234, [None, None]),
    (False, 0, 0, 5, [None, None]),
])
def test_membership_reports_only_requested_pids_and_explicit_unknown(success, assigned, returned, error, expected):
    from hermes_platform.host import windows_job as job

    class Api:
        def GetCurrentProcess(self):
            return -1
        def IsProcessInJob(self, process, handle, result):
            assert process == -1 and handle is None
            ctypes.cast(result, ctypes.POINTER(ctypes.c_int32)).contents.value = 1
            return True
        def QueryInformationJobObject(self, handle, kind, result, size, actual):
            assert handle is None and kind == 3 and actual is None
            assert size == ctypes.sizeof(job._ProcessIds)
            data = ctypes.cast(result, ctypes.POINTER(job._ProcessIds)).contents
            data.assigned, data.returned = assigned, returned
            data.pids[0], data.pids[1] = 321, 987654
            return success

    result = job._query_membership(Api(), lambda: error, [321, 123])
    assert [r['present'] for r in result['members']] == expected
    assert [r['pid'] for r in result['members']] == [321, 123]
    assert result['query_error'] == (error or None)
    assert result['complete'] is (success and assigned == returned)
    assert '987654' not in str(result)


@pytest.mark.parametrize('failure', [False, True])
def test_absent_or_unreadable_job_does_not_query_members(failure):
    from hermes_platform.host import windows_job as job
    class Api:
        def GetCurrentProcess(self):
            return -1
        def IsProcessInJob(self, process, handle, result):
            return not failure
    result = job._query_membership(Api(), lambda: 5, [321])
    assert result['members'][0]['present'] is (None if failure else False)
    assert result['membership_error'] == (5 if failure else None)

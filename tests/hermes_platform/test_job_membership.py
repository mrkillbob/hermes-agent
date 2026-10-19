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


@pytest.mark.platforms('windows')
def test_native_owned_child_job_queries_real_pid_list_without_exposing_other_members():
    import json
    from pathlib import Path
    import subprocess
    import sys

    # Only this disposable child joins the fresh job; the test runner is untouched.
    program = r'''
import ctypes,json,os,sys
sys.path.insert(0,sys.argv[1])
from hermes_platform.host.windows_job import _kernel32,current_job_membership,current_process_job_limits
k=_kernel32()
k.CreateJobObjectW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p]
k.CreateJobObjectW.restype=ctypes.c_void_p
k.AssignProcessToJobObject.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
k.AssignProcessToJobObject.restype=ctypes.c_int32
k.CloseHandle.argtypes=[ctypes.c_void_p]
k.CloseHandle.restype=ctypes.c_int32
job=k.CreateJobObjectW(None,None)
if not job:raise ctypes.WinError(ctypes.get_last_error())
try:
    if not k.AssignProcessToJobObject(job,k.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    print(json.dumps({'pid':os.getpid(),'job':current_job_membership([os.getpid(),0]),
                      'limits':current_process_job_limits()}))
finally:
    k.CloseHandle(job)
'''
    result = subprocess.run([sys.executable, '-c', program, str(Path(__file__).resolve().parents[2])],
                            capture_output=True, text=True, encoding='utf-8', timeout=20, check=True)
    report = json.loads(result.stdout)
    assert report['limits']['in_job'] is True and report['limits']['limit_flags'] == 0
    job = report['job']
    assert job['in_job'] is True and job['complete'] is True
    assert job['membership_error'] is None and job['query_error'] is None
    assert job['members'] == [{'pid': report['pid'], 'present': True}, {'pid': 0, 'present': False}]

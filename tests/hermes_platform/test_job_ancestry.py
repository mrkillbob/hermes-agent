"""Bounded parent/job correlation must not mistake stale PIDs for live ancestry."""
from __future__ import annotations

import json

import pytest


def test_ancestry_is_bounded_private_and_stops_at_reused_parent(tmp_path):
    from hermes_platform.host.job_ancestry import _ancestry

    queried = []
    class Process:
        def __init__(self, pid):
            queried.append(pid)
            self.pid = pid
        def create_time(self):
            return {30: 300.0, 20: 200.0, 10: 400.0}[self.pid]
        def ppid(self):
            return self.pid - 10
        def exe(self):
            return str(tmp_path / 'private-name' / 'python.exe')

    report = _ancestry(30, tmp_path, Process)
    assert queried == [30, 20, 10]
    assert [(r['pid'], r['create_time']) for r in report['processes']] == [(30, 300.0), (20, 200.0)]
    assert report['stop'] == 'newer_parent'
    assert 'private-name' not in json.dumps(report)


def test_ancestry_cycle_depth_and_access_failure_are_explicit(tmp_path):
    from hermes_platform.host.job_ancestry import _ancestry
    import psutil

    class Process:
        def __init__(self, pid):
            self.pid = pid
        def create_time(self):
            return 100.0
        def ppid(self):
            return self.pid + 1
        def exe(self):
            return '/private/python.exe'

    report = _ancestry(1, tmp_path, Process)
    assert len(report['processes']) == 8 and report['stop'] == 'depth_limit'
    Process.ppid = lambda self: 1
    assert _ancestry(1, tmp_path, Process)['stop'] == 'cycle'
    def denied(pid):
        raise psutil.AccessDenied(pid)
    assert _ancestry(1, tmp_path, denied) == {'processes': [], 'stop': 'unavailable'}


@pytest.mark.platforms('windows')
def test_native_job_ancestry_correlates_current_identity_without_paths(tmp_path):
    import os
    from hermes_platform.host.job_ancestry import process_job_ancestry

    report = process_job_ancestry(tmp_path)
    current = report['processes'][0]
    assert current['pid'] == os.getpid() and current['create_time'] > 0
    assert len(report['processes']) <= 8
    assert len(report['job']['members']) == len(report['processes'])
    if report['job']['in_job']:
        assert report['job']['members'][0]['present'] is True, report
    assert str(tmp_path) not in json.dumps(report)


def test_failed_membership_query_keeps_collected_ancestry():
    from hermes_platform.host.job_ancestry import _with_membership

    original = {'processes': [{'pid': 30, 'parent_pid': 20, 'create_time': 300.0}], 'stop': 'unavailable'}
    def denied(pids):
        assert pids == [30]
        raise OSError(5, 'private-native-error')

    report = _with_membership(original, denied)
    assert report['processes'] == original['processes']
    assert report['job'] == {'scope': 'immediate_job_tree', 'unavailable': True}
    assert 'private' not in json.dumps(report)


@pytest.mark.parametrize('failure', ['denied', 'gone', 'oserror'])
def test_unreadable_ancestor_image_preserves_numeric_chain(tmp_path, failure):
    import psutil
    from hermes_platform.host.job_ancestry import _ancestry

    queried = []
    class Process:
        def __init__(self, pid):
            self.pid = pid
            queried.append(pid)
        def create_time(self):
            return float(self.pid * 10)
        def ppid(self):
            return self.pid - 10
        def exe(self):
            if self.pid == 20:
                raise {'denied': psutil.AccessDenied(20), 'gone': psutil.NoSuchProcess(20),
                       'oserror': OSError(5, 'private-path-error')}[failure]
            return str(tmp_path / 'private-path' / 'python.exe')

    report = _ancestry(30, tmp_path, Process)
    assert queried == [30, 20, 10]
    assert [row['pid'] for row in report['processes']] == [30, 20, 10]
    assert report['processes'][1] == {'pid': 20, 'parent_pid': 10, 'create_time': 200.0,
                                      'image': {'unavailable': True}}
    assert report['processes'][2]['image']['path_sha256']
    assert report['stop'] == 'root'
    assert 'private-path' not in json.dumps(report)

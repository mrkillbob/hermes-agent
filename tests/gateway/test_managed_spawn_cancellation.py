"""Cancelling threaded Popen acquisition still reaps the child it eventually creates."""
import asyncio
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_cancelled_spawn_reaps_late_child(monkeypatch):
    from gateway import session_managed_worker as managed
    entered, release, created = (threading.Event() for _ in range(3))
    original = subprocess.Popen
    children = []
    def spawn(args, **kwargs):
        entered.set()
        assert release.wait(10)
        child = original([sys.executable, '-c', 'import sys; sys.stdin.read()'], **kwargs)
        children.append(child)
        created.set()
        return child
    monkeypatch.setattr(managed.subprocess, 'Popen', spawn)
    monkeypatch.setattr(managed, '_worker_env', lambda authority: None)
    authority = SimpleNamespace(profile_id='profile', pending_results={})
    task = asyncio.create_task(managed.execute_managed(
        authority, SimpleNamespace(session_id='s'), {'admission_id': 'unstarted'}, None))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        assert await task == ''
        assert authority.pending_results['unstarted']['result']['interrupted'] is True
        release.set()
        assert await asyncio.to_thread(created.wait, 5)
        await asyncio.to_thread(children[0].wait, 5)
    finally:
        release.set()
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            child.stdin.close()
            child.stdout.close()


@pytest.mark.asyncio
async def test_budget_deadline_cancels_acquisition_and_reaps_late_child(monkeypatch, tmp_path):
    from dataclasses import replace
    import json
    from gateway import session_managed_worker as managed
    from gateway.session_policy import build_policy
    from hermes_state_runtime import RuntimeStoreError

    entered, release, created = (threading.Event() for _ in range(3))
    original, children = subprocess.Popen, []

    def spawn(args, **kwargs):
        entered.set()
        assert release.wait(10)
        child = original([sys.executable, '-c', 'import sys; sys.stdin.read()'], **kwargs)
        children.append(child)
        created.set()
        return child

    monkeypatch.setattr(managed.subprocess, 'Popen', spawn)
    monkeypatch.setattr(managed, '_worker_env', lambda authority: None)
    authority = SimpleNamespace(profile_id=str(tmp_path), pending_results={})
    policy = build_policy({'cwd': str(tmp_path), 'model': 'fixture'}, {}, profile_terminal=False)
    policy = replace(policy, kanban_json=json.dumps({'execution_budget': {
        'wall_seconds': 2, 'output_bytes': 1024, 'requests': 1}}))
    task = asyncio.create_task(managed.execute_managed(authority, SimpleNamespace(session_id='s'),
        {'admission_id': 'unstarted', 'payload': {'text': 'go'}}, policy))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        with pytest.raises(RuntimeStoreError, match='execution_wall_budget'):
            await asyncio.wait_for(task, 5)
        assert not children, 'deadline must return before the blocked spawn hands over a child'
        assert not authority.pending_results
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(created.wait, 5)
        child = children[0]
        try:
            await asyncio.to_thread(child.wait, 5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None:
                    stream.close()


@pytest.mark.asyncio
async def test_budget_deadline_bounds_working_directory_resolution(monkeypatch, tmp_path):
    from dataclasses import replace
    import json
    from gateway import session_managed_worker as managed
    from gateway.session_policy import build_policy
    from hermes_state_runtime import RuntimeStoreError

    entered, release = threading.Event(), threading.Event()
    resolved = managed.Path(managed.__file__).resolve()
    policy = build_policy({'cwd': str(tmp_path), 'model': 'fixture'}, {}, profile_terminal=False)
    policy = replace(policy, kanban_json=json.dumps({'execution_budget': {
        'wall_seconds': 2, 'output_bytes': 1024, 'requests': 1}}))

    def resolve():
        entered.set()
        assert release.wait(10)
        return resolved

    monkeypatch.setattr(managed, 'Path', lambda path: SimpleNamespace(resolve=resolve))
    monkeypatch.setattr(managed, '_worker_env', lambda authority: None)
    monkeypatch.setattr(managed.subprocess, 'Popen', lambda *a, **k: pytest.fail('spawn after preflight deadline'))
    authority = SimpleNamespace(profile_id=str(tmp_path), pending_results={})
    task = asyncio.create_task(managed.execute_managed(authority, SimpleNamespace(session_id='s'),
        {'admission_id': 'unstarted', 'payload': {'text': 'go'}}, policy))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        with pytest.raises(RuntimeStoreError, match='execution_wall_budget'):
            await asyncio.wait_for(task, 5)
        assert not authority.pending_results
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

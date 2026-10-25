"""Actual native source boot and restart under a targetless hold, provider-free."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from tests.gateway.test_normal_runtime_boot import control


def await_held(child, home, log):
    deadline = time.monotonic() + 45
    descriptor = {}
    while child.poll() is None and time.monotonic() < deadline:
        try:
            descriptor = control(home, 'identify')
            if descriptor.get('drain_reason') == 'startup_hold':
                return descriptor
        except (OSError, ValueError, AssertionError):
            pass
        time.sleep(.05)
    log.seek(0)
    effect = home / 'forbidden-effect'
    pytest.fail(effect.read_text() if effect.exists() else repr((descriptor, child.poll(), log.read())))


def stop(child):
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=15)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


async def observation_only(home, descriptor):
    from websockets.asyncio.client import connect
    binding = {'profile_id': str(home.resolve()), 'instance_id': descriptor['instance_id'], 'purpose': 'interactive'}
    grant = control(home, 'session-ticket', binding)
    protocols = ['hermes-gateway-v1', 'hermes-gateway-ticket.' + grant['ticket']]
    async with connect(descriptor['api_origin'].replace('http:', 'ws:') + '/api/ws', subprotocols=protocols) as peer:
        for method in ['ping', 'runtime.describe', 'session.create', 'prompt.submit', 'kanban.run', 'worker.adopt']:
            await peer.send(json.dumps({'jsonrpc': '2.0', 'id': method, 'method': method, 'params': {}}))
            async with asyncio.timeout(10):
                while True:
                    reply = json.loads(await peer.recv())
                    if reply.get('id') == method:
                        break
            if method == 'runtime.describe':
                assert reply['result']['capabilities'] == ['startup-hold-v1']
                assert reply['result']['session_create'] == {'sources': [], 'parameters': []}
            elif method == 'ping':
                assert reply['result']['pong'] is True
            else:
                assert reply.get('error'), reply


@pytest.mark.platforms('any')
def test_native_boot_stays_held_through_marker_loss_and_restart(tmp_path, monkeypatch):
    from gateway import drain_startup
    home = tmp_path / 'state'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(drain_startup, '_refuse_legacy_writers', lambda *args: None)
    monkeypatch.setattr(drain_startup, '_checkout_root', lambda: Path(os.environ['HERMES_HOME']))
    (home / 'ESTOP').write_text('preserved-pause')
    (home / '.env').write_text('PRIVATE_FIXTURE=must-not-load\n')
    (home / 'config.yaml').write_text('session_reset:\n  mode: daily\nmonitoring:\n  enabled: true\n')
    (home / '.clean_shutdown').write_text('old recovery receipt')
    (home / 'state').mkdir()
    (home / 'state/gateway.lifecycle.json').write_text('{"phase":"running","pid":999999,"deferred_integrity":true}')
    (home / 'cron').mkdir()
    (home / 'cron/jobs.json').write_text('{"jobs":[{"id":"fixture-old","enabled":true,"state":"dispatched","last_status":"running"}]}')
    names = ('ESTOP', '.env', 'config.yaml', '.clean_shutdown', 'state/gateway.lifecycle.json', 'cron/jobs.json')
    def stamp(name):
        path = home / name
        return path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns
    preserved = {name: stamp(name) for name in names}
    hold = drain_startup.begin()
    marker = home / '.drain_request.json'
    root = Path(__file__).resolve().parents[2]
    peer = root / 'tests/gateway/fixtures/startup_hold_peer.py'
    from pm.lifetime_launch import popen
    effects = []
    for remove_marker in (True, False):
        with (tmp_path / 'boot.log').open('w+') as log, popen(
                [sys.executable, str(peer)], cwd=root, env={**os.environ, 'HERMES_HOME': str(home)},
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT) as child:
            try:
                descriptor = await_held(child, home, log)
                assert descriptor['state'] == 'draining', descriptor
                asyncio.run(observation_only(home, descriptor))
                if remove_marker:
                    marker.unlink()
                assert control(home, 'identify')['state'] == 'draining'
                asyncio.run(observation_only(home, descriptor))
                assert not (home / 'kanban.db').exists()
                from gateway.status import write_planned_stop_marker
                owner_pid = json.loads((home / 'gateway.pid').read_text())['pid']
                assert write_planned_stop_marker(owner_pid)
                result = child.wait(timeout=20)
                effect = home / 'forbidden-effect'
                assert result == 0, effect.read_text() if effect.exists() else result
            finally:
                stop(child)
        assert not (home / 'forbidden-effect').exists()
        assert {name: stamp(name) for name in preserved} == preserved
        effects.append(sorted(str(path.relative_to(home)) for path in home.rglob('*') if path.is_file()))
    drain_startup.restore(hold_id=hold['hold_id'])
    assert not marker.exists()
    print(json.dumps({'held_boots': 2, 'marker_loss_survived': True, 'provider_calls': 0, 'files': effects}))

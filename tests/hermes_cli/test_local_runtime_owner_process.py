"""A disposable cross-process router survives consumer exit and stops with its owner."""

import json
import os
from pathlib import Path
import subprocess
import sys

import psutil
import pytest

from tests.waiting import wait_until


def _alive(identity):
    try:
        proc = psutil.Process(identity['pid'])
        return proc.create_time() == identity['created'] and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest.mark.platforms('linux', 'macos', 'windows')
def test_boot_contender_and_consumer_exit_preserve_the_owner_router(tmp_path):
    home = tmp_path / 'home'
    (home / 'models').mkdir(parents=True)
    (home / 'models' / 'fixture.gguf').touch()
    script = Path(__file__).parent / 'fixtures' / 'runtime_owner_process.py'
    env = dict(os.environ, HERMES_HOME=str(home), PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    processes = []
    with (tmp_path / 'children.log').open('w') as log:
        def launch(role):
            proc = subprocess.Popen([sys.executable, str(script), str(tmp_path), role],
                                    env=env, stdout=log, stderr=log)
            processes.append(proc)
            return proc

        def receipt(name):
            path = tmp_path / name
            assert wait_until(path.exists, timeout=20), (name, (tmp_path / 'children.log').read_text())
            return json.loads(path.read_text())

        try:
            owner = launch('owner')
            identity = receipt('owner-child.json')
            contender = launch('contender')
            assert wait_until((tmp_path / 'contended').exists, timeout=20)
            assert not (tmp_path / 'contender-child.json').exists()
            (tmp_path / 'publish').touch()
            assert receipt('owner-result.json') == {'owned': True, 'adopted': True}
            assert receipt('contender-result.json') == {'owned': False, 'adopted': True}
            assert contender.wait(timeout=20) == 0
            assert _alive(identity)
            consumer = launch('consumer')
            assert receipt('consumer-result.json') == {'owned': False, 'adopted': True}
            assert consumer.wait(timeout=20) == 0
            assert not (tmp_path / 'consumer-child.json').exists()
            assert _alive(identity), 'a consumer exiting stopped the shared router'
            (tmp_path / 'stop').touch()
            assert owner.wait(timeout=20) == 0
            assert wait_until(lambda: not _alive(identity), timeout=10)
        finally:
            (tmp_path / 'publish').touch()
            (tmp_path / 'stop').touch()
            for proc in processes:
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()  # Only a helper created by this test.
                    proc.wait(timeout=10)
            for path in tmp_path.glob('*-child.json'):
                identity = json.loads(path.read_text())
                if _alive(identity):
                    proc = psutil.Process(identity['pid'])
                    if proc.create_time() == identity['created']:
                        proc.kill()
                        proc.wait(timeout=10)

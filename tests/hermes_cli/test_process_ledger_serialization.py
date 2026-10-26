"""Two real local registrants remain live while their shared ledger writes serialize."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_two_live_registrants_both_survive(tmp_path, monkeypatch):
    from hermes_cli.process_ledger_lock import ledger_gate
    from pm.lifetime_launch import popen
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    code = '''
import sys
from hermes_cli import process_identity
import pm.filesystem
original = pm.filesystem.lock_fd
def observed(fd, **kwargs):
    print('locking', flush=True)
    return original(fd, **kwargs)
pm.filesystem.lock_fd = observed
print('ready', flush=True)
sys.stdin.read(1)
print(process_identity.register_self('interactive'), flush=True)
sys.stdin.read(1)
'''
    options = dict(cwd=Path(__file__).resolve().parents[2], env=dict(os.environ),
                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    with popen([sys.executable, '-c', code], **options) as first, popen([sys.executable, '-c', code], **options) as second:
        assert first.stdout.readline().strip() == 'ready'
        assert second.stdout.readline().strip() == 'ready'
        with ledger_gate():
            for child in (first, second):
                child.stdin.write('s')
                child.stdin.flush()
            assert first.stdout.readline().strip() == 'locking'
            assert second.stdout.readline().strip() == 'locking'
        assert first.stdout.readline().strip() == 'True'
        assert second.stdout.readline().strip() == 'True'
        entries = json.loads((tmp_path / 'spawn-ledger.json').read_bytes())
        assert {entry['pid'] for entry in entries} == {first.pid, second.pid}
        for child in (first, second):
            out, err = child.communicate('q', timeout=10)
            assert child.returncode == 0, (out, err)

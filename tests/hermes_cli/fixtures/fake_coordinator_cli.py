"""Pinned inert child: no accounts, model, network or external executable."""
import json
import os
from pathlib import Path
import select
import signal
import sys
import time

value = json.load(sys.stdin)
behavior = sys.argv[1]
if value.get('fixture') is not True:
    raise SystemExit(90)
if behavior == 'hold':
    os.mkfifo('release', 0o600)
    Path('ready').write_bytes(b'ready')
    with open('release', 'rb') as stream:
        stream.read(2)
if behavior == 'ignore_term':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    select.select([], [], [], 60)
if behavior == 'overflow':
    os.write(1, b'x' * 131072)
    raise SystemExit(0)
if behavior == 'descendant':
    deadline = time.monotonic() + 12
    read_end, write_end = os.pipe()
    parent_read, parent_write = os.pipe()
    if os.fork() == 0:
        import fcntl
        os.close(read_end)
        os.close(parent_write)
        try:
            lock = os.open('descendant-live.lock', os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            os.mkfifo('descendant-release', 0o600)
            release = os.open('descendant-release', os.O_RDWR | os.O_NONBLOCK)
            Path('descendant-ready').write_bytes(b'ready')
            os.write(write_end, b'R')
            os.close(write_end)
            parent_closed, _, _ = select.select([parent_read], [], [], max(0, deadline - time.monotonic()))
            if not parent_closed or os.read(parent_read, 1) != b'':
                Path('descendant-done.tmp').write_bytes(b'expired')
                os.replace('descendant-done.tmp', 'descendant-done')
                os._exit(0)
            os.close(parent_read)
            Path('descendant-parent-pipe-closed').write_bytes(b'closed')
            released, _, _ = select.select([release], [], [], max(0, deadline - time.monotonic()))
            value = os.read(release, 1) if released else b''
            Path('descendant-done.tmp').write_bytes(b'released' if value == b'R' else b'expired')
            os.replace('descendant-done.tmp', 'descendant-done')
            os.close(release)
        except BrokenPipeError:
            pass
        finally:
            os._exit(0)
    os.close(write_end)
    os.close(parent_read)
    if os.read(read_end, 1) != b'R':
        os._exit(91)
    os.close(read_end)
    os._exit(0)
if behavior == 'fail':
    raise SystemExit(7)
if behavior == 'malformed':
    print('not a receipt')
    raise SystemExit(0)
result = {key: value[key] for key in ('task_id', 'run_id', 'key', 'payload_sha256')}
result.update(acknowledgment='accepted', kind='question' if behavior == 'question' else 'completed',
              summary='Synthetic result: 2 + 2 = 4', findings=['inert child'], questions=[])
if behavior == 'wrong_binding':
    result['key'] = 'wrong-key'
print(json.dumps({'is_error': False, 'modelUsage': {'claude-sonnet-fixture': {
    'provider': 'other' if behavior == 'provider' else 'firstParty'}},
    'usage': {'fallback_credit': 1 if behavior == 'fallback' else None}, 'result': json.dumps(result)}))

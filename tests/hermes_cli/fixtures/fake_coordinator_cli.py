"""Pinned inert child: no accounts, model, network or external executable."""
import json
import os
from pathlib import Path
import select
import signal
import sys

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
    read_end, write_end = os.pipe()
    if os.fork() == 0:
        os.close(write_end)
        os.read(read_end, 1)
        os.close(read_end)
        try:
            while True:
                os.write(1, b'x' * 4096)
        except BrokenPipeError:
            os._exit(0)
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

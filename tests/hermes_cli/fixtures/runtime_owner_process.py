"""Disposable Python router fixture; no model engine, network listener or provider call."""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import psutil

from hermes_cli.local_runtime import binaries, bootstrap, endpoint, supervisor
from hermes_cli.local_runtime.ownership import RuntimeOwner
from hermes_constants import get_hermes_home
from tests.waiting import wait_until

root, role = Path(sys.argv[1]), sys.argv[2]
real_supervisor = supervisor.LlamaServerSupervisor
bootstrap._generate_presets = lambda *args, **kwargs: None
bootstrap._admitted_models_max = lambda directory, count, engine_tag=None: count
bootstrap._start_idle_sweeper = lambda sup: None
supervisor._stable_port = lambda: 12345
def fixture_engine(backend):
    if sys.platform == 'darwin':
        from hermes_cli.local_runtime.generation_evidence import reserve_for_engine_probe

        reserve_for_engine_probe()
    return binaries.Engine(backend, 'fixture', Path(sys.executable))


binaries.installed_engine = fixture_engine


def write(name, value):
    path = root / name
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


class PythonRouter(real_supervisor):
    def _spawn(self):
        from hermes_cli.local_runtime.processes import spawn_server

        if os.name != 'nt':
            if self._generation_ticket is not None:
                from hermes_cli.local_runtime import generation_evidence, reconciliation

                self._spawn_intent = generation_evidence.publish_intent(
                    supervisor.state_path().parent, self._generation_ticket,
                    reconciliation._native_identity, reconciliation._volume_identity)
            else:
                from hermes_cli.local_runtime.tree_custody import reserve_uncontained_tree

                self._spawn_intent = reserve_uncontained_tree(supervisor.state_path())
        self.proc, self._job = spawn_server(
            [sys.executable, '-c', 'import threading; threading.Event().wait(90)'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc = psutil.Process(self.proc.pid)
        write(f'{role}-child.json', {'pid': proc.pid, 'created': proc.create_time()})
        assert wait_until((root / 'publish').exists, timeout=20)
        self._write_state()

    def _wait_health(self, timeout_s, *, cancel_event=None):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError('fixture boot cancelled')


supervisor.LlamaServerSupervisor = PythonRouter
if role == 'contender':
    lock_fd = bootstrap._try_lock_boot_fd

    def record_contention(fd):
        acquired = lock_fd(fd)
        if not acquired:
            (root / 'contended').touch()
        return acquired

    bootstrap._try_lock_boot_fd = record_contention

owner = RuntimeOwner(get_hermes_home())
try:
    config = {'local_runtime': {'enabled': True, 'backend': 'cpu', 'models_max': 1}}
    if role == 'consumer':
        owned = bootstrap.ensure_local_runtime(config, force=True)
    else:
        owned = bootstrap.ensure_local_runtime(config, owner=owner)
    resolved = endpoint.resolve_llamacpp_endpoint(config, wait_for_boot_s=0)
    write(f'{role}-result.json', {'owned': owned is not None, 'adopted': resolved is not None})
    if role == 'owner':
        assert wait_until((root / 'stop').exists, timeout=60)
finally:
    owner.close()
    bootstrap.shutdown_local_runtime()

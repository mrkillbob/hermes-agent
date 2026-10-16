"""Lease maintenance and lossless transport for fleet executions (stdlib only)."""
from __future__ import annotations

import json
import contextvars
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time


def stream_event(stdout, kind, subtype=None):
    found = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get('type') == kind and (subtype is None or event.get('subtype') == subtype):
            found = event
    return found


class ClaimGuard:
    """Keep claim and capability liveness independent of inference latency."""
    def __init__(self, runner, claim, now, interval):
        self.runner, self.claim = runner, claim
        self.origin = time.monotonic()
        self.wall_origin = time.time() if now is None else now
        self.interval = interval
        self.done = threading.Event()
        self.cancel = threading.Event()
        self.thread = None
        self.error = ''

    def now(self):
        return self.wall_origin + time.monotonic() - self.origin

    def refresh(self):
        try:
            if self.runner.stopped or not self.runner._is_live():
                self.error = 'owning Hermes stopped'
                self.cancel.set()
                return
            claim = self.runner.coordinator.renew_claim(self.claim.task_id, self.claim.claim_id, now=self.now())
            if claim is None:
                self.error = 'claim renewal refused'
                self.cancel.set()
                return
            for capability in self.runner.capabilities:
                if not self.runner.coordinator.heartbeat_runner(capability, now=self.now(), ttl=self.runner._runner_ttl):
                    self.error = 'runner heartbeat refused'
                    self.cancel.set()
                    return
        except (OSError, RuntimeError, ValueError) as exc:
            self.error = f'claim maintenance unavailable ({type(exc).__name__})'
            self.cancel.set()

    def _run(self):
        while not self.done.wait(self.interval) and not self.cancel.is_set():
            self.refresh()

    def __enter__(self):
        self.refresh()
        self.thread = threading.Thread(target=contextvars.copy_context().run, args=(self._run,), name='fleet-claim', daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.done.set()
        self.thread.join()


def execute_transport(argv, input_text, *, cancel=None, output_dir=None, env=None):
    """Wait for process settlement, never a fixed task duration. Files avoid pipe-EOF hangs.

    The owner may outlive this CLI. A killed transport must be reconciled separately.
    With output_dir the capture survives a runner crash for exact-admission recovery.
    """
    from hermes_cli._subprocess_compat import _close_job, kill_process_tree, windows_hide_flags
    from hermes_cli.local_runtime.processes import spawn_server

    if output_dir:
        Path(output_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
    files = []
    job = None
    try:
        for label in ('input', 'stdout', 'stderr'):
            files.append(tempfile.NamedTemporaryFile(mode='w+b', prefix=label + '-', dir=output_dir,
                                                    delete=not bool(output_dir) or label == 'input'))
        source, stdout, stderr = files
        source.write(input_text.encode('utf-8'))
        source.flush()
        source.seek(0)
        try:
            proc, job = spawn_server(argv, stdin=source, stdout=stdout, stderr=stderr,
                                    env={**(os.environ if env is None else env), 'PYTHONIOENCODING': 'utf-8'},
                                    **({'creationflags': windows_hide_flags()} if os.name == 'nt' else {'process_group': 0}))
        except OSError as exc:
            result = subprocess.CompletedProcess(argv, 127, '', f'Fleet launcher did not start ({type(exc).__name__})')
            result.not_started = True
            return result
        interrupted = False
        while True:
            try:
                proc.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    interrupted = True
                    kill_process_tree(proc)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    break
        for handle in (stdout, stderr):
            handle.flush()
            handle.seek(0)
        result = subprocess.CompletedProcess(argv, proc.returncode if proc.returncode is not None else 75,
                                             stdout.read().decode('utf-8', 'replace'), stderr.read().decode('utf-8', 'replace'))
        result.transport_interrupted = interrupted
        return result
    finally:
        _close_job(job)
        for handle in files:
            handle.close()


def profile_child_env(profile):
    """Use the same served-profile environment for execution and reconciliation."""
    from hermes_cli.profiles import resolve_profile_env
    from tools.environments.local import restore_managed_env, served_profile_child_env

    env = served_profile_child_env(target_home=resolve_profile_env(profile), inherit_credentials=True)
    env['PYTHONIOENCODING'] = 'utf-8'
    return restore_managed_env(env)

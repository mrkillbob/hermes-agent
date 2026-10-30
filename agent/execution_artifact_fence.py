"""Local per-task ownership fences; no distributed authority or runtime callers."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from concurrent.futures import Future
from hashlib import sha256
import json
import os
import sys
from pathlib import Path
from threading import Event
from agent.memory_provider import spawn_context_thread

from agent.cross_process_file_lock import exclusive_file_lock
from agent.execution_artifact_contracts import ArtifactDenied, TaskIdentity

_HELD = ContextVar("artifact_sync_fences", default=())
_ASYNC_HELD = ContextVar("artifact_async_fences", default=False)


def task_key(identity: TaskIdentity) -> str:
    return sha256(json.dumps([identity.node, identity.profile, identity.task]).encode()).hexdigest()


class TaskFences:
    """Stable node/profile/task namespace in a private local ledger directory.

    Mutators enter after existing async gates, before event snapshots or writers.
    Dispatch never acquires async gates. No callback may reenter a held fence.
    Network filesystems and independently mutating authorities are unqualified.
    """
    def __init__(self, ledger_path: Path):
        self._path = Path(ledger_path).absolute()

    @staticmethod
    def check_entry():
        if _HELD.get() or _ASYNC_HELD.get():
            raise ArtifactDenied("artifact_fence_reentry")

    @contextmanager
    def scope(self, identity: TaskIdentity):
        # Late import keeps private-file policy shared with the ledger.
        from agent.execution_artifact_ledger import _private_file
        key = (str(self._path), identity.node, identity.profile, identity.task)
        held = _HELD.get()
        if key in held or _ASYNC_HELD.get():
            raise ArtifactDenied("artifact_fence_reentry")
        if held and key <= max(held):
            raise ArtifactDenied("artifact_fence_lock_order")
        path = self._path.with_name(self._path.name + ".task-" + task_key(identity) + ".lock")
        _private_file(path)
        with exclusive_file_lock(path):
            token = _HELD.set((*held, key))
            try:
                yield
            finally:
                _HELD.reset(token)

    @asynccontextmanager
    async def async_scope(self, identity: TaskIdentity, *, before_handoff=None):
        """Worker owns acquisition AND release; cancellation never abandons a lease.

        Nonblocking lock attempts allow a cancelled waiter to stop without waiting
        for the current holder. Worker retains the lease through acknowledged
        handoff and until scope exit; repeated cancellation still joins cleanup.
        """
        from agent.execution_artifact_ledger import _private_file
        if _ASYNC_HELD.get() or _HELD.get():
            raise ArtifactDenied("artifact_fence_reentry")
        if os.name != "posix":
            raise ArtifactDenied("artifact_fence_platform_unqualified")
        import fcntl
        path = self._path.with_name(self._path.name + ".task-" + task_key(identity) + ".lock")
        ready, stop = Future(), Event()

        def worker():
            fd, worker_token = None, None
            try:
                _private_file(path)
                fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
                while not stop.is_set():
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        stop.wait(0.01)
                        continue
                    worker_token = _HELD.set(((str(self._path), identity.node, identity.profile, identity.task),))
                    if before_handoff is not None:
                        before_handoff()
                    ready.set_result(True)
                    stop.wait()
                    return
            except BaseException as error:
                if not ready.done():
                    ready.set_exception(error)
                raise
            finally:
                if worker_token is not None:
                    _HELD.reset(worker_token)
                if fd is not None:
                    os.close(fd)

        completed = Future()
        def own_lease():
            try:
                worker()
            except BaseException as error:  # health: allow BLE001 -- Transfer worker failure to the awaited future; it is re-raised on the owner loop.
                completed.set_exception(error)
            else:
                completed.set_result(None)
        spawn_context_thread(own_lease, name="artifact-task-fence").start()
        pending = asyncio.wrap_future(completed)
        handoff = asyncio.wrap_future(ready)
        token = None
        try:
            await asyncio.shield(handoff)
            token = _ASYNC_HELD.set(True)
            yield
        finally:
            cancelled = isinstance(sys.exception(), asyncio.CancelledError)
            if token is not None:
                _ASYNC_HELD.reset(token)
            stop.set()
            while not pending.done():
                try:
                    await asyncio.wait({pending})
                except asyncio.CancelledError:
                    cancelled = True
            # Observe acquisition errors even if cancellation won the handoff.
            if handoff.done() and not handoff.cancelled():
                handoff.exception()
            error = pending.exception()
            if cancelled:
                raise asyncio.CancelledError from None
            if error is not None:
                raise error

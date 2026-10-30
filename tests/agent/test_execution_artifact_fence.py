"""Deterministic local fence tests; no runtime services or result disclosure."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from agent.execution_artifact_contracts import ArtifactDenied
from agent.execution_artifact_ledger import DurableArtifactAuthority
from tests.agent.test_execution_artifacts import Authority, IDENTITY, INVOCATION, RECIPIENT


@pytest.mark.platforms("posix")
def test_admission_snapshot_is_outside_writer_transaction(tmp_path):
    import sqlite3
    path = tmp_path / "state.db"
    snapshots = []

    def check(_):
        with sqlite3.connect(path, timeout=0) as db:
            db.execute("BEGIN IMMEDIATE")
            db.rollback()
        snapshots.append(True)
        return True

    ledger = DurableArtifactAuthority(path, enabled=True, admission_check=check, clock=lambda: 100.)
    ledger.record_approval(Authority().approval)
    approval = ledger.approve(IDENTITY, INVOCATION, RECIPIENT, "request")
    assert ledger.current(approval)
    with ledger.dispatch_scope(approval, 10):
        assert len(snapshots) >= 3


@pytest.mark.platforms("posix")
def test_slow_send_does_not_block_another_task(tmp_path):
    ledger = DurableArtifactAuthority(tmp_path / "state.db", enabled=True,
                                     admission_check=lambda _: True, clock=lambda: 100.)
    first = Authority().approval
    second = replace(first, identity=replace(IDENTITY, task="second"), approval_id="second",
                     invocation_id="second", request_id="second")
    for approval in (first, second):
        ledger.record_approval(approval)
        ledger.approve(approval.identity, INVOCATION, RECIPIENT, approval.request_id)
    entered, release = Event(), Event()

    def send():
        with ledger.dispatch_scope(first, 10):
            entered.set()
            assert release.wait(10)

    with ThreadPoolExecutor(2) as pool:
        pending = pool.submit(send)
        try:
            assert entered.wait(10)
            other = pool.submit(ledger.revoke, second.approval_id)
            other.result(10)
            assert not ledger.current(second)
        finally:
            release.set()
        pending.result(10)


@pytest.mark.platforms("posix")
def test_async_wait_cancel_has_no_late_lease_and_loop_stays_responsive(tmp_path):
    from agent.execution_artifact_fence import TaskFences
    fences = TaskFences(tmp_path / "state.db")
    held, release = Event(), Event()

    def owner():
        with fences.scope(IDENTITY):
            held.set()
            assert release.wait(10)

    async def run():
        async def mutation():
            async with fences.async_scope(IDENTITY):
                pytest.fail("cancelled mutation entered")
        task = asyncio.create_task(mutation())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # A task with another identity can be acquired while the first waits.
        async with fences.async_scope(replace(IDENTITY, task="other")):
            pass

    with ThreadPoolExecutor(1) as pool:
        worker = pool.submit(owner)
        try:
            assert held.wait(10)
            asyncio.run(run())
        finally:
            release.set()
        worker.result(10)
    with fences.scope(IDENTITY):
        pass


@pytest.mark.platforms("posix")
def test_same_thread_sink_reentry_is_refused_before_lock(tmp_path):
    from agent.execution_artifact_fence import TaskFences
    fences = TaskFences(tmp_path / "state.db")
    with fences.scope(IDENTITY):
        with pytest.raises(ArtifactDenied, match="reentry"):
            with fences.scope(IDENTITY):
                pytest.fail("reentry admitted")


@pytest.mark.platforms("posix")
def test_quarantine_survives_restart_and_blocks_revocation_acknowledgment(tmp_path):
    path = tmp_path / "state.db"
    ledger = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    approval = Authority().approval
    ledger.record_approval(approval)
    ledger.quarantine(IDENTITY)
    reopened = DurableArtifactAuthority(path, enabled=True, admission_check=lambda _: True, clock=lambda: 100.)
    with pytest.raises(ArtifactDenied, match="quarantined"):
        reopened.approve(IDENTITY, INVOCATION, RECIPIENT, "request")
    with pytest.raises(ArtifactDenied, match="quarantined"):
        reopened.revoke(approval.approval_id)
    with pytest.raises(ArtifactDenied, match="quarantined"):
        with reopened.mutation_scope(IDENTITY):
            pytest.fail("quarantined owner reassigned")


@pytest.mark.platforms("posix")
def test_mutation_wins_then_stale_dispatch_sends_nothing(tmp_path):
    ledger = DurableArtifactAuthority(tmp_path / "state.db", enabled=True,
                                     admission_check=lambda _: True, clock=lambda: 100.)
    approval = Authority().approval
    ledger.record_approval(approval)
    ledger.approve(IDENTITY, INVOCATION, RECIPIENT, "request")
    ledger.revoke(approval.approval_id)
    with pytest.raises(ArtifactDenied):
        with ledger.dispatch_scope(approval, 10):
            pytest.fail("revoked result sent")


@pytest.mark.platforms("posix")
def test_async_handoff_cancellation_after_adoption_releases(tmp_path):
    from agent.execution_artifact_fence import TaskFences
    fences = TaskFences(tmp_path / "state.db")

    async def run():
        adopted = asyncio.Event()
        async def mutation():
            async with fences.async_scope(IDENTITY):
                adopted.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(mutation())
        await asyncio.wait_for(adopted.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with fences.async_scope(IDENTITY):
            pass
    asyncio.run(run())


@pytest.mark.platforms("posix")
def test_registry_sink_reentry_refuses_and_turn_cleanup_orders_after_send(tmp_path):
    from agent.execution_artifacts import ExecutionArtifactRegistry
    from tests.agent.test_execution_artifacts import Executor, Sink
    ledger = DurableArtifactAuthority(tmp_path / "state.db", enabled=True,
                                     admission_check=lambda _: True, clock=lambda: 100.)
    ledger.record_approval(Authority().approval)
    registry = ExecutionArtifactRegistry(enabled=True, authority=ledger, executor=Executor(), clock=lambda: 100.)
    ref = registry.produce(IDENTITY, INVOCATION, RECIPIENT, request_id="request")
    entered, release, cleanup_attempted = Event(), Event(), Event()
    order = []
    class HeldSink(Sink):
        def send(self, request_id, payload):
            with pytest.raises(ArtifactDenied, match="reentry"):
                registry.clear_request(request_id)
            with pytest.raises(ArtifactDenied, match="reentry"):
                ledger.revoke("approval")
            order.append("send")
            entered.set()
            assert release.wait(10)
            super().send(request_id, payload)
            order.append("settled")
    sink = HeldSink()
    def clear():
        cleanup_attempted.set()
        registry.clear_turn(IDENTITY.turn)
        order.append("cleared")
    with ThreadPoolExecutor(2) as pool:
        sender = pool.submit(registry.dispatch, ref, identity=IDENTITY, invocation=INVOCATION,
                             request_id="request", sink=sink)
        try:
            assert entered.wait(10)
            closer = pool.submit(clear)
            assert cleanup_attempted.wait(10)
        finally:
            release.set()
        sender.result(10)
        closer.result(10)
    assert order == ["send", "settled", "cleared"]
    assert len(sink.sent) == 1


@pytest.mark.platforms("posix")
def test_async_mutation_progresses_with_single_default_executor_worker(tmp_path):
    ledger = DurableArtifactAuthority(tmp_path / "state.db", enabled=True,
                                     admission_check=lambda _: True, clock=lambda: 100.)
    async def run():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(1))
        async def mutation():
            async with ledger.async_mutation_scope(IDENTITY):
                return "entered"
        assert await asyncio.wait_for(mutation(), 10) == "entered"
    asyncio.run(run())


@pytest.mark.platforms("posix")
def test_cancellation_between_worker_acquisition_and_coroutine_adoption(tmp_path, monkeypatch):
    from agent.execution_artifact_fence import TaskFences
    fences = TaskFences(tmp_path / "state.db")
    original_wrap = asyncio.wrap_future
    async def run():
        loop = asyncio.get_running_loop()
        holder = {}
        acquired = Event()
        def wrap(future, *args, **kwargs):
            def before_handoff(done):
                if done.exception() is None and done.result() is True:
                    acquired.set()
                    loop.call_soon_threadsafe(holder["task"].cancel)
            # Run cancellation before asyncio adopts the worker's ready result.
            future.add_done_callback(before_handoff)
            return original_wrap(future, *args, **kwargs)
        monkeypatch.setattr(asyncio, "wrap_future", wrap)
        async def mutation():
            async with fences.async_scope(IDENTITY):
                pytest.fail("cancelled handoff adopted")
        holder["task"] = asyncio.create_task(mutation())
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(holder["task"], 10)
        assert acquired.is_set()
        monkeypatch.setattr(asyncio, "wrap_future", original_wrap)
        async with fences.async_scope(IDENTITY):
            pass
    asyncio.run(run())


@pytest.mark.platforms("posix")
def test_stalled_writer_does_not_hold_event_snapshot_lock(tmp_path):
    import sqlite3
    from threading import RLock
    event_lock, snapshotted, writer_locked = RLock(), Event(), Event()
    state = {"stall": False}
    def admission(_):
        with event_lock:
            snapshotted.set()
        if state["stall"]:
            assert writer_locked.wait(10)
        return True
    path = tmp_path / "state.db"
    ledger = DurableArtifactAuthority(path, enabled=True, admission_check=admission, clock=lambda: 100.)
    approval = Authority().approval
    ledger.record_approval(approval)
    ledger.approve(IDENTITY, INVOCATION, RECIPIENT, "request")
    snapshotted.clear()
    state["stall"] = True
    def dispatch():
        with ledger.dispatch_scope(approval, 10):
            return "consumed"
    with ThreadPoolExecutor(1) as pool, sqlite3.connect(path, timeout=0) as writer:
        future = pool.submit(dispatch)
        try:
            assert snapshotted.wait(10)
            writer.execute("BEGIN IMMEDIATE")
            writer_locked.set()
            with event_lock:
                observed = "owner loop responsive"
        finally:
            writer.rollback()
            writer_locked.set()
        assert future.result(10) == "consumed"
    assert observed == "owner loop responsive"


@pytest.mark.platforms("posix")
def test_cancellation_during_normal_exit_join_is_preserved(tmp_path, monkeypatch):
    from agent.execution_artifact_fence import TaskFences
    fences = TaskFences(tmp_path / "state.db")
    original_wrap = asyncio.wrap_future
    async def run():
        loop, holder = asyncio.get_running_loop(), {}
        def wrap(future, *args, **kwargs):
            def cancel_at_completion(done):
                if done.exception() is None and done.result() is None:
                    loop.call_soon_threadsafe(holder["task"].cancel)
            future.add_done_callback(cancel_at_completion)
            return original_wrap(future, *args, **kwargs)
        monkeypatch.setattr(asyncio, "wrap_future", wrap)
        async def mutation():
            async with fences.async_scope(IDENTITY):
                pass
        holder["task"] = asyncio.create_task(mutation())
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(holder["task"], 10)
        monkeypatch.setattr(asyncio, "wrap_future", original_wrap)
        async with fences.async_scope(IDENTITY):
            pass
    asyncio.run(run())


@pytest.mark.platforms("posix")
def test_quarantine_does_not_prevent_registry_payload_cleanup(tmp_path):
    from agent.execution_artifacts import ExecutionArtifactRegistry
    from tests.agent.test_execution_artifacts import Executor, Sink
    ledger = DurableArtifactAuthority(tmp_path / "state.db", enabled=True,
                                     admission_check=lambda _: True, clock=lambda: 100.)
    ledger.record_approval(Authority().approval)
    registry = ExecutionArtifactRegistry(enabled=True, authority=ledger, executor=Executor(), clock=lambda: 100.)
    ref = registry.produce(IDENTITY, INVOCATION, RECIPIENT, request_id="request")
    ledger.quarantine(IDENTITY)
    registry.clear_request("request")
    with pytest.raises(ArtifactDenied, match="unknown"):
        registry.dispatch(ref, identity=IDENTITY, invocation=INVOCATION, request_id="request", sink=Sink())
    with pytest.raises(ArtifactDenied, match="quarantined"):
        ledger.revoke("approval")


@pytest.mark.platforms("posix")
def test_inherited_callback_context_refuses_cross_thread_reentry(tmp_path):
    from agent.execution_artifact_fence import TaskFences
    from agent.memory_provider import spawn_context_thread
    fences = TaskFences(tmp_path / "state.db")
    outcomes = []
    with fences.scope(IDENTITY):
        def callback():
            try:
                with fences.scope(IDENTITY):
                    outcomes.append("entered")
            except ArtifactDenied:
                outcomes.append("refused")
        thread = spawn_context_thread(callback, name="synthetic-reentry")
        thread.start()
        thread.join(10)
        assert not thread.is_alive()
    assert outcomes == ["refused"]


@pytest.mark.platforms("posix")
def test_reverse_multi_task_fence_order_is_refused(tmp_path):
    from agent.execution_artifact_fence import TaskFences
    fences = TaskFences(tmp_path / "state.db")
    with fences.scope(replace(IDENTITY, task="z")):
        with pytest.raises(ArtifactDenied, match="lock_order"):
            with fences.scope(replace(IDENTITY, task="a")):
                pytest.fail("reverse task ordering admitted")


@pytest.mark.platforms("posix")
def test_async_mutation_with_pool_threads_contending_for_its_fence(tmp_path, monkeypatch):
    from agent.execution_artifact_fence import TaskFences
    ledger = DurableArtifactAuthority(tmp_path / "state.db", enabled=True,
                                     admission_check=lambda _: True, clock=lambda: 100.)
    fences = TaskFences(tmp_path / "state.db")
    original_wrap, started, contenders = asyncio.wrap_future, Event(), []
    async def run():
        loop = asyncio.get_running_loop()
        pool = ThreadPoolExecutor(1)
        loop.set_default_executor(pool)
        def contend():
            started.set()
            with fences.scope(IDENTITY):
                return "other mutation"
        def wrap(future, *args, **kwargs):
            def hold_shared_pool(done):
                if done.exception() is None and done.result() is True:
                    contenders.append(pool.submit(contend))
                    assert started.wait(10)
            future.add_done_callback(hold_shared_pool)
            return original_wrap(future, *args, **kwargs)
        monkeypatch.setattr(asyncio, "wrap_future", wrap)
        async def mutate():
            async with ledger.async_mutation_scope(IDENTITY):
                return "entered"
        assert await asyncio.wait_for(mutate(), 10) == "entered"
        monkeypatch.setattr(asyncio, "wrap_future", original_wrap)
    asyncio.run(run())
    assert contenders[0].result(10) == "other mutation"


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt, ArtifactDenied])
def test_cleanup_acquisition_failure_discards_payload_and_file_grants(tmp_path, failure):
    from contextlib import contextmanager
    from agent.execution_artifacts import ExecutionArtifactRegistry
    from tests.agent.test_execution_artifacts import Executor, Sink
    ledger = DurableArtifactAuthority(tmp_path / "state.db", enabled=True,
                                     admission_check=lambda _: True, clock=lambda: 100.)
    ledger.record_approval(Authority().approval)
    registry = ExecutionArtifactRegistry(enabled=True, authority=ledger, executor=Executor(), clock=lambda: 100.)
    ref = registry.produce(IDENTITY, INVOCATION, RECIPIENT, request_id="request")
    source = tmp_path / "report.txt"
    source.write_bytes(b"safe\n")
    registry.issue_file_slice(path=source, line_start=1, line_end=1, content=b"safe\n",
                              session_id=IDENTITY.session, turn_id=IDENTITY.turn,
                              request_id="request", policy_digest=IDENTITY.policy)
    assert registry.grants_for_request("request")
    @contextmanager
    def failed_scope(identity):
        raise failure("synthetic fence failure")
        yield
    ledger.cleanup_scope = failed_scope
    with pytest.raises(failure):
        registry.clear_request("request")
    assert registry.grants_for_request("request") == ()
    with pytest.raises(ArtifactDenied, match="unknown"):
        registry.dispatch(ref, identity=IDENTITY, invocation=INVOCATION, request_id="request", sink=Sink())


@pytest.mark.platforms("posix")
def test_unrelated_cleanup_cannot_forget_failed_cleanup_sender(tmp_path):
    from contextlib import contextmanager
    from agent.execution_artifacts import ExecutionArtifactRegistry
    from tests.agent.test_execution_artifacts import Executor, Sink
    ledger = DurableArtifactAuthority(tmp_path / "state.db", enabled=True,
                                     admission_check=lambda _: True, clock=lambda: 100.)
    first = Authority().approval
    second = replace(first, identity=replace(IDENTITY, task="second"), approval_id="second",
                     invocation_id="second", request_id="second")
    for approval in (first, second):
        ledger.record_approval(approval)
    executor = Executor()
    registry = ExecutionArtifactRegistry(enabled=True, authority=ledger, executor=executor, clock=lambda: 100.)
    registry.produce(IDENTITY, INVOCATION, RECIPIENT, request_id="request")
    executor.result = replace(executor.result, identity=second.identity)
    ref = registry.produce(second.identity, INVOCATION, RECIPIENT, request_id="second")
    entered, release, retry_waiting = Event(), Event(), Event()
    order, state = [], {"fail": True}
    original_scope = ledger.cleanup_scope
    @contextmanager
    def cleanup_scope(identity):
        if identity == second.identity:
            if state["fail"]:
                raise OSError("synthetic cleanup acquisition failure")
            retry_waiting.set()
        with original_scope(identity):
            yield
    ledger.cleanup_scope = cleanup_scope
    class HeldSink(Sink):
        def send(self, request_id, payload):
            order.append("send")
            entered.set()
            assert release.wait(10)
            order.append("settled")
    def retry():
        registry.clear_request("second")
        order.append("closed")
    with ThreadPoolExecutor(2) as pool:
        sender = pool.submit(registry.dispatch, ref, identity=second.identity, invocation=INVOCATION,
                             request_id="second", sink=HeldSink())
        try:
            assert entered.wait(10)
            with pytest.raises(OSError):
                registry.clear_request("second")
            registry.clear_request("request")
            state["fail"] = False
            closer = pool.submit(retry)
            assert retry_waiting.wait(10)
        finally:
            release.set()
        sender.result(10)
        closer.result(10)
    assert order == ["send", "settled", "closed"]

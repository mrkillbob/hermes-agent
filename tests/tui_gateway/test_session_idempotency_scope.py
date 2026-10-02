"""Creation retries stay within their profile, caller, operation and parent."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading

import pytest

from hermes_state import SessionDB
from tui_gateway import server
from tui_gateway.transport import bind_transport, reset_transport


class Peer:
    def __init__(self, user=None):
        self.auth_identity = {"provider": "basic", "user_id": user} if user else None

    def write(self, frame):
        return True


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    homes = {name: tmp_path / name for name in ("default", "a", "b")}
    for home in homes.values():
        home.mkdir()
        (home / "config.yaml").write_text("model:\n  default: test-model\n")
    db = SessionDB(homes["default"] / "state.db")
    monkeypatch.setenv("HERMES_HOME", str(homes["default"]))
    monkeypatch.setattr(server, "_hermes_home", homes["default"])
    monkeypatch.setattr(server, "get_process_hermes_home", lambda: homes["default"])
    monkeypatch.setattr(server, "_profile_home", lambda p: homes[{"alias-a": "a"}.get(p, p)] if p else None)
    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setattr(server, "_idempotency_keys", {})
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda sid: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda record: None)
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_project_info_for_cwd", lambda cwd: None)
    monkeypatch.setattr(server, "_resolve_model", lambda: "test-model")
    monkeypatch.setattr(server, "_make_agent", lambda *a, **kw: SimpleNamespace(model="test-model", tools=[], session_id=kw.get("session_id")))
    monkeypatch.setattr(server, "_claim_active_session_slot", lambda *a, **kw: (None, None))
    monkeypatch.setattr(server, "_attach_worker", lambda *a, **kw: None)
    monkeypatch.setattr(server, "_start_session_services", lambda *a, **kw: None)
    monkeypatch.setattr(server, "_schedule_mcp_late_refresh", lambda *a, **kw: None)
    monkeypatch.setattr(server, "_session_info", lambda *a: {"model": "test-model"})
    monkeypatch.setattr(server, "_fallback_session_info", lambda *a: {"model": "test-model"})
    yield homes
    for sid in list(server._sessions):
        server._close_session_by_id(sid, end_reason="test_cleanup")
    db.close()


def rpc(method="session.create", *, peer=None, **params):
    token = bind_transport(peer)
    try:
        return server.handle_request({"id": "test", "method": method, "params": {"source": "api", **params} if method in {"session.create", "session.branch_stored"} else params})
    finally:
        reset_transport(token)


def result(*args, **kwargs):
    response = rpc(*args, **kwargs)
    assert "result" in response, response
    return response["result"]


def parent(peer, text="parent", profile=None):
    created = result(peer=peer, profile=profile, messages=[{"role": "user", "content": text}])
    record = server._sessions[created["session_id"]]
    record["agent"] = SimpleNamespace(model="test-model", tools=[])
    record["agent_ready"].set()
    return created


def test_profiles_and_aliases_scope_retries(sandbox):
    peer = Peer("alice")
    a = result(peer=peer, profile="a", idempotency_key="same", messages=[{"role": "user", "content": "A private"}])
    b = result(peer=peer, profile="b", idempotency_key="same")
    assert b["session_id"] != a["session_id"]
    assert b["messages"] == []
    alias = result(peer=peer, profile="alias-a", idempotency_key="same")
    assert alias["session_id"] == a["session_id"]


def test_authenticated_caller_retries_across_peers_but_not_users(sandbox):
    a = result(peer=Peer("alice"), idempotency_key="same", messages=[{"role": "user", "content": "private"}])
    retry = result(peer=Peer("alice"), idempotency_key="same")
    b = result(peer=Peer("bob"), idempotency_key="same")
    assert retry["session_id"] == a["session_id"]
    assert b["session_id"] != a["session_id"]
    assert b["messages"] == []


def test_legacy_peer_authority_and_stdio_have_separate_buckets(sandbox):
    a, b = Peer(), Peer()
    first = result(peer=a, idempotency_key="same")
    assert result(peer=a, idempotency_key="same")["session_id"] == first["session_id"]
    assert result(peer=b, idempotency_key="same")["session_id"] != first["session_id"]
    stdio = result(idempotency_key="same")
    assert stdio["session_id"] != first["session_id"]
    assert result(idempotency_key="same")["session_id"] == stdio["session_id"]


def test_create_and_stored_branch_operations_and_parents_do_not_collide(sandbox):
    peer = Peer("alice")
    p1, p2 = parent(peer, "one"), parent(peer, "two")
    created = result(peer=peer, idempotency_key="same")
    one = result("session.branch_stored", peer=peer, parent_session_id=p1["stored_session_id"], idempotency_key="same")
    two = result("session.branch_stored", peer=peer, parent_session_id=p2["stored_session_id"], idempotency_key="same")
    assert len({created["session_id"], one["session_id"], two["session_id"]}) == 3
    retry = result("session.branch_stored", peer=peer, parent_session_id=p1["stored_session_id"], idempotency_key="same")
    assert retry["session_id"] == one["session_id"]
    assert retry["messages_omitted"] is True and "messages" not in retry
    alias = result("session.branch_stored", peer=peer, parent_session_id=p1["session_id"], idempotency_key="same")
    assert alias["session_id"] == one["session_id"]


def test_live_branch_operation_parent_and_caller_isolation(sandbox):
    peer = Peer("alice")
    p1, p2 = parent(peer, "one"), parent(peer, "two")
    one = result("session.branch", peer=peer, session_id=p1["session_id"], idempotency_key="same", name="One")
    retry = result("session.branch", peer=Peer("alice"), session_id=p1["session_id"], idempotency_key="same", name="One")
    two = result("session.branch", peer=peer, session_id=p2["session_id"], idempotency_key="same")
    whole = result("session.branch_whole", peer=peer, session_id=p1["session_id"], idempotency_key="same")
    bob = result("session.branch", peer=Peer("bob"), session_id=p1["session_id"], idempotency_key="same")
    assert retry["session_id"] == one["session_id"] and retry["title"] == "One"
    assert len({one["session_id"], two["session_id"], whole["session_id"], bob["session_id"]}) == 4
    assert two["parent"] == p2["stored_session_id"]
    assert whole["messages_omitted"] and "messages" not in whole
    assert server._session_auth_user_id(server._sessions[bob["session_id"]]) == "basic:alice"


@pytest.mark.parametrize("field,value", [("profile_home", "foreign"), ("auth_user_id", "basic:bob"), ("parent_session_id", "foreign")])
def test_invalid_cached_record_is_not_replayed(sandbox, field, value):
    peer = Peer("alice")
    first = result(peer=peer, idempotency_key="same")
    server._sessions[first["session_id"]][field] = str(sandbox["b"]) if field == "profile_home" else value
    assert result(peer=peer, idempotency_key="same")["session_id"] != first["session_id"]


def test_concurrent_retry_waits_for_full_create_and_publishes_once(sandbox, monkeypatch):
    entered, release, second_started, second_done = (threading.Event() for _ in range(4))
    calls = []
    def finish(sid):
        calls.append(sid)
        entered.set()
        assert release.wait(3)
    monkeypatch.setattr(server, "_schedule_agent_build", finish)
    peer = Peer("alice")
    def second():
        second_started.set()
        try:
            return result(peer=peer, idempotency_key="same")
        finally:
            second_done.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(result, peer=peer, idempotency_key="same")
        assert entered.wait(3)
        retry = pool.submit(second)
        assert second_started.wait(3)
        early = second_done.wait(0.2)
        release.set()
        a, b = first.result(timeout=3), retry.result(timeout=3)
    assert not early, "retry exposed a creation whose first response was not complete"
    assert a["session_id"] == b["session_id"] and len(calls) == 1


def test_failed_first_create_is_not_cached_and_retry_can_complete(sandbox, monkeypatch):
    calls = []
    def finish(sid):
        calls.append(sid)
        if len(calls) == 1:
            raise RuntimeError("synthetic post-registration failure")
    monkeypatch.setattr(server, "_schedule_agent_build", finish)
    peer = Peer("alice")
    with pytest.raises(RuntimeError, match="synthetic post-registration failure"):
        rpc(peer=peer, idempotency_key="same")
    created = result(peer=peer, idempotency_key="same")
    assert len(calls) == 2
    assert calls[0] not in server._sessions
    assert created["session_id"] == calls[1]
    assert result(peer=peer, idempotency_key="same")["session_id"] == calls[1]


def test_live_branch_concurrent_misses_share_one_completed_child(sandbox, monkeypatch):
    peer = Peer("alice")
    source = parent(peer)
    entered, release, second_started, second_done = (threading.Event() for _ in range(4))
    calls = []
    persist = server._persist_branch
    def slow_persist(*args, **kwargs):
        calls.append(args[1])
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
        return persist(*args, **kwargs)
    monkeypatch.setattr(server, "_persist_branch", slow_persist)
    def branch():
        return result("session.branch", peer=peer, session_id=source["session_id"], idempotency_key="same")
    def second():
        second_started.set()
        try:
            return branch()
        finally:
            second_done.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(branch)
        assert entered.wait(3)
        retry = pool.submit(second)
        assert second_started.wait(3)
        early = second_done.wait(0.2)
        release.set()
        a, b = first.result(timeout=3), retry.result(timeout=3)
    assert not early
    assert a["session_id"] == b["session_id"] and len(calls) == 1
    assert not server._session_idempotency_pending


def test_branch_reply_failure_cleans_runtime_and_releases_retry(sandbox, monkeypatch):
    peer = Peer("alice")
    source = parent(peer)
    calls = []
    def info(agent, record):
        calls.append(record["session_key"])
        if len(calls) == 2:  # after the build's session.info emission, in the reply
            raise RuntimeError("synthetic branch reply failure")
        return {"model": "test-model"}
    monkeypatch.setattr(server, "_session_info", info)
    params = {"session_id": source["session_id"], "idempotency_key": "same"}
    with pytest.raises(RuntimeError, match="synthetic branch reply failure"):
        rpc("session.branch", peer=peer, **params)
    assert not server._idempotency_keys
    assert not server._session_idempotency_pending
    assert [r["session_key"] for r in server._sessions.values()] == [source["stored_session_id"]]
    child = result("session.branch", peer=peer, **params)
    assert len(calls) == 4 and child["stored_session_id"] == calls[3]
    assert result("session.branch", peer=peer, **params)["session_id"] == child["session_id"]


def test_live_branch_same_stored_parent_key_in_two_profiles_is_isolated(sandbox, monkeypatch):
    peer = Peer("alice")
    # Independent profile databases can contain identical durable parent ids.
    mint = server._new_session_key
    monkeypatch.setattr(server, "_new_session_key", lambda: "shared-parent")
    a, b = parent(peer, "A private", "a"), parent(peer, "B private", "b")
    monkeypatch.setattr(server, "_new_session_key", mint)
    first = result("session.branch", peer=peer, session_id=a["session_id"], idempotency_key="same")
    second = result("session.branch", peer=peer, session_id=b["session_id"], idempotency_key="same")
    assert first["session_id"] != second["session_id"]
    assert first["messages"][0]["text"] == "A private"
    assert second["messages"][0]["text"] == "B private"
    assert result("session.branch", peer=Peer("alice"), session_id=b["session_id"], idempotency_key="same")["session_id"] == second["session_id"]

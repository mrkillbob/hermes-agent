"""Fleet reconciles exact accepted admissions without replaying work."""
import json

import pytest


class Peer:
    def __init__(self, receipt, snapshot=None):
        self.receipt = receipt
        self.snapshot = snapshot or {}
        self.calls = []

    async def rpc(self, method, **params):
        self.calls.append((method, params))
        if method == "prompt.receipt":
            return self.receipt
        if method == "session.resume":
            return self.snapshot
        return {}


@pytest.mark.asyncio
async def test_completed_receipt_returns_committed_result(capsys):
    from hermes_cli.fleet_reconcile import reconcile_admission
    peer = Peer({"admission_id": "a", "status": "terminal", "outcome": "completed",
                 "result": {"final_response": "done", "input_tokens": 7}})
    assert await reconcile_admission(peer, "s", "a") == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["text"] == "done"
    assert result["tokens"]["input"] == 7
    assert result["settled"] is True
    assert result["admission_id"] == "a"
    assert result["admission_status"] == "terminal"
    assert [method for method, _ in peer.calls] == ["prompt.receipt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "started", "unknown"])
async def test_nonterminal_receipt_stays_uncertain(status, capsys):
    from hermes_cli.fleet_reconcile import reconcile_admission
    peer = Peer({"admission_id": "a", "status": status})
    assert await reconcile_admission(peer, "s", "a") == 75
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["settled"] is False
    assert result["exit_code"] == 75
    assert result["admission_status"] == status


@pytest.mark.asyncio
async def test_cancel_queued_to_started_race_interrupts_exact_generation():
    from hermes_cli.fleet_reconcile import reconcile_admission
    peer = Peer({"admission_id": "a", "status": "queued"})
    original = peer.rpc
    stopped = False
    post_interrupt_reads = 0

    async def rpc(method, **params):
        nonlocal stopped, post_interrupt_reads
        if method == "prompt.receipt" and stopped:
            post_interrupt_reads += 1
            if post_interrupt_reads == 2:
                peer.receipt = {"admission_id": "a", "status": "terminal", "outcome": "interrupted"}
        response = await original(method, **params)
        if method == "prompt.cancel":
            peer.receipt = {"admission_id": "a", "status": "started", "execution_generation": 9}
            peer.snapshot = {"execution_generation": 9, "pending": [dict(peer.receipt)]}
            return dict(peer.receipt)
        if method == "session.interrupt":
            stopped = True
        return response

    peer.rpc = rpc
    assert await reconcile_admission(peer, "s", "a", cancel=True, control_wait=2) == 130
    assert [(method, params) for method, params in peer.calls if method == "session.interrupt"] == [
        ("session.interrupt", {"session_id": "s", "execution_generation": 9})]


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt_generation, expected", [(4, True), (5, False)])
async def test_started_admission_reports_only_its_own_input_requirement(prompt_generation, expected, capsys):
    from hermes_cli.fleet_reconcile import reconcile_admission
    receipt = {"admission_id": "a", "status": "started", "execution_generation": 4}
    peer = Peer(receipt, {"execution_generation": 4, "pending": [dict(receipt)],
                          "prompts": [{"kind": "approval", "execution_generation": prompt_generation}]})
    assert await reconcile_admission(peer, "s", "a") == 75
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["needs_input"] is expected
    assert result["exit_code"] == 75
    assert result["admission_status"] == "started"


@pytest.mark.asyncio
@pytest.mark.parametrize("generation, admission", [(4, "other"), (5, "a")])
async def test_cancel_never_interrupts_a_different_generation(generation, admission):
    from hermes_cli.fleet_reconcile import reconcile_admission
    peer = Peer({"admission_id": "a", "status": "started", "execution_generation": 4},
                {"execution_generation": generation, "pending": [
                    {"admission_id": admission, "status": "started", "execution_generation": generation}]})
    assert await reconcile_admission(peer, "s", "a", cancel=True, control_wait=0) == 75
    assert not any(method == "session.interrupt" for method, _ in peer.calls)


@pytest.mark.asyncio
async def test_submit_emits_admission_receipt(capsys):
    from hermes_cli.gateway_chat_view import GatewayChatView
    from hermes_cli.stream_json import StreamJsonEmitter
    peer = Peer({})

    async def submit(method, **params):
        return {"admission_id": "a", "status": "queued"}

    peer.rpc = submit
    view = GatewayChatView(peer, {"stored_session_id": "s"}, emitter=StreamJsonEmitter())
    await view.submit("work")
    event = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert (event["type"], event["subtype"], event["session_id"], event["admission_id"]) == (
        "system", "admission", "s", "a")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "started"])
async def test_cancel_exact_admission_waits_for_terminal(status, capsys):
    from hermes_cli.fleet_reconcile import reconcile_admission
    receipt = {"admission_id": "a", "status": status, "execution_generation": 4}
    peer = Peer(receipt, {"execution_generation": 4, "pending": [dict(receipt)]})
    original = peer.rpc

    async def rpc(method, **params):
        response = await original(method, **params)
        if method in {"prompt.cancel", "session.interrupt"}:
            peer.receipt = {"admission_id": "a", "status": "terminal", "outcome": "cancelled"}
        return response

    peer.rpc = rpc
    assert await reconcile_admission(peer, "s", "a", cancel=True) == 130
    terminal = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert terminal["settled"] is True
    controls = [(method, params) for method, params in peer.calls if method != "prompt.receipt"]
    if status == "started":
        assert controls[-1] == ("session.interrupt", {"session_id": "s", "execution_generation": 4})
    else:
        assert controls == [("prompt.cancel", {"session_id": "s", "admission_id": "a"})]


@pytest.mark.asyncio
async def test_unready_gateway_is_never_started(monkeypatch, capsys):
    from types import SimpleNamespace
    from hermes_cli.fleet_reconcile import run_reconcile
    monkeypatch.setattr("hermes_cli.gateway_runtime.discover_gateway_endpoint",
                        lambda _home: SimpleNamespace(state="absent", endpoint=None))
    monkeypatch.setattr("hermes_cli.gateway_client.connect_gateway",
                        lambda **kw: pytest.fail("absent gateway must not be started"))
    assert await run_reconcile("s", "a") == 75
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["settled"] is False
    assert result["admission_status"] == "unknown"


@pytest.mark.asyncio
async def test_unavailable_receipt_reports_unknown_status(capsys):
    from hermes_cli.fleet_reconcile import reconcile_admission
    from hermes_cli.gateway_client import GatewayClientError

    class Unavailable:
        async def rpc(self, method, **params):
            raise GatewayClientError("disconnected")

    assert await reconcile_admission(Unavailable(), "s", "a") == 75
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["admission_status"] == "unknown"
    assert result["settled"] is False

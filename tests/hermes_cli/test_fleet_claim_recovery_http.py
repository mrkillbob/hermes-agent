"""Boundary tests use inert handler objects and fake authenticated transports."""

from types import SimpleNamespace

import pytest

from hermes_cli.fleet_client import FleetClient, FleetTransportError
from hermes_cli.fleet_server import _FleetHandler
from tests.hermes_cli.test_fleet_claim_recovery import setup_store, request, submit


@pytest.fixture(autouse=True)
def inert(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("live transport forbidden")

    monkeypatch.setattr("socket.socket", refuse)
    monkeypatch.setattr("subprocess.Popen", refuse)


def handler(store, *, authorized=True):
    instance = object.__new__(_FleetHandler)
    instance.server = SimpleNamespace(
        fleet_server=SimpleNamespace(store=store, token="fixture-token")
    )
    instance.headers = {"Authorization": "Bearer fixture-token" if authorized else "wrong"}
    instance.path = "/v2/fleet/claim-recovery/capabilities"
    instance.responses = []
    instance._respond = lambda status, body: instance.responses.append((int(status), body))
    return instance


def test_new_endpoints_require_existing_bearer_before_every_store_call(tmp_path):
    store, _capability, _info = setup_store(tmp_path)
    value = handler(store, authorized=False)
    value._handle_get()
    assert value.responses[-1][0] == 401
    value.path = "/v2/fleet/claim-recovery/resolve"
    value._json_body = lambda: (_ for _ in ()).throw(AssertionError("must authenticate first"))
    value._handle_post()
    assert value.responses[-1][0] == 401


def test_inert_handler_roundtrip_claim_lookup_resolve_and_explicit_cutover(tmp_path):
    store, capability, info = setup_store(tmp_path, cutover=False)
    value = handler(store)
    value._handle_get()
    assert value.responses[-1][1].get("version") == "hermes.fleet.claim-recovery.v1"
    value.path = "/v2/fleet/claim-recovery/cutover"
    value._json_body = lambda: {"expected_store_id": info["store_id"]}
    value._handle_post()
    assert value.responses[-1][0] == 200
    info = value.responses[-1][1]
    req = request(info, capability)
    submit(store, "a")
    value.path = "/v2/fleet/claim-recovery/claim"
    value._json_body = lambda: req.to_dict()
    value._handle_post()
    outcome = value.responses[-1][1]
    assert outcome["kind"] == "claimed"
    from urllib.parse import quote
    import json

    value.path = "/v2/fleet/claim-recovery/outcome?request=" + quote(
        json.dumps(req.to_dict()), safe=""
    )
    value._handle_get()
    assert value.responses[-1][1] == outcome
    value.path = "/v2/fleet/claim-recovery/resolve"
    value._handle_post()
    assert value.responses[-1][1] == outcome


def test_client_rejects_old_malformed_and_mismatched_outcomes_without_fallback(
    tmp_path, monkeypatch
):
    store, capability, info = setup_store(tmp_path)
    req = request(info, capability)
    client = FleetClient("http://fixture.invalid", token="fixture")
    method = getattr(client, "claim_request", None)
    assert callable(method), "new client must negotiate the protocol explicitly"
    for body in ({"claim": None}, {}, {"kind": "empty"}, {"kind": "unknown"}):
        monkeypatch.setattr(client, "_request", lambda *args, **kwargs: body)
        with pytest.raises((ValueError, FleetTransportError)):
            method(req)
    outcome = store.resolve_claim_request(req).to_dict()
    outcome["fingerprint"] = "0" * 64
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: outcome)
    with pytest.raises((ValueError, FleetTransportError)):
        method(req)


def test_legacy_missing_claim_never_becomes_empty(tmp_path, monkeypatch):
    _store, capability, _info = setup_store(tmp_path)
    client = FleetClient("http://fixture.invalid", token="fixture")
    for body in ({}, {"claim": []}, {"claim": "bad"}):
        monkeypatch.setattr(client, "_request", lambda *args, **kwargs: body)
        with pytest.raises((ValueError, FleetTransportError)):
            client.claim_task(capability)
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: {"claim": None})
    assert client.claim_task(capability) is None


def test_client_does_not_accept_absent_as_a_claim_or_seal_ack(tmp_path, monkeypatch):
    store, capability, info = setup_store(tmp_path)
    req = request(info, capability)
    absent = store.lookup_claim_request(req).to_dict()
    client = FleetClient("http://fixture.invalid", token="fixture")
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: absent)
    for method in (client.claim_request, client.resolve_claim_request):
        with pytest.raises(ValueError, match="absent"):
            method(req)
    assert client.lookup_claim_request(req).kind == "absent"


def test_new_protocol_requires_bounded_complete_response_framing(tmp_path, monkeypatch):
    import json
    from hermes_cli import fleet_client as module

    _store, _capability, info = setup_store(tmp_path)
    body = json.dumps(info).encode()
    client = FleetClient("http://fixture.invalid", token="fixture")

    class Response:
        def __init__(self, length):
            self.headers = {} if length is None else {"Content-Length": str(length)}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, *args):
            return body

    for length in (None, len(body) + 10, 2_000_000):
        monkeypatch.setattr(
            module, "open_credentialed_url", lambda *args, **kwargs: Response(length)
        )
        with pytest.raises(FleetTransportError):
            client.claim_recovery_capabilities()
    monkeypatch.setattr(
        module, "open_credentialed_url", lambda *args, **kwargs: Response(len(body))
    )
    assert client.claim_recovery_capabilities() == info


def test_request_size_is_bounded_for_get_reconciliation(tmp_path):
    from dataclasses import replace
    from hermes_cli.fleet_protocol import RunnerCapability

    _store, capability, info = setup_store(tmp_path)
    req = request(info, capability)
    huge = RunnerCapability("node", "profile", models=("x" * 20_000,), platform="posix")
    with pytest.raises(ValueError, match="large"):
        replace(req, capability=huge)

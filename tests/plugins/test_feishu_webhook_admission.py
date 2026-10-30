"""Bounded webhook authentication must precede trusted event admission."""

import asyncio
import json

import pytest

from tests.plugins.test_feishu_webhook_quota import make_adapter, make_request


@pytest.mark.parametrize("payload", [
    [], None, "text", {"header": []}, {"header": "invalid"}, {"header": 1},
    {"header": {"token": "test-token"}, "event": []},
    {"type": "url_verification", "token": "test-token", "challenge": {}},
])
def test_malformed_envelope_is_rejected_without_delivery(payload):
    adapter = make_adapter()
    response = asyncio.run(adapter._handle_webhook_request(make_request(payload)))
    assert response.status == 400
    assert adapter._webhook_rate_counts == {}
    adapter._on_message_event.assert_not_called()


@pytest.mark.parametrize("depth", [40, 1500])
def test_nested_json_has_controlled_rejection(depth):
    adapter = make_adapter()
    body = b'{"header":{"token":"test-token"},"event":' + b"[" * depth + b"0" + b"]" * depth + b"}"
    request = make_request({})
    request.content_length = len(body)
    request.content.readexactly.side_effect = asyncio.IncompleteReadError(body, len(body) + 1)
    assert asyncio.run(adapter._handle_webhook_request(request)).status == 400
    assert adapter._webhook_rate_counts == {}
    adapter._on_message_event.assert_not_called()


@pytest.mark.parametrize("credentials,signed,corrupt,status", [
    ("token", False, False, 200), ("both", False, False, 200),
    ("both", True, False, 200), ("both", True, True, 401),
    ("signature", False, False, 401), ("signature", True, False, 200),
    ("signature", True, True, 401), ("none", False, False, 401),
])
def test_challenge_requires_configured_authentication(credentials, signed, corrupt, status):
    adapter = make_adapter()
    if credentials in {"signature", "none"}:
        adapter._verification_token = ""
    if credentials in {"token", "none"}:
        adapter._encrypt_key = ""
    payload = {"type": "url_verification", "token": "test-token", "challenge": "inert-challenge"}
    request = make_request(payload, signed=signed)
    if corrupt:
        request.headers["x-lark-signature"] = "invalid"
    response = asyncio.run(adapter._handle_webhook_request(request))
    assert response.status == status
    if status == 200:
        assert json.loads(response.text) == {"challenge": "inert-challenge"}
    assert adapter._webhook_rate_counts == {}
    adapter._on_message_event.assert_not_called()


@pytest.mark.parametrize("kind,status", [
    ("utf8", 400), ("surrogate-token", 401), ("encrypted", 400), ("read-error", 400),
])
def test_invalid_wire_input_and_unsupported_encryption_do_not_dispatch(kind, status):
    adapter = make_adapter()
    payload = {"header": {"token": "test-token", "event_type": "im.message.receive_v1"}}
    if kind == "surrogate-token":
        payload["header"]["token"] = "\ud800"
    elif kind == "encrypted":
        payload["encrypt"] = "unsupported-envelope"
    request = make_request(payload)
    if kind == "utf8":
        request.content.readexactly.side_effect = asyncio.IncompleteReadError(b"\xff", 2)
    elif kind == "read-error":
        request.content.readexactly.side_effect = RuntimeError("failed body read")
    assert asyncio.run(adapter._handle_webhook_request(request)).status == status
    assert adapter._webhook_rate_counts == {}
    adapter._on_message_event.assert_not_called()


@pytest.mark.parametrize("scope", ["peer", "listener"])
@pytest.mark.parametrize("cancel", [False, True])
def test_active_body_reads_are_bounded_and_capacity_is_released(scope, cancel, monkeypatch):
    from plugins.platforms.feishu import webhook_ingress

    monkeypatch.setattr(webhook_ingress, "_MAX_ACTIVE_PER_PEER", 2, raising=False)
    monkeypatch.setattr(webhook_ingress, "_MAX_ACTIVE_REQUESTS", 3, raising=False)

    async def scenario():
        adapter = make_adapter()
        count = (webhook_ingress._MAX_ACTIVE_PER_PEER if scope == "peer"
                 else webhook_ingress._MAX_ACTIVE_REQUESTS)
        entered = 0
        ready = asyncio.Event()
        release = asyncio.Event()
        tasks = []

        async def slow_read(_size):
            nonlocal entered
            entered += 1
            if entered == count:
                ready.set()
            await release.wait()
            raise asyncio.IncompleteReadError(b"{}", 3)

        try:
            for index in range(count):
                request = make_request({})
                request.remote = "shared-peer" if scope == "peer" else f"peer-{index}"
                request.content.readexactly.side_effect = slow_read
                tasks.append(asyncio.create_task(adapter._handle_webhook_request(request)))
            await asyncio.wait_for(ready.wait(), 2)
            overflow = make_request({})
            overflow.remote = "shared-peer" if scope == "peer" else "new-peer"
            assert (await adapter._handle_webhook_request(overflow)).status == 503
            overflow.content.readexactly.assert_not_called()
            if scope == "peer":
                # One slow peer cannot consume every listener slot.
                other = make_request({"type": "url_verification", "token": "test-token", "challenge": "ok"})
                other.remote = "other-peer"
                assert (await adapter._handle_webhook_request(other)).status == 200
            if cancel:
                for task in tasks:
                    task.cancel()
            else:
                release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            assert adapter._webhook_active_reads == {}
            recovered = make_request({"type": "url_verification", "token": "test-token", "challenge": "ok"})
            recovered.remote = overflow.remote
            assert (await adapter._handle_webhook_request(recovered)).status == 200
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("profile_modules", [False, True])
def test_shared_listener_capacity_spans_profile_adapters(monkeypatch, tmp_path, profile_modules):
    from types import SimpleNamespace

    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from gateway.platforms.shared_ingress import dispatch_profile_ingress
    from plugins.platforms.feishu import webhook_ingress

    monkeypatch.setattr(webhook_ingress, "_MAX_ACTIVE_REQUESTS", 3)

    async def scenario():
        ready = asyncio.Event()
        release = asyncio.Event()
        entered = 0
        runner = SimpleNamespace(_profile_adapters={})
        tasks = []

        async def slow_read(_size):
            nonlocal entered
            entered += 1
            if entered == 3:
                ready.set()
            await release.wait()
            raise asyncio.IncompleteReadError(b"{}", 3)

        def request(profile, *, slow=False):
            original = make_request({"type": "url_verification", "token": "test-token", "challenge": "ok"})
            if slow:
                original.content.readexactly.side_effect = slow_read
            return make_mocked_request("POST", f"/p/{profile}/feishu", payload=original.content)

        for index in range(4):
            adapter = make_adapter()
            if profile_modules:
                import importlib
                from pathlib import Path

                from hermes_cli.plugins import PluginManager
                from hermes_cli.plugins_manifest import PluginManifest

                manager = PluginManager(scope_key=str(tmp_path / f"home-{index}"))
                manifest = PluginManifest(name=f"feishu-admission-{tmp_path.name}", source="bundled",
                                          path=str(Path(webhook_ingress.__file__).parent))
                package = manager._load_directory_module(manifest)
                scoped = importlib.import_module(f"{package.__name__}.adapter")
                ingress = importlib.import_module(f"{package.__name__}.webhook_ingress")
                monkeypatch.setattr(ingress, "_MAX_ACTIVE_REQUESTS", 3)
                adapter = object.__new__(scoped.FeishuAdapter)
                adapter.__dict__.update(make_adapter().__dict__)
            app = web.Application()
            app.router.add_post("/feishu", adapter._handle_webhook_request)
            app.freeze()
            adapter._shared_ingress_app = app
            runner._profile_adapters[f"profile-{index}"] = {"feishu": adapter}
        try:
            for index in range(3):
                profile = f"profile-{index}"
                tasks.append(asyncio.create_task(dispatch_profile_ingress(
                    runner, profile, "feishu", request(profile, slow=True), scoped=True,
                )))
            await asyncio.wait_for(ready.wait(), 2)
            overflow = request("profile-3")
            assert (await dispatch_profile_ingress(runner, "profile-3", "feishu", overflow, scoped=True)).status == 503
            overflow.content.readexactly.assert_not_called()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        recovered = request("profile-3")
        assert (await dispatch_profile_ingress(runner, "profile-3", "feishu", recovered, scoped=True)).status == 200

    asyncio.run(scenario())

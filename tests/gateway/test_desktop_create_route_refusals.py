"""Desktop's omitted model route inherits config; explicit nulls are invalid params, not storage errors."""

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_desktop_create_omission_inherits_profile_route(tmp_path, monkeypatch):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from gateway.session_policy import restore_policy
    from hermes_state_local import local_receipt

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        'model: {default: mock-model, provider: custom, base_url: "http://127.0.0.1:9/v1"}\n'
        "platform_toolsets:\n  cli: []\n  gui: []\n"
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(run, "_hermes_home", home)
    store = SessionStore(tmp_path / "sessions", GatewayConfig())
    runner = SimpleNamespace(
        session_store=store,
        _session_db=store._db,
        adapters={},
        _draining=False,
        _cached_agent_for=lambda route: None,
    )
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = await initialize_session_authority(
        runner, profile_id=str(home), instance_id="first"
    )
    connection = AuthorityConnection(authority, object(), {"user_id": "human"})

    omitted = await connection.dispatch(
        {
            "id": 1,
            "method": "session.create",
            "params": {
                "request_id": "desktop-omitted",
                "source": "gui",
                "cwd": str(tmp_path),
            },
        }
    )

    assert "result" in omitted, omitted
    policy = restore_policy(
        local_receipt(authority.db, omitted["result"]["session_id"])["policy"]
    )
    assert (policy.model, policy.provider, policy.base_url) == (
        "mock-model",
        "custom",
        "http://127.0.0.1:9/v1",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["model", "provider"])
async def test_desktop_create_null_route_is_invalid_params_not_storage_unavailable(
    tmp_path, monkeypatch, field
):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        'model: {default: mock-model, provider: custom, base_url: "http://127.0.0.1:9/v1"}\n'
        "platform_toolsets:\n  cli: []\n  gui: []\n"
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(run, "_hermes_home", home)
    store = SessionStore(tmp_path / "sessions", GatewayConfig())
    runner = SimpleNamespace(
        session_store=store,
        _session_db=store._db,
        adapters={},
        _draining=False,
        _cached_agent_for=lambda route: None,
    )
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = await initialize_session_authority(
        runner, profile_id=str(home), instance_id="first"
    )
    connection = AuthorityConnection(authority, object(), {"user_id": "human"})

    reply = await connection.dispatch(
        {
            "id": field,
            "method": "session.create",
            "params": {
                "request_id": f"desktop-null-{field}",
                "source": "gui",
                "cwd": str(tmp_path),
                field: None,
            },
        }
    )

    assert reply["error"]["data"]["reason"] == "invalid_params", reply
    assert reply["error"]["data"]["field"] == field, reply
    assert reply["error"]["data"].get("cause") is None, reply

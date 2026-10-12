"""Loading legacy webhook secrets repairs broad POSIX file modes."""

import json
import stat

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.webhook import WebhookAdapter
from hermes_cli.webhook import _load_subscriptions


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("reader", ["cli", "gateway"])
def test_loading_legacy_subscriptions_restricts_permissions(tmp_path, monkeypatch, reader):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "webhook_subscriptions.json"
    data = {"demo": {"secret": "inert-test-secret", "prompt": "test"}}
    path.write_text(json.dumps(data))
    path.chmod(0o644)
    original = path.read_bytes()
    if reader == "cli":
        assert _load_subscriptions() == data
    else:
        adapter = WebhookAdapter(PlatformConfig(enabled=True, extra={"secret": "test-global-secret"}))
        adapter._reload_dynamic_routes()
        assert adapter._routes["demo"] == data["demo"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_bytes() == original


@pytest.mark.platforms("posix")
def test_read_only_legacy_store_warns_without_losing_subscriptions(tmp_path, monkeypatch, caplog):
    from pathlib import Path

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "webhook_subscriptions.json"
    data = {"demo": {"secret": "inert-test-secret", "prompt": "test"}}
    path.write_text(json.dumps(data))
    path.chmod(0o644)
    original_chmod = Path.chmod

    def readonly_chmod(candidate, mode, **kwargs):
        if candidate == path:
            raise PermissionError("read-only test store")
        return original_chmod(candidate, mode, **kwargs)

    monkeypatch.setattr(Path, "chmod", readonly_chmod)
    assert _load_subscriptions() == data
    assert "Could not restrict credential file permissions" in caplog.text

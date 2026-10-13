"""Permission repair only mutates the directly observed regular credential file."""

import json
import stat

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.webhook import WebhookAdapter
from hermes_cli.webhook import _load_subscriptions
from utils import restrict_credential_file_permissions


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("reader", ["cli", "gateway"])
@pytest.mark.parametrize("entry", ["symlink", "directory"])
def test_subscription_read_does_not_chmod_another_entry(tmp_path, monkeypatch, reader, entry):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "webhook_subscriptions.json"
    data = {"demo": {"secret": "inert-secret", "prompt": "test"}}
    target = tmp_path / "shared.json"
    target.write_text(json.dumps(data))
    target.chmod(0o644)
    original = target.read_bytes()
    if entry == "symlink":
        path.symlink_to(target)
        protected = target
    else:
        path.mkdir(mode=0o750)
        protected = path
    before = stat.S_IMODE(protected.stat().st_mode)
    expected = data if entry == "symlink" else {}
    if reader == "cli":
        assert _load_subscriptions() == expected
    else:
        adapter = WebhookAdapter(PlatformConfig(enabled=True, extra={"secret": "test-global-secret"}))
        adapter._reload_dynamic_routes()
        assert adapter._dynamic_routes == expected
    assert stat.S_IMODE(protected.stat().st_mode) == before
    assert target.read_bytes() == original


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("mode", [0o644, 0o244, 0o044])
def test_permission_repair_keeps_legacy_regular_file_contents(tmp_path, mode):
    path = tmp_path / "credentials.json"
    path.write_text("inert legacy fixture")
    path.chmod(mode)
    restrict_credential_file_permissions(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text() == "inert legacy fixture"

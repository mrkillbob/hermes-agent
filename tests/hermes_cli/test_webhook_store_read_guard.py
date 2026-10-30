"""An unavailable subscription snapshot must never become a replacement empty store."""

from argparse import Namespace
import os
import stat
from pathlib import Path

import pytest

from hermes_cli import webhook


@pytest.mark.parametrize("failure", ["unreadable", "invalid", "nonobject", "directory"])
def test_mutation_preserves_existing_store_when_snapshot_is_unavailable(tmp_path, monkeypatch, failure):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "webhook_subscriptions.json"
    original = b'{"keep":{"secret":"inert-existing-secret"}}'
    path.write_bytes(original if failure == "unreadable" else b"{" if failure == "invalid" else b"[]")
    if failure == "directory":
        path.unlink()
        path.mkdir()
    before = path.lstat()
    read_bytes = Path.read_bytes

    def read(candidate):
        if candidate == path and failure == "unreadable":
            raise PermissionError("inert denied read")
        return read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", read)
    changes, saves = [], []
    monkeypatch.setattr(webhook, "_save_subscriptions_unlocked", saves.append)
    assert webhook._load_subscriptions() == {}
    with pytest.raises(RuntimeError, match="unchanged"):
        webhook._mutate_subscriptions(lambda subscriptions: changes.append(subscriptions))
    assert changes == []
    assert saves == []
    assert path.lstat().st_ino == before.st_ino
    if failure != "directory":
        assert read_bytes(path) == (original if failure == "unreadable" else b"{" if failure == "invalid" else b"[]")


@pytest.mark.platforms("posix")
def test_inaccessible_legacy_store_and_dangling_link_are_not_replaced(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "webhook_subscriptions.json"
    original = b'{"keep":{"secret":"inert-existing-secret"}}'
    path.write_bytes(original)
    path.chmod(0o044)
    open_file, read_bytes = os.open, Path.read_bytes
    mutations = []

    def deny_open(candidate, flags, *args, **kwargs):
        if candidate == path:
            raise PermissionError("inert denied descriptor")
        return open_file(candidate, flags, *args, **kwargs)

    def deny_read(candidate):
        if candidate == path:
            raise PermissionError("inert denied read")
        return read_bytes(candidate)

    try:
        with monkeypatch.context() as mocks:
            mocks.setattr(os, "open", deny_open)
            mocks.setattr(Path, "read_bytes", deny_read)
            with pytest.raises(webhook.SubscriptionStoreReadError, match="unchanged"):
                webhook._mutate_subscriptions(mutations.append)
        assert mutations == []
        assert stat.S_IMODE(path.stat().st_mode) == 0o044
        assert "operator repair may be required" in caplog.text
    finally:
        path.chmod(0o600)  # Restore only this inert fixture so its preserved bytes can be checked.
    assert path.read_bytes() == original
    path.unlink()
    path.symlink_to(tmp_path / "absent-store")
    with pytest.raises(webhook.SubscriptionStoreReadError, match="unchanged"):
        webhook._mutate_subscriptions(mutations.append)
    assert mutations == []
    assert path.is_symlink()


@pytest.mark.parametrize("action", ["subscribe", "remove"])
def test_cli_reports_blocked_store_without_success_or_secret_output(tmp_path, monkeypatch, capsys, action):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(webhook, "_is_webhook_enabled", lambda: True)
    path = tmp_path / "webhook_subscriptions.json"
    path.write_text("{")
    args = Namespace(webhook_action=action, name="demo", secret="inert-new-secret", route_profile=None,
                     events="", description="", prompt="", skills="", deliver="log", deliver_chat_id="")
    with pytest.raises(SystemExit) as error:
        webhook.webhook_command(args)
    assert error.value.code == 1
    output = capsys.readouterr().out
    assert "unchanged" in output
    assert "inert-new-secret" not in output
    assert "Created" not in output and "Removed" not in output
    assert path.read_text() == "{"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "delete", "toggle"])
async def test_dashboard_reports_store_conflict_without_publishing(tmp_path, monkeypatch, action):
    from fastapi import HTTPException
    from hermes_cli.web_models import WebhookCreate, WebhookEnabledToggle
    from hermes_cli.web_routers import ops

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(webhook, "_is_webhook_enabled", lambda: True)
    path = tmp_path / "webhook_subscriptions.json"
    path.write_text("{")

    async def run_in_scope(_profile, function):
        return function()

    monkeypatch.setattr(ops, "config_scoped_to_thread", run_in_scope)
    operations = {
        "create": lambda: ops.create_webhook(WebhookCreate(name="demo", secret="inert-new-secret")),
        "delete": lambda: ops.delete_webhook("demo", profile="default"),
        "toggle": lambda: ops.set_webhook_enabled("demo", WebhookEnabledToggle(enabled=True)),
    }
    with pytest.raises(HTTPException) as error:
        await operations[action]()
    assert error.value.status_code == 409
    assert "unchanged" in error.value.detail
    assert "inert-new-secret" not in error.value.detail
    assert path.read_text() == "{"

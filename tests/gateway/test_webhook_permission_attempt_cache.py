"""Subscription permission repair is bounded without freezing legacy-store recovery."""

import json
import os
import stat
from pathlib import Path

import pytest

from gateway.config import PlatformConfig
from gateway.platforms import webhook


def _adapter():
    return webhook.WebhookAdapter(PlatformConfig(enabled=True, extra={"secret": "inert-secret"}))


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("content", [json.dumps({"demo": {"prompt": "test"}}), "{", "[]"])
def test_unchanged_failed_repair_is_bounded_and_eventually_recovers(tmp_path, monkeypatch, caplog, content):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "webhook_subscriptions.json"
    path.write_text(content)
    path.chmod(0o644)
    original_chmod = os.fchmod
    attempts = []
    blocked = True
    now = 100.0

    def repair(fd, mode):
        attempts.append(mode)
        if blocked:
            raise PermissionError("read-only test store")
        return original_chmod(fd, mode)

    monkeypatch.setattr(os, "fchmod", repair)
    monkeypatch.setattr(webhook.time, "monotonic", lambda: now)
    adapter = _adapter()
    for _ in range(3):
        adapter._reload_dynamic_routes()
    assert attempts == [0o600]
    warnings = [record for record in caplog.records if "Could not restrict credential" in record.message]
    assert len(warnings) == 1
    if content.startswith('{"demo"'):
        assert adapter._routes["demo"] == {"prompt": "test"}
    blocked = False
    now += 61.0
    adapter._reload_dynamic_routes()
    assert attempts == [0o600, 0o600]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text() == content


@pytest.mark.platforms("posix")
def test_permission_repair_tracks_mode_replacement_deletion_and_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "webhook_subscriptions.json"
    original = json.dumps({"demo": {"prompt": "first"}})
    path.write_text(original)
    path.chmod(0o644)
    adapter = _adapter()
    adapter._reload_dynamic_routes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    snapshot = adapter._routes
    path.chmod(0o644)
    adapter._reload_dynamic_routes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert adapter._routes is snapshot

    before = path.stat()
    replacement = tmp_path / "replacement.json"
    replacement.write_text(original.replace("first", "other"))
    replacement.chmod(0o644)
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    replacement.replace(path)
    adapter._reload_dynamic_routes()
    assert adapter._routes["demo"]["prompt"] == "other"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.unlink()
    adapter._reload_dynamic_routes()
    assert adapter._routes == {}
    path.write_text(original)
    path.chmod(0o644)
    adapter._reload_dynamic_routes()
    assert adapter._routes["demo"]["prompt"] == "first"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    other_home = tmp_path / "other-home"
    other_home.mkdir()
    other_path = other_home / path.name
    other_path.write_text(original)
    other_path.chmod(0o644)
    monkeypatch.setenv("HERMES_HOME", str(other_home))
    adapter._reload_dynamic_routes()
    assert stat.S_IMODE(other_path.stat().st_mode) == 0o600
    assert other_path.read_text() == original


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("recovery", ["timer", "mode-change"])
def test_failed_snapshot_reads_are_bounded_without_losing_last_good_routes(tmp_path, monkeypatch, caplog, recovery):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "webhook_subscriptions.json"
    path.write_text(json.dumps({"demo": {"prompt": "first"}}))
    path.chmod(0o600)
    adapter = _adapter()
    now = 100.0
    monkeypatch.setattr(webhook.time, "monotonic", lambda: now)
    adapter._reload_dynamic_routes()
    path.write_text(json.dumps({"demo": {"prompt": "recovered"}}))
    read_text = Path.read_text
    reads = []
    blocked = True

    def read(candidate, *args, **kwargs):
        if candidate == path:
            reads.append(candidate)
            if blocked:
                raise PermissionError("inert denied snapshot read")
        return read_text(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    for _ in range(3):
        adapter._reload_dynamic_routes()
    assert len(reads) == 1
    assert adapter._routes["demo"]["prompt"] == "first"
    assert len([record for record in caplog.records if "Failed to read dynamic routes" in record.message]) == 1
    blocked = False
    if recovery == "timer":
        now += 61.0
    else:
        path.chmod(0o644)
    adapter._reload_dynamic_routes()
    assert len(reads) == 2
    assert adapter._routes["demo"]["prompt"] == "recovered"
    adapter._reload_dynamic_routes()
    assert len(reads) == 2


@pytest.mark.parametrize("malformed", ["decode", "json"])
def test_malformed_snapshot_after_read_failure_retains_routes_and_is_cached(tmp_path, monkeypatch, caplog, malformed):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = tmp_path / "webhook_subscriptions.json"
    path.write_text(json.dumps({"demo": {"prompt": "last-good"}}))
    adapter = _adapter()
    now = 100.0
    monkeypatch.setattr(webhook.time, "monotonic", lambda: now)
    adapter._reload_dynamic_routes()
    path.write_text("changed inert snapshot")
    read_text = Path.read_text
    reads = []
    failure = "read"

    def read(candidate, *args, **kwargs):
        if candidate != path:
            return read_text(candidate, *args, **kwargs)
        reads.append(candidate)
        if failure == "read":
            raise PermissionError("inert denied snapshot read")
        if malformed == "decode":
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "inert invalid encoding")
        return "{"

    monkeypatch.setattr(Path, "read_text", read)
    adapter._reload_dynamic_routes()
    failure = "malformed"
    now += 61.0
    for _ in range(3):
        adapter._reload_dynamic_routes()
    now += 61.0
    adapter._reload_dynamic_routes()
    assert len(reads) == 2
    assert adapter._routes["demo"]["prompt"] == "last-good"
    assert len([record for record in caplog.records if "Failed to parse dynamic routes" in record.message]) == 1

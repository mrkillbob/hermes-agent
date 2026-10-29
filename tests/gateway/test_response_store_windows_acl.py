"""Metadata evidence only: no ACL repair or confidentiality verdict is exercised here.

The Windows fixture observes synthetic files at an explicit temporary DB path. It does
not qualify provisioned, managed or shared homes, effective access, or other users.
"""

import json
import os
import shutil
from pathlib import Path

import pytest


def _principal_kind(sid, current_user):
    """Keep account identifiers out of retained receipts; unknown stays explicit."""
    if sid == current_user:
        return "current_user"
    return {"S-1-5-18": "system", "S-1-5-32-544": "administrators",
            "S-1-1-0": "everyone", "S-1-5-11": "authenticated_users",
            "S-1-5-32-545": "users"}.get(sid, "other")


@pytest.mark.parametrize("sid,expected", [
    ("fixture-user", "current_user"), ("S-1-5-18", "system"),
    ("S-1-5-32-544", "administrators"), ("S-1-1-0", "everyone"),
    ("S-1-5-11", "authenticated_users"), ("S-1-5-32-545", "users"),
    ("fixture-other-account", "other"),
])
def test_acl_receipt_labels_do_not_expose_account_identifiers(sid, expected):
    # Constructed expectations test receipt labeling, never native ACL enforcement.
    assert _principal_kind(sid, "fixture-user") == expected


def test_constructed_receipt_survives_attempt_cleanup(tmp_path):
    scratch = tmp_path / "attempt"
    scratch.mkdir()
    body = '{"privacy_verdict":"unqualified","files":{"main":{"status":"observed"}}}'
    exported = _export_acl_receipt(body, tmp_path, scratch)
    shutil.rmtree(scratch)
    assert exported.relative_to(tmp_path).as_posix() == (
        "response-store-acl-diagnostics/response-store-acl-0001.json")
    assert json.loads(exported.read_text(encoding="utf-8")) == {
        "privacy_verdict": "unqualified", "files": {"main": {"status": "observed"}}}


def test_constructed_attempt_receipts_never_overwrite(tmp_path):
    scratch = tmp_path / "attempt"
    scratch.mkdir()
    first = _export_acl_receipt('{"attempt":"first"}', tmp_path, scratch)
    second = _export_acl_receipt('{"attempt":"second"}', tmp_path, scratch)
    assert first != second
    assert first.read_text(encoding="utf-8") == '{"attempt":"first"}\n'
    assert second.read_text(encoding="utf-8") == '{"attempt":"second"}\n'


def test_export_refuses_a_destination_inside_attempt_cleanup(tmp_path):
    scratch = tmp_path / "attempt"
    scratch.mkdir()
    with pytest.raises(ValueError, match="cleanup"):
        _export_acl_receipt("{}", scratch, scratch)
    assert list(scratch.iterdir()) == []


def test_export_refuses_oversized_receipt_before_file_creation(tmp_path):
    scratch = tmp_path / "attempt"
    scratch.mkdir()
    with pytest.raises(ValueError, match="bound"):
        _export_acl_receipt("x" * 65537, tmp_path, scratch)
    assert not (tmp_path / "response-store-acl-diagnostics").exists()


def test_unconfigured_local_inspection_has_no_retained_receipt():
    assert _retain_acl_receipt("{}", None, None, required=False) is None


def test_ci_inspection_requires_retained_receipt_configuration():
    with pytest.raises(ValueError, match="required"):
        _retain_acl_receipt("{}", None, None, required=True)


def test_configured_local_export_still_refuses_cleanup_destination(tmp_path):
    with pytest.raises(ValueError, match="cleanup"):
        _retain_acl_receipt("{}", tmp_path, tmp_path, required=False)


def test_configured_ci_inspection_retains_constructed_receipt(tmp_path):
    scratch = tmp_path / "attempt"
    scratch.mkdir()
    exported = _retain_acl_receipt('{"scope":"constructed"}', tmp_path, scratch, required=True)
    assert json.loads(exported.read_text(encoding="utf-8")) == {"scope": "constructed"}


def _export_acl_receipt(body, output_root, scratch_root):
    """Use the existing retained-artifact root, never the runner's deleted attempt root.

    This survives per-file cleanup; the workflow still needs its own ACL upload glob.
    Exclusive numbered files preserve earlier attempts instead of overwriting them.
    """
    payload = (body + "\n").encode("utf-8")
    if len(payload) > 65536:
        raise ValueError("metadata receipt exceeded byte bound")
    root = Path(output_root)
    if not root.is_absolute():
        raise ValueError("artifact root must be absolute")
    root = root.resolve(strict=True)
    cleanup = Path(scratch_root).resolve(strict=True)
    if root.is_relative_to(cleanup):
        raise ValueError("artifact root is inside attempt cleanup")
    destination = root / "response-store-acl-diagnostics"
    destination.mkdir(exist_ok=True)
    if destination.resolve().is_relative_to(cleanup):
        raise ValueError("artifact destination is inside attempt cleanup")
    for number in range(1, 9):
        path = destination / f"response-store-acl-{number:04d}.json"
        try:
            with path.open("xb") as stream:
                stream.write(payload)
        except FileExistsError:
            continue
        return path
    raise FileExistsError("bounded ACL receipt attempt slots exhausted")


def _retain_acl_receipt(body, output_root, cleanup_root, *, required):
    """Local inspection may omit export; hosted CI must provide retained evidence."""
    if not output_root:
        if required:
            raise ValueError("retained artifact root is required for CI qualification")
        return None
    if not cleanup_root:
        raise ValueError("canonical runner cleanup root is required for export")
    return _export_acl_receipt(body, output_root, cleanup_root)


def _acl_metadata(path, current_user):
    import win32security

    try:
        info = win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION
        descriptor = win32security.GetFileSecurity(str(path), info)
        owner = win32security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner())
        dacl = descriptor.GetSecurityDescriptorDacl()
        count = dacl.GetAceCount() if dacl is not None else 0
        aces = []
        for index in range(min(count, 64)):
            ace = dacl.GetAce(index)
            aces.append({"type": ace[0][0], "flags": ace[0][1], "mask": ace[1],
                         "principal": _principal_kind(
                             win32security.ConvertSidToStringSid(ace[-1]), current_user)})
        return {"status": "observed", "owner": _principal_kind(owner, current_user),
                "dacl": "null" if dacl is None else "present",
                "protected": bool(descriptor.GetSecurityDescriptorControl()[0]
                                  & win32security.SE_DACL_PROTECTED),
                "ace_count": count, "truncated": count > 64, "aces": aces}
    except (OSError, ValueError, TypeError, win32security.error) as error:
        return {"status": "unavailable", "error_type": type(error).__name__}


@pytest.mark.platforms("windows")
def test_response_store_windows_file_dacl_metadata(tmp_path, record_property):
    import win32api
    import win32con
    import win32security

    from gateway.platforms.api_server_response_store import ResponseStore

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        current_user = win32security.ConvertSidToStringSid(
            win32security.GetTokenInformation(token, win32security.TokenUser)[0])
    finally:
        token.Close()
    home = tmp_path / "synthetic-home"
    home.mkdir()
    path = home / "response_store.db"
    store = ResponseStore(db_path=str(path))
    try:
        assert store._db_path == str(path), "memory fallback cannot qualify disk ACL metadata"
        store.put("inert-history", {"output": "synthetic fixture"})
        assert store.get("inert-history") == {"output": "synthetic fixture"}
        assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        files = {"parent": home, "main": path,
                 "wal": path.with_name(path.name + "-wal"),
                 "shm": path.with_name(path.name + "-shm")}
        receipt = {"schema": 1, "scope": "explicit temporary path; metadata observation only",
                   "privacy_verdict": "unqualified",
                   "files": {name: _acl_metadata(item, current_user) for name, item in files.items()}}
        body = json.dumps(receipt, sort_keys=True)
        assert len(body) <= 65536, "metadata receipt exceeded its bound"
        output_root = os.environ.get("HERMES_E2E_ARTIFACTS")
        cleanup_root = os.environ.get("PYTEST_DEBUG_TEMPROOT")
        exported = _retain_acl_receipt(
            body, output_root, cleanup_root, required=os.environ.get("GITHUB_ACTIONS") == "true")
        record_property("response_store_windows_acl_metadata", body)
        record_property("response_store_windows_acl_export",
                        exported.name if exported is not None else "not_configured")
        assert all(row["status"] == "observed" for row in receipt["files"].values()), body
        assert not any(row["truncated"] for row in receipt["files"].values()), body
    finally:
        store.close()

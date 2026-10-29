"""Metadata evidence only: no ACL repair or confidentiality verdict is exercised here.

The Windows fixture observes synthetic files at an explicit temporary DB path. It does
not qualify provisioned, managed or shared homes, effective access, or other users.
"""

import json
import os
import shutil
from pathlib import Path

import pytest


# TOKEN_GROUPS attributes: learn.microsoft.com/windows/win32/api/winnt/ns-winnt-token_groups
_GROUP_ENABLED = 0x00000004
_GROUP_DENY_ONLY = 0x00000010
_GROUP_LOGON_ID = 0xC0000000


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



@pytest.mark.parametrize("flags,relationship,enabled,deny_only", [
    (0xC0000005, "current_logon_sid", True, False),
    (0xC0000010, "current_logon_sid", False, True),
    (-1073741819, "current_logon_sid", True, False),
    (-1073741808, "current_logon_sid", False, True),
    (4, "enabled_token_group", True, False),
    (16, "deny_only_token_group", False, True),
    (2, "disabled_token_group", False, False),
    (0, "disabled_token_group", False, False),
    (0x80000004, "enabled_token_group", True, False),
    (None, "not_in_queried_groups", None, None),
])
def test_constructed_token_relationships_preserve_group_attributes(
        flags, relationship, enabled, deny_only):
    groups = [] if flags is None else [("private-group", flags)]
    context = _read_token_context(lambda: ("private-user", 0), lambda: groups, str)
    evidence = _ace_identity("private-group", context["user"], context, {})
    assert _token_summary(context)["status"] == "observed"
    assert evidence["principal"] == "other"
    assert evidence.get("token_relationship") == relationship
    assert evidence.get("token_enabled") is enabled
    assert evidence.get("token_deny_only") is deny_only


@pytest.mark.parametrize("failure", ["user", "groups", "conversion"])
def test_token_query_errors_stay_unresolved_and_redacted(failure):
    def broken():
        raise RuntimeError("private-account private-user private-group")

    read_user = broken if failure == "user" else lambda: ("private-user", 0)
    read_groups = broken if failure == "groups" else lambda: [("private-group", 4)]
    convert = (lambda _sid: broken()) if failure == "conversion" else str
    context = _read_token_context(read_user, read_groups, convert)
    evidence = _ace_identity("private-group", context["user"], context, {})
    summary = _token_summary(context)
    assert summary == {"status": "unavailable", "reason": "query_or_metadata_error",
                       "correlation_scope": "fixture_only"}
    assert evidence.get("token_relationship") == "unresolved"
    assert evidence.get("token_enabled") is None
    assert evidence.get("token_deny_only") is None
    body = json.dumps({"token_groups": summary, "ace": evidence})
    assert all(value not in body for value in ("private-user", "private-group", "private-account"))


@pytest.mark.parametrize("groups", [
    [("private-group", -1)],
    [("private-group", -2147483649)],
    [("private-group", 0x100000000)],
    [("private-group", 20)],
    [("private-group", 4), ("private-group", 16)],
    [("private-group",)],
])
def test_malformed_token_metadata_never_claims_a_relationship(groups):
    context = _read_token_context(lambda: ("private-user", 0), lambda: groups, str)
    assert _token_summary(context)["status"] == "unavailable"
    assert _ace_identity("private-group", context["user"], context, {}).get(
        "token_relationship") == "unresolved"


def test_token_group_bound_does_not_silently_qualify_partial_metadata():
    context = _read_token_context(
        lambda: ("private-user", 0), lambda: [("private-group", 4)] * 1025, str)
    assert _token_summary(context) == {
        "status": "unavailable", "reason": "group_limit_exceeded",
        "correlation_scope": "fixture_only"}
    assert _ace_identity("private-group", context["user"], context, {}).get(
        "token_relationship") == "unresolved"


def test_ace_equality_is_fixture_local_and_never_exports_identifiers():
    context = _read_token_context(lambda: ("private-user", 0),
                                  lambda: [("private-a", 4), ("private-b", 16)], str)
    seen = {}
    first = _ace_identity("private-a", context["user"], context, seen)
    same = _ace_identity("private-a", context["user"], context, seen)
    different = _ace_identity("private-b", context["user"], context, seen)
    assert first.get("fixture_principal") == same.get("fixture_principal") == 1
    assert different.get("fixture_principal") == 2
    assert _ace_identity("private-b", context["user"], context, {}).get("fixture_principal") == 1
    payload = {"privacy_verdict": "unqualified", "token_groups": _token_summary(context),
               "aces": [first, same, different]}
    body = json.dumps(payload)
    assert all(value not in body for value in ("private-user", "private-a", "private-b"))
    assert payload["token_groups"] == {"status": "observed", "correlation_scope": "fixture_only"}


def test_ace_correlation_bound_is_explicit_instead_of_reusing_an_identity():
    context = _read_token_context(lambda: ("private-user", 0),
                                  lambda: [("private-new", 4)], str)
    seen = {f"private-{number}": number + 1 for number in range(256)}
    evidence = _ace_identity("private-new", context["user"], context, seen)
    assert evidence.get("fixture_principal") is None
    assert evidence.get("correlation_status") == "bound_exceeded"

def _read_token_context(read_user, read_groups, sid_text):
    """Private comparison inputs stay in memory; any incomplete query stays unknown."""
    context = {"user": None, "groups": {}, "status": "unavailable",
               "reason": "query_or_metadata_error"}
    try:
        user = sid_text(read_user()[0])
        if not isinstance(user, str) or not 1 <= len(user) <= 512:
            return context
        context["user"] = user
        rows = read_groups()
        if not isinstance(rows, (list, tuple)):
            return context
        if len(rows) > 1024:
            context["reason"] = "group_limit_exceeded"
            return context
        groups = {}
        for sid, flags in rows:
            text = sid_text(sid)
            if not isinstance(text, str) or not 1 <= len(text) <= 512:
                return context
            if type(flags) is not int or not -2147483648 <= flags <= 0xFFFFFFFF:
                return context
            # pywin32 marshals DWORD attributes with signed C long (Py_BuildValue "Ol").
            flags &= 0xFFFFFFFF
            if flags & _GROUP_ENABLED and flags & _GROUP_DENY_ONLY:
                return context
            if text in groups and groups[text] != flags:
                return context
            groups[text] = flags
    except Exception:
        # Native query/conversion errors must be retained without their account-bearing text.
        return context
    return {"user": user, "groups": groups, "status": "observed"}


def _token_summary(context):
    summary = {"status": "observed" if context["status"] == "observed" else "unavailable",
               "correlation_scope": "fixture_only"}
    if summary["status"] == "unavailable":
        summary["reason"] = ("group_limit_exceeded" if context.get("reason") == "group_limit_exceeded"
                             else "query_or_metadata_error")
    return summary


def _ace_identity(sid, current_user, context, seen):
    evidence = {"principal": _principal_kind(sid, current_user)}
    if evidence["principal"] != "other":
        return evidence
    if sid not in seen and len(seen) < 256:
        seen[sid] = len(seen) + 1
    identifier = seen.get(sid)
    evidence.update({"fixture_principal": identifier,
                     "correlation_status": "observed" if identifier is not None else "bound_exceeded",
                     "token_relationship": "unresolved", "token_enabled": None,
                     "token_deny_only": None})
    if context["status"] != "observed":
        return evidence
    flags = context["groups"].get(sid)
    if flags is None:
        evidence["token_relationship"] = "not_in_queried_groups"
        return evidence
    enabled, deny_only = bool(flags & _GROUP_ENABLED), bool(flags & _GROUP_DENY_ONLY)
    if flags & _GROUP_LOGON_ID == _GROUP_LOGON_ID:
        relationship = "current_logon_sid"
    elif deny_only:
        relationship = "deny_only_token_group"
    else:
        relationship = "enabled_token_group" if enabled else "disabled_token_group"
    evidence.update(token_relationship=relationship, token_enabled=enabled, token_deny_only=deny_only)
    return evidence


def _acl_metadata(path, current_user, context, seen):
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
                         **_ace_identity(win32security.ConvertSidToStringSid(ace[-1]),
                                         current_user, context, seen)})
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
        context = _read_token_context(
            lambda: win32security.GetTokenInformation(token, win32security.TokenUser),
            lambda: win32security.GetTokenInformation(token, win32security.TokenGroups),
            win32security.ConvertSidToStringSid)
    finally:
        token.Close()
    current_user = context["user"]
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
        seen = {}  # Ephemeral equality labels reset for each fixture, never cross-run identifiers.
        receipt = {"schema": 2, "scope": "explicit temporary path; metadata observation only",
                   "privacy_verdict": "unqualified",
                   "token_groups": _token_summary(context),
                   "files": {name: _acl_metadata(item, current_user, context, seen)
                             for name, item in files.items()}}
        body = json.dumps(receipt, sort_keys=True)
        assert len(body) <= 65536, "metadata receipt exceeded its bound"
        output_root = os.environ.get("HERMES_E2E_ARTIFACTS")
        cleanup_root = os.environ.get("PYTEST_DEBUG_TEMPROOT")
        exported = _retain_acl_receipt(
            body, output_root, cleanup_root, required=os.environ.get("GITHUB_ACTIONS") == "true")
        record_property("response_store_windows_acl_metadata", body)
        record_property("response_store_windows_acl_export",
                        exported.name if exported is not None else "not_configured")
        assert receipt["token_groups"]["status"] == "observed", body
        assert all(row["status"] == "observed" for row in receipt["files"].values()), body
        assert not any(row["truncated"] for row in receipt["files"].values()), body
        assert all(ace.get("correlation_status") == "observed"
                   for row in receipt["files"].values() for ace in row["aces"]
                   if ace["principal"] == "other"), body
    finally:
        store.close()

#!/usr/bin/env python3
"""Validate an immutable conversation creation base before project bootstrap.

Run with the manager's BASE_COMMIT and SOURCE_WORKTREE environment metadata.
The installer owns the configured launcher and preserves its interpreter when
publishing this tracked body. Freshness is resolved before claim by the manager.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
from urllib.parse import urlsplit


RECEIPT = "hermes-conversation-base-receipt.json"


def _git(checkout: Path, *arguments: str) -> str:
    return _command(checkout, ["git", *arguments])


def _command(checkout: Path, command: list[str]) -> str:
    result = subprocess.run(
        command, cwd=checkout, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"},
    )
    if result.returncode:
        raise RuntimeError(f"bootstrap command failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def _remote_identity(value: str) -> str:
    # HTTPS and SSH spellings of the same repository have one policy identity.
    value = value.strip().lower().rstrip("/").removesuffix(".git")
    if "://" in value:
        parsed = urlsplit(value)
        return f"{parsed.hostname or ''}/{parsed.path.lstrip('/')}".rstrip("/")
    if ":" in value:
        authority, path = value.rsplit(":", 1)
        return f"{authority.rsplit('@', 1)[-1]}/{path}".rstrip("/")
    return value


def _metadata_path(checkout: Path, name: str) -> Path:
    path = Path(_git(checkout, "rev-parse", "--git-path", name))
    return path if path.is_absolute() else checkout / path


def _common_directory(checkout: Path) -> Path:
    return Path(_git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()


def _verify_creation(checkout: Path, common: Path) -> str:
    base = os.environ.get("HERMES_CONVERSATION_BASE_COMMIT", "")
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", base) is None:
        raise RuntimeError("bootstrap requires the manager's full pinned creation SHA")
    if _git(checkout, "rev-parse", "--verify", f"{base}^{{commit}}") != base:
        raise RuntimeError("pinned creation base is not a commit")
    if _git(checkout, "rev-parse", "HEAD") != base:
        raise RuntimeError("child HEAD differs from pinned creation base")
    value = os.environ.get("HERMES_CONVERSATION_SOURCE_WORKTREE", "")
    source = Path(value)
    if not value or not source.is_absolute():
        raise RuntimeError("bootstrap requires the manager's absolute source worktree")
    source = source.resolve()
    if source == checkout or Path(_git(source, "rev-parse", "--show-toplevel")).resolve() != source:
        raise RuntimeError("creation source is not a separate repository worktree")
    if _common_directory(source) != common:
        raise RuntimeError("creation source and child do not share the claimed repository")
    return base


def _require_clean(checkout: Path) -> None:
    if _git(checkout, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("new conversation worktree is not clean before bootstrap")


def _record_base(checkout: Path, expected: dict[str, object]) -> None:
    receipt = _metadata_path(checkout, RECEIPT)
    if receipt.exists():
        stored = json.loads(receipt.read_text(encoding="utf-8-sig"))
        if not isinstance(stored, Mapping) or any(stored.get(key) != value for key, value in expected.items()):
            raise RuntimeError("stored base receipt differs from pinned creation identity")
        return
    _require_clean(checkout)
    with receipt.open("x", encoding="utf-8") as destination:
        json.dump(expected, destination, sort_keys=True, indent=2)
        destination.write("\n")


def _hook_setting(checkout: Path) -> tuple[str, str, str] | None:
    result = subprocess.run(
        ["git", "config", "--null", "--show-scope", "--show-origin", "--get-all", "core.hooksPath"],
        cwd=checkout, capture_output=True, text=True, check=False,
    )
    if result.returncode == 1 and not result.stdout:
        return None
    fields = result.stdout.rstrip("\0").split("\0")
    if result.returncode or len(fields) != 3:
        raise RuntimeError("hook configuration has unknown or multiple origins; preserving it")
    return tuple(fields)


def _tracked_hook(checkout: Path) -> tuple[Path, str]:
    hook = checkout / ".githooks" / "pre-push"
    if hook.is_symlink() or hook.resolve() != hook or not hook.is_file() or not os.access(hook, os.X_OK):
        raise RuntimeError("own pre-push hook is missing, nonexecutable or escapes its checkout")
    entry = _git(checkout, "ls-tree", "HEAD", "--", ".githooks/pre-push").split()
    if len(entry) != 4 or entry[:2] != ["100755", "blob"]:
        raise RuntimeError("own executable pre-push hook is not tracked at HEAD")
    if _git(checkout, "hash-object", "--no-filters", str(hook)) != entry[2]:
        raise RuntimeError("pre-push hook differs from its exact HEAD bytes")
    return hook, entry[2]


def _repair_inherited_hooks(checkout: Path) -> None:
    setting = _hook_setting(checkout)
    hook, child_blob = _tracked_hook(checkout)
    if setting is None:
        return  # The project's bootstrap owns installing an absent setting.
    scope, origin, old_value = setting
    configured = Path(old_value)
    effective = configured if configured.is_absolute() else checkout / configured
    if effective.resolve() == hook.parent:
        return
    source = Path(os.environ["HERMES_CONVERSATION_SOURCE_WORKTREE"]).resolve()
    common = _common_directory(checkout)
    child_config = _metadata_path(checkout, "config.worktree")
    source_config = _metadata_path(source, "config.worktree")
    if (
        scope != "worktree" or origin != f"file:{child_config}"
        or child_config.is_symlink() or source_config.is_symlink()
        or _hook_setting(source) != ("worktree", f"file:{source_config}", old_value)
        or not configured.is_absolute() or configured != source / ".githooks"
    ):
        raise RuntimeError("hook setting is not proven owned worktree inheritance; preserving it")
    source_hook, source_blob = _tracked_hook(source)
    if source_blob != child_blob or Path(_git(checkout, "rev-parse", "--git-path", "hooks/pre-push")) != source_hook:
        raise RuntimeError("inherited effective hook does not match source and child HEAD bytes")
    registered = _git(source, "worktree", "list", "--porcelain", "-z").split("\0")
    if any(f"worktree {path}" not in registered for path in (source, checkout)):
        raise RuntimeError("source or child is not a registered worktree")
    proof = json.loads(_metadata_path(checkout, "hermes-conversation-config-inheritance-v1.json").read_text(encoding="utf-8-sig"))
    required = {
        "owner": "conversation-worktree-manager", "worktree_path": str(checkout),
        "repo_common_dir": str(common), "source_worktree": str(source),
        "source_head": _git(source, "rev-parse", "HEAD"),
        "base_commit": os.environ["HERMES_CONVERSATION_BASE_COMMIT"],
    }
    if not isinstance(proof, Mapping) or any(proof.get(key) != value for key, value in required.items()):
        raise RuntimeError("creation config inheritance receipt does not match this allocation")
    root_id = proof.get("root_session_id")
    if not isinstance(root_id, str) or not root_id:
        raise RuntimeError("creation inheritance receipt has no conversation owner")
    ownership = {key: proof[key] for key in ("owner", "root_session_id", "worktree_path", "repo_common_dir")}
    digest = hashlib.sha256(str(checkout).encode()).hexdigest()
    claims = (
        _metadata_path(checkout, "hermes-conversation-owner-v1"),
        common / "hermes-conversation-owner-claims-v1" / f"{digest}.json",
    )
    if any(json.loads(path.read_text(encoding="utf-8-sig")) != ownership for path in claims):
        raise RuntimeError("config inheritance is missing exact durable manager ownership")
    original = child_config.read_bytes()
    if source_config.read_bytes() != original or hashlib.sha256(original).hexdigest() != proof.get("config_sha256"):
        raise RuntimeError("copied worktree configuration has changed since creation")
    lock = child_config.with_name(child_config.name + ".lock")
    with lock.open("xb") as handle:
        handle.write(original)
        handle.flush()
        os.fsync(handle.fileno())
    owned_lock = lock.lstat()
    lock_identity = (owned_lock.st_dev, owned_lock.st_ino)
    try:
        backup = child_config.with_name(f"hermes-hook-repair-{uuid.uuid4().hex}.backup")
        with backup.open("xb") as destination:
            destination.write(original)
            destination.flush()
            os.fsync(destination.fileno())
        _command(checkout, [
            "git", "config", "--file", str(lock), "--fixed-value", "--replace-all",
            "core.hooksPath", ".githooks", old_value,
        ])
        with lock.open("rb") as replacement:
            os.fsync(replacement.fileno())
        owned_lock = lock.lstat()
        lock_identity = (owned_lock.st_dev, owned_lock.st_ino)
        if (
            child_config.read_bytes() != original or source_config.read_bytes() != original
            or _git(source, "rev-parse", "HEAD") != proof["source_head"]
        ):
            raise RuntimeError("worktree configuration changed during hook repair; preserving it")
        _tracked_hook(source)
        _tracked_hook(checkout)
        audit = {**dict(proof), "old_hooks_path": old_value, "new_hooks_path": ".githooks",
                 "backup": str(backup), "hook_blob": child_blob}
        with backup.with_suffix(".json").open("x", encoding="utf-8") as destination:
            json.dump(audit, destination, sort_keys=True)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(lock, child_config)
    except BaseException:
        # An interrupt can arrive after rename committed. Preserve any lock
        # acquired by a successor, even when replacement never returned.
        try:
            current_lock = lock.lstat()
        except FileNotFoundError:
            pass
        else:
            if (current_lock.st_dev, current_lock.st_ino) == lock_identity:
                lock.unlink()
        raise
    if _hook_setting(checkout) != ("worktree", f"file:{child_config}", ".githooks"):
        raise RuntimeError("repaired hook setting did not resolve inside the child")


def _bootstrap_luna(checkout: Path) -> None:
    _repair_inherited_hooks(checkout)
    if not _metadata_path(checkout, "agent-workspace-receipt.json").exists():
        _require_clean(checkout)
    cold_commands = (
        ["scripts/check_agent_workspace.py", "--ensure-new", "--agent", "hermes"],
        ["scripts/bootstrap_agent_workspace.py"],
    )
    for arguments in cold_commands:
        _command(checkout, [sys.executable, *arguments])
    # The project's stdlib validator owns runtime, lock and prefix admission.
    probe = (
        "from pathlib import Path; import runpy; "
        "check = runpy.run_path(str(Path.cwd() / 'scripts/worktree_environment.py'))['validate_environment']; "
        "issue = check(Path.cwd()); raise SystemExit(issue or 0)"
    )
    _command(checkout, [sys.executable, "-c", probe])
    child_python = checkout / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _command(checkout, [str(child_python), "scripts/check_agent_workspace.py"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("hermes-agent", "lunabot"))
    parser.add_argument("--remote", required=True)
    parser.add_argument("--branch", required=True, help="Governed creation branch selected by the manager")
    parser.add_argument("--expected-remote", required=True)
    parser.add_argument("--worktree-root", required=True)
    parser.add_argument("--branch-prefix", default="hermes/session/")
    parser.add_argument("--skip-non-target-repo", action="store_true")
    options = parser.parse_args(argv)
    try:
        checkout = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel")).resolve()
        remote = _git(checkout, "remote", "get-url", options.remote)
    except RuntimeError:
        if options.skip_non_target_repo:
            return 0
        raise
    identity = _remote_identity(options.expected_remote)
    if _remote_identity(remote) != identity:
        if options.skip_non_target_repo:
            return 0
        raise RuntimeError("configured creation remote identity mismatch")
    if not checkout.is_relative_to(Path(options.worktree_root).resolve()):
        raise RuntimeError("conversation checkout is outside the configured worktree root")
    common = _common_directory(checkout)
    git_directory = Path(_git(checkout, "rev-parse", "--absolute-git-dir")).resolve()
    if git_directory == common:
        raise RuntimeError("conversation bootstrap requires an isolated linked worktree")
    branch = _git(checkout, "branch", "--show-current")
    if not options.branch_prefix or not branch.startswith(options.branch_prefix):
        raise RuntimeError("conversation branch does not match the configured prefix")
    base = _verify_creation(checkout, common)
    if options.mode == "lunabot":
        _bootstrap_luna(checkout)
    else:
        _record_base(checkout, {
            "schema": 1, "remote": options.remote, "remote_identity": identity,
            "branch": branch, "worktree": str(checkout), "common_dir": str(common),
            "base_sha": base,
        })
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"[conversation-worktree-base-guard] BLOCKED: {error}", file=sys.stderr)
        sys.exit(2)

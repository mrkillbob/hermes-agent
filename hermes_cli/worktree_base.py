"""Canonical base selection for newly-created Git worktrees."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from hermes_cli._subprocess_compat import noninteractive_git_env

logger = logging.getLogger(__name__)


def resolve_worktree_base(
    repo_root: str,
    fetch_timeout: float = 5,
    freshness_window: float = 300,
    *,
    prefer_current_upstream: bool = True,
) -> tuple[str, str]:
    """Return a refreshed base ref and a human-readable provenance label.

    Continuation flows may prefer the current branch's configured upstream.
    New-work flows set ``prefer_current_upstream=False`` so a parked feature
    checkout cannot silently become the base; they resolve the remote default
    branch first. Network failures retain the last known remote-tracking ref,
    with local ``HEAD`` as the final compatibility fallback.
    """

    def _git(args: list[str], timeout: float = 20) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            env=noninteractive_git_env(),
        )

    def _ref_exists(ref: str) -> bool:
        try:
            return (
                _git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"]).returncode
                == 0
            )
        except Exception:
            return False

    def _ref_age(ref: str) -> float | None:
        # ``FETCH_HEAD`` records the most recent fetch of *any* remote/branch,
        # not necessarily ``ref``. Freshness must be tied to the tracking ref
        # actually being used, so use the mtime of its own loose ref file
        # (``.git/refs/remotes/<remote>/<branch>``) instead. If the ref has
        # been packed (no loose file) its per-ref freshness can't be verified
        # this way, so return ``None`` and let the caller re-fetch.
        try:
            result = _git(["rev-parse", "--git-path", f"refs/remotes/{ref}"])
            if result.returncode != 0:
                return None
            ref_path = Path(result.stdout.strip())
            if not ref_path.is_absolute():
                ref_path = Path(repo_root) / ref_path
            if not ref_path.exists():
                return None
            return max(0.0, time.time() - ref_path.stat().st_mtime)
        except Exception:
            return None

    def _refresh(remote: str, branch: str, ref: str) -> tuple[str, str]:
        age = _ref_age(ref)
        if age is not None and age < freshness_window and _ref_exists(ref):
            return ref, f"{ref} (fetched {int(age)}s ago)"
        try:
            fetched = _git(["fetch", remote, branch], timeout=fetch_timeout)
            if fetched.returncode == 0:
                return ref, f"{ref} (fetched)"
            reason = "fetch failed"
        except subprocess.TimeoutExpired:
            reason = f"fetch timed out after {fetch_timeout:g}s"
        except Exception as exc:
            reason = f"fetch error: {exc}"
        if _ref_exists(ref):
            logger.debug("worktree base: %s - using cached %s", reason, ref)
            return ref, f"{ref} (cached - {reason})"
        return "HEAD", f"HEAD (local - {reason}, no cached {ref})"

    if prefer_current_upstream:
        try:
            upstream_result = _git(
                ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]
            )
            upstream = upstream_result.stdout.strip() if upstream_result.returncode == 0 else ""
            if upstream and "/" in upstream:
                remote, branch = upstream.split("/", 1)
                return _refresh(remote, branch, upstream)
        except Exception as exc:
            logger.debug("worktree base: upstream resolution failed: %s", exc)

    try:
        head_ref = _git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"])
        default_ref = ""
        if head_ref.returncode == 0:
            default_ref = head_ref.stdout.strip().removeprefix("refs/remotes/")
        if not default_ref:
            show = _git(["remote", "show", "origin"], timeout=max(fetch_timeout, 5))
            for raw_line in show.stdout.splitlines():
                line = raw_line.strip()
                if line.startswith("HEAD branch:"):
                    branch = line.split(":", 1)[1].strip()
                    if branch and branch != "(unknown)":
                        default_ref = f"origin/{branch}"
                    break
        if default_ref and "/" in default_ref:
            remote, branch = default_ref.split("/", 1)
            return _refresh(remote, branch, default_ref)
    except Exception as exc:
        logger.debug("worktree base: default-branch resolution failed: %s", exc)

    return "HEAD", "HEAD (local - could not reach remote)"

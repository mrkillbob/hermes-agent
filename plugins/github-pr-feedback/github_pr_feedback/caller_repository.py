"""Bind a feedback invocation to the host's caller workspace, never global cwd.

The context is task-local. Long-lived adapters capture the immutable value before
submitting work to threads; no process cwd or environment is rewritten.
"""
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
import json
import os
import re
import subprocess


class RepositoryBindingError(ValueError):
    pass


_active = ContextVar("github_feedback_caller_repository", default=None)
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_GIT_ROUTING = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                          "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"})
_GIT_CONFIG_ISOLATION = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                         "GIT_CONFIG_NOSYSTEM": "1"}


def reject_git_overrides():
    config_keys = (key for key in os.environ if key == "GIT_CONFIG" or key.startswith("GIT_CONFIG_"))
    # Hermes deliberately disables global/system config in sanitized children.
    # Only those exact inert settings are safe; arbitrary files/injections refuse.
    if _GIT_ROUTING.intersection(os.environ) or any(
        key not in _GIT_CONFIG_ISOLATION or os.environ[key] != _GIT_CONFIG_ISOLATION[key]
        for key in config_keys
    ):
        raise RepositoryBindingError("ambient_git_routing_override")


def _git(path, *args):
    reject_git_overrides()
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args], check=False, text=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RepositoryBindingError("caller_repository_unavailable") from None
    if result.returncode:
        raise RepositoryBindingError("caller_repository_unavailable")
    return result.stdout.strip()


def _repository(url):
    # Never retain or echo embedded credentials, URL queries or arbitrary hosts.
    if url.startswith("git@github.com:"):
        path = url[len("git@github.com:"):]
    else:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise RepositoryBindingError("caller_remote_identity_unavailable") from None
        if (parsed.hostname != "github.com" or parsed.query or parsed.fragment
                or parsed.password or port is not None
                or (parsed.scheme == "https" and parsed.username is not None)
                or (parsed.scheme == "ssh" and parsed.username != "git")
                or parsed.scheme not in {"https", "ssh"}):
            raise RepositoryBindingError("caller_remote_identity_unavailable")
        path = parsed.path.lstrip("/")
    path = path.removesuffix(".git")
    if not _REPO.fullmatch(path):
        raise RepositoryBindingError("caller_remote_identity_unavailable")
    return path.casefold()


@dataclass(frozen=True, slots=True)
class CallerRepository:
    root: Path
    common_dir: Path
    repository: str

    @classmethod
    def resolve(cls, ctx, requested=None):
        try:
            cwd = ctx.session_cwd(require_session=True)
        except (AttributeError, RuntimeError, TypeError):
            raise RepositoryBindingError("trusted_caller_workspace_required") from None
        if not isinstance(cwd, str) or not cwd or not Path(cwd).is_absolute():
            raise RepositoryBindingError("trusted_caller_workspace_required")
        root = Path(_git(cwd, "rev-parse", "--show-toplevel")).resolve(strict=True)
        common = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve(strict=True)
        urls = _git(root, "remote", "get-url", "--all", "origin").splitlines()
        if len(urls) != 1:
            raise RepositoryBindingError("unambiguous_caller_origin_required")
        repository = _repository(urls[0])
        push_urls = _git(root, "remote", "get-url", "--push", "--all", "origin").splitlines()
        if len(push_urls) != 1 or _repository(push_urls[0]) != repository:
            raise RepositoryBindingError("caller_push_repository_differs")
        if requested is not None and (not isinstance(requested, str) or requested.casefold() != repository):
            raise RepositoryBindingError("requested_repository_differs_from_caller")
        return cls(root, common, repository)

    @property
    def lock_key(self):
        return sha256(f"{self.common_dir}\0{self.repository.casefold()}".encode()).hexdigest()

    def verify_workspace(self, workspace, repository):
        if not isinstance(repository, str) or repository.casefold() != self.repository.casefold():
            raise RepositoryBindingError("workspace_repository_differs_from_caller")
        if not isinstance(workspace, (str, Path)):
            raise RepositoryBindingError("absolute_caller_workspace_required")
        candidate = Path(workspace)
        if not candidate.is_absolute():
            raise RepositoryBindingError("absolute_caller_workspace_required")
        resolved = CallerRepository.resolve(_WorkspaceContext(candidate), repository)
        if resolved.common_dir != self.common_dir:
            raise RepositoryBindingError("workspace_common_directory_differs_from_caller")


class _WorkspaceContext:
    def __init__(self, path):
        self.path = path

    def session_cwd(self, *, require_session=False):
        return str(self.path)


def current_repository():
    return _active.get()


@contextmanager
def repository_scope(binding):
    token = _active.set(binding)
    try:
        yield binding
    finally:
        _active.reset(token)


class ScopedContext:
    """Narrow existing grants before parsing, without inventing repository policy."""
    def __init__(self, ctx, binding):
        self._ctx, self.binding = ctx, binding
        targets = ctx.get_config("repositories", default=[])
        if not isinstance(targets, list):
            raise RepositoryBindingError("caller_repository_not_configured")
        selected = [t for t in targets if isinstance(t, dict)
                    and str(t.get("base_repository", "")).casefold() == binding.repository.casefold()]
        if len(selected) != 1:
            raise RepositoryBindingError("caller_repository_not_configured")
        target = selected[0]
        # The checkout belongs to the base. An explicitly allowed head fork is
        # admission policy, not an alternate workspace or lock identity.
        binding.verify_workspace(target.get("local_path", ""), binding.repository)
        # Git identity/locks are case-insensitive; existing admission/intake
        # contracts retain the configured spelling used by GitHub responses.
        self.binding = replace(binding, repository=target["base_repository"])
        self._target = {**target, "local_path": str(binding.root)}

    def _verify_deployment(self, lane):
        deployment = lane.get("post_merge")
        if lane.get("enabled") is True and isinstance(deployment, dict) and deployment.get("enabled") is True:
            self.binding.verify_workspace(deployment.get("deployment_path"), self.binding.repository)

    def __getattr__(self, name):
        return getattr(self._ctx, name)

    def get_config(self, key, default=None):
        if key == "repositories":
            return [dict(self._target)]
        value = self._ctx.get_config(key, default=default)
        scopers = {
            "merge_maintainer": self._merge_lane,
            "release_maintenance": self._single_lane,
            "merge_maintainers": self._merge_lanes,
            "release_maintenances": self._many_lanes,
            "repair_steward": self._repository_subset,
            "local_ci_audit": self._all_targets_subset,
            "agent_labels": self._all_targets_subset,
            "github_actions_permissions_identity": self._repository_subset,
        }
        scope = scopers.get(key)
        return scope(value, default) if scope is not None else value

    def _single_lane(self, value, default):
        if not isinstance(value, dict):
            return value
        if value.get("enabled") is True and str(value.get("repository", "")).casefold() != self.binding.repository.casefold():
            return default
        return {**value, "repository": self.binding.repository} if "repository" in value else value

    def _merge_lane(self, value, default):
        scoped = self._single_lane(value, default)
        if isinstance(scoped, dict):
            self._verify_deployment(scoped)
        return scoped

    def _many_lanes(self, value, default):
        if not isinstance(value, list):
            return value
        selected = [{**v, "repository": self.binding.repository} for v in value if isinstance(v, dict)
                    and str(v.get("repository", "")).casefold() == self.binding.repository.casefold()]
        return selected or default

    def _merge_lanes(self, value, default):
        selected = self._many_lanes(value, default)
        if isinstance(selected, list):
            for lane in selected:
                self._verify_deployment(lane)
        return selected

    def _repository_subset(self, value, default):
        if not isinstance(value, dict) or value.get("repositories") is None:
            return value
        repos = value["repositories"]
        if not isinstance(repos, list):
            raise RepositoryBindingError("invalid_repository_policy")
        if not any(isinstance(r, str) and r.casefold() == self.binding.repository.casefold() for r in repos):
            return default
        return {**value, "repositories": [self.binding.repository]}

    def _all_targets_subset(self, value, default):
        if isinstance(value, dict) and value.get("repositories") == []:
            return {**value, "repositories": [self.binding.repository]}
        return self._repository_subset(value, default)


def bind_cli(ctx, handler):
    def invoke(args):
        try:
            if getattr(args, "github_pr_feedback_action", None) == "scan-admin":
                return _administrative_scan(ctx, handler, args)
            binding = CallerRepository.resolve(ctx, getattr(args, "repository", None))
            scoped = ScopedContext(ctx, binding)
            binding = scoped.binding
            for name in ("worktree", "repository_path"):
                path = getattr(args, name, None)
                if path is not None and not (name == "worktree" and path is False):
                    binding.verify_workspace(path, binding.repository)
            bound_args = copy(args)
            if getattr(args, "repository", None) is not None:
                bound_args.repository = binding.repository
            with repository_scope(binding):
                return handler(scoped, bound_args)
        except (RepositoryBindingError, OSError):
            # Stable typed error, never a raw path, remote URL, config or secret.
            print(json.dumps({"status": "rejected", "reason": "caller_repository_binding_failed"}))
            return 1
    return invoke


def _administrative_scan(ctx, handler, args):
    # A new name alone is not authority. Existing workers cannot opt themselves
    # out of binding, and no installed config grants this separate capability.
    protected = {"HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID",
                 "HERMES_KANBAN_CLAIM_LOCK", "HERMES_DELEGATED_CHILD_CONTEXT",
                 "HERMES_KANBAN_DB", "HERMES_KANBAN_WORKSPACES_ROOT",
                 "HERMES_KANBAN_ATTACHMENTS_ROOT"}
    if (ctx.get_config("administrative_batch", default=False) is not True
            or getattr(args, "all_configured", False) is not True
            or protected.intersection(os.environ)
            or current_repository() is not None):
        raise RepositoryBindingError("administrative_batch_not_authorized")
    # Administrative scans take the same caller locks in a stable order, then
    # the legacy scan takes its control-home lock. No admin/caller overlap.
    from .cli import _exclusive_scan_lock
    targets = ctx.get_config("repositories", default=[])
    if not isinstance(targets, list) or not targets:
        raise RepositoryBindingError("administrative_targets_unavailable")
    bindings = {}
    for target in targets:
        if not isinstance(target, dict):
            raise RepositoryBindingError("administrative_targets_unavailable")
        repository = target.get("base_repository")
        if not isinstance(repository, str) or not _REPO.fullmatch(repository):
            raise RepositoryBindingError("administrative_targets_unavailable")
        source = CallerRepository.resolve(_WorkspaceContext(target.get("local_path", "")), repository)
        bindings[source.lock_key] = source
    with ExitStack() as held:
        for key in sorted(bindings):
            with repository_scope(bindings[key]):
                acquired = held.enter_context(_exclusive_scan_lock())
            if not acquired:
                raise RepositoryBindingError("administrative_scan_in_progress")
        return handler(ctx, args)

"""Platform-neutral configuration policy for conversation worktree isolation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import re
from numbers import Real
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


class ConversationWorktreePolicyError(ValueError):
    """Raised when conversation worktree configuration is unsafe or incomplete."""


@dataclass(frozen=True)
class ConversationCreationBasePolicy:
    """An explicitly governed remote branch selected before a new claim."""

    remote: str
    branch: str
    expected_remote_url: str


@dataclass(frozen=True)
class ConversationWorktreePolicy:
    enabled: bool
    source_worktree: Path | None
    worktree_root: Path | None
    branch_prefix: str = "hermes/session"
    bootstrap: bool = False
    bootstrap_command: tuple[str, ...] = ()
    bootstrap_timeout: float = 300.0
    create_timeout: float = 60.0
    retain_until_explicit_cleanup: bool = True
    legacy_location: bool = False
    creation_base: ConversationCreationBasePolicy | None = None


_BRANCH_FORBIDDEN = frozenset(" ~^:?*[\\")


def _section(config: Mapping[str, object]) -> tuple[Mapping[str, object], bool]:
    if "conversation_worktree" in config:
        section = config["conversation_worktree"]
        if section is None:
            # ``DEFAULT_CONFIG`` uses None as a presence sentinel. Deep merge
            # preserves it only when no user top-level policy exists, so the
            # legacy desktop block remains observable during the migration.
            pass
        elif not isinstance(section, Mapping):
            raise ConversationWorktreePolicyError("conversation_worktree must be a mapping")
        else:
            return section, False

    desktop = config.get("desktop", {})
    if not isinstance(desktop, Mapping):
        raise ConversationWorktreePolicyError("desktop must be a mapping")
    section = desktop.get("conversation_worktree", {})
    if not isinstance(section, Mapping):
        raise ConversationWorktreePolicyError("desktop.conversation_worktree must be a mapping")
    return section, bool(section)


def _boolean(section: Mapping[str, object], field: str, default: bool) -> bool:
    value = section.get(field, default)
    if not isinstance(value, bool):
        raise ConversationWorktreePolicyError(f"{field} must be a boolean")
    return value


def _absolute_path(section: Mapping[str, object], field: str) -> Path | None:
    value = section.get(field)
    if value is None or value == "":
        return None
    if not isinstance(value, (str, Path)):
        raise ConversationWorktreePolicyError(f"{field} must be an absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ConversationWorktreePolicyError(f"{field} must be an absolute path")
    return path.resolve()


def _positive_timeout(section: Mapping[str, object], field: str, default: float) -> float:
    value = section.get(field, default)
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ConversationWorktreePolicyError(f"{field} must be a finite positive number")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ConversationWorktreePolicyError(f"{field} must be a finite positive number")
    return value


def _branch_prefix(section: Mapping[str, object]) -> str:
    value = section.get("branch_prefix", "hermes/session")
    if not isinstance(value, str):
        raise ConversationWorktreePolicyError("branch_prefix must be a non-empty safe branch prefix")
    prefix = value.strip()
    components = prefix.split("/")
    if (
        not prefix
        or prefix.startswith("/")
        or prefix.endswith("/")
        or "//" in prefix
        or ".." in prefix
        or "@{" in prefix
        or prefix.endswith(".")
        or prefix.endswith(".lock")
        or prefix == "@"
        or any(component.startswith(".") or component.endswith(".lock") for component in components)
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in prefix)
        or any(char in _BRANCH_FORBIDDEN or char.isspace() for char in prefix)
    ):
        raise ConversationWorktreePolicyError("branch_prefix must be a non-empty safe branch prefix")
    return prefix


def _bootstrap_command(section: Mapping[str, object]) -> tuple[str, ...]:
    value: Any = section.get("bootstrap_command", [])
    if not isinstance(value, list) or any(not isinstance(arg, str) or not arg for arg in value):
        raise ConversationWorktreePolicyError("bootstrap_command must be a list of non-empty strings")
    return tuple(value)


def _creation_base(section: Mapping[str, object]) -> ConversationCreationBasePolicy | None:
    configured = section.get("creation_base")
    if configured is not None:
        required = {"remote", "branch", "expected_remote_url"}
        if not isinstance(configured, Mapping) or set(configured) != required:
            raise ConversationWorktreePolicyError("creation_base requires remote, branch and expected_remote_url")
        if any(not isinstance(configured[key], str) or not configured[key] for key in required):
            raise ConversationWorktreePolicyError("creation_base fields must be non-empty strings")
        configured_base = _validated_creation_base(
            configured["remote"], configured["branch"], configured["expected_remote_url"],
        )
    else:
        configured_base = None
    guarded_base = _guarded_bootstrap_creation_base(section)
    if (
        configured_base is not None
        and guarded_base is not None
        and (
            configured_base.remote != guarded_base.remote
            or configured_base.branch != guarded_base.branch
            or repository_identity(configured_base.expected_remote_url)
            != repository_identity(guarded_base.expected_remote_url)
        )
    ):
        raise ConversationWorktreePolicyError(
            "creation_base conflicts with the governed bootstrap remote, branch or repository"
        )
    return configured_base or guarded_base


def _validated_creation_base(remote: str, branch: str, expected: str) -> ConversationCreationBasePolicy:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", remote):
        raise ConversationWorktreePolicyError("creation_base.remote must be a safe remote name")
    try:
        branch = _branch_prefix({"branch_prefix": branch})
    except ConversationWorktreePolicyError as exc:
        raise ConversationWorktreePolicyError("creation_base.branch must be a safe branch name") from exc
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in expected):
        raise ConversationWorktreePolicyError("creation_base.expected_remote_url contains control characters")
    return ConversationCreationBasePolicy(remote, branch, expected)


def _guarded_bootstrap_creation_base(section: Mapping[str, object]) -> ConversationCreationBasePolicy | None:
    command = _bootstrap_command(section)
    guarded_names = {"hermes-conversation-base-guard", "conversation_worktree_base_guard.py"}
    if not any(Path(argument).name in guarded_names for argument in command):
        return None

    def option(name: str) -> str:
        indices = [index for index, value in enumerate(command) if value == name]
        if len(indices) != 1 or indices[0] + 1 == len(command):
            raise ConversationWorktreePolicyError(
                f"governed bootstrap command requires exactly one {name} value"
            )
        return command[indices[0] + 1]

    mode = option("--mode")
    if mode not in {"hermes-agent", "lunabot"}:
        raise ConversationWorktreePolicyError("governed bootstrap mode is unsupported")
    return _validated_creation_base(
        option("--remote"), option("--branch"), option("--expected-remote"),
    )


def repository_identity(value: str) -> str:
    """Normalize common Git URL spellings to one repository identity."""
    raw = value.strip()
    if raw.lower().startswith("file://"):
        parsed = urlsplit(raw)
        path = unquote(parsed.path).rstrip("/")
        if path.lower().endswith(".git"):
            path = path[:-4]
        resolved = str(Path(path).resolve())
        host = (parsed.hostname or "").lower()
        return resolved if host in {"", "localhost"} else f"{host}/{resolved.lstrip('/')}"
    if Path(raw).is_absolute():
        path = raw.rstrip("/")
        if path.lower().endswith(".git"):
            path = path[:-4]
        return str(Path(path).resolve())
    value_without_trailing_slash = raw.rstrip("/")
    if "://" in value_without_trailing_slash:
        parsed = urlsplit(value_without_trailing_slash)
        host = (parsed.hostname or "").lower()
        try:
            port = parsed.port
        except ValueError:
            # Keep malformed authorities distinct instead of silently treating
            # them as the default endpoint.
            return f"invalid:{parsed.netloc}/{_remote_path_identity(parsed.path, host)}".rstrip("/")
        default_port = {"http": 80, "https": 443, "ssh": 22}.get(parsed.scheme)
        authority = f"{host}:{port}" if port is not None and port != default_port else host
        return f"{authority}/{_remote_path_identity(parsed.path, host)}".rstrip("/")
    if ":" in value_without_trailing_slash:
        authority, path = value_without_trailing_slash.rsplit(":", 1)
        host = authority.rsplit("@", 1)[-1].lower()
        return f"{host}/{_remote_path_identity(path, host)}".rstrip("/")
    if "/" in value_without_trailing_slash:
        host, path = value_without_trailing_slash.split("/", 1)
        host = host.lower()
        return f"{host}/{_remote_path_identity(path, host)}".rstrip("/")
    return value_without_trailing_slash


def _remote_path_identity(path: str, host: str) -> str:
    normalized = path.lstrip("/")
    if normalized.lower().endswith(".git"):
        normalized = normalized[:-4]
    # GitHub repository URLs are case-insensitive. Keep path case for other
    # hosts, which may use case-sensitive repository routing.
    return normalized.lower() if host == "github.com" else normalized


def resolve_conversation_worktree_policy(config: Mapping[str, object]) -> ConversationWorktreePolicy:
    """Resolve top-level policy, falling back to desktop.conversation_worktree."""
    if not isinstance(config, Mapping):
        raise ConversationWorktreePolicyError("configuration must be a mapping")

    section, legacy_location = _section(config)
    enabled = _boolean(section, "enabled", False)
    source_worktree = _absolute_path(section, "source_worktree")
    worktree_root = _absolute_path(section, "worktree_root")
    retain_until_explicit_cleanup = _boolean(section, "retain_until_explicit_cleanup", True)
    bootstrap = _boolean(section, "bootstrap", False)
    bootstrap_command = _bootstrap_command(section)

    if enabled and source_worktree is None:
        raise ConversationWorktreePolicyError("source_worktree is required when enabled")
    if enabled and worktree_root is None:
        raise ConversationWorktreePolicyError("worktree_root is required when enabled")
    if enabled and not retain_until_explicit_cleanup:
        raise ConversationWorktreePolicyError(
            "retain_until_explicit_cleanup must be true when enabled"
        )
    if bootstrap and not bootstrap_command:
        raise ConversationWorktreePolicyError(
            "bootstrap_command must be non-empty when bootstrap is enabled"
        )

    return ConversationWorktreePolicy(
        enabled=enabled,
        source_worktree=source_worktree,
        worktree_root=worktree_root,
        branch_prefix=_branch_prefix(section),
        bootstrap=bootstrap,
        bootstrap_command=bootstrap_command,
        bootstrap_timeout=_positive_timeout(section, "bootstrap_timeout", 300.0),
        create_timeout=_positive_timeout(section, "create_timeout", 60.0),
        retain_until_explicit_cleanup=retain_until_explicit_cleanup,
        legacy_location=legacy_location,
        creation_base=_creation_base(section),
    )

"""Bounded, read-only adapter for the shared agent-learning Vault catalog."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Optional

from agent.skill_utils import parse_frontmatter
from datetime import UTC


MAX_NOTE_BYTES = 16 * 1024
MAX_SUMMARY_CHARS = 1200
MAX_CATALOG_FILES = 5000
_AUTHORITIES = {"source-index", "narrative-only"}
_AGENTS = {"claude", "codex", "hermes", "shared"}
_KINDS = {"memory", "working-preference", "skill-reference"}
_CATALOG_ROOT_PARTS = (
    *(("Memories", agent) for agent in ("Claude", "Codex", "Hermes", "Shared")),
    *(("Reference", "Agent Skills", agent) for agent in ("Claude", "Codex", "Hermes")),
)


@dataclass(frozen=True)
class ExactVaultNoteRequest:
    """Immutable selection that a trusted host policy must authorize in full."""

    vault_root: str
    root_device: int
    root_inode: int
    relative_path: str
    expected_record_id: str
    expected_sha256: str
    max_bytes: int = MAX_NOTE_BYTES


@dataclass(frozen=True)
class VaultLearningNode:
    record_id: str
    label: str
    kind: str
    origin_agent: str
    area: str
    status: str
    timestamp: Optional[int]
    related_record_ids: tuple[str, ...]
    summary: str
    execution_status: str


@dataclass(frozen=True)
class VaultLearningDiagnostic:
    reason_code: str
    source_label: str = ""


def _text(value: object) -> str:
    return str(value or "").strip()


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(_text(item) for item in value if _text(item))
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            return tuple(
                _text(item) for item in stripped[1:-1].split(",") if _text(item)
            )
    return ()


def _timestamp(metadata: dict[str, Any], fallback_mtime: float | None) -> Optional[int]:
    for key in ("verified_at", "observed_at", "created_at"):
        value = _text(metadata.get(key))
        if not value:
            continue
        try:
            from datetime import datetime

            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return int(parsed.timestamp())
        except (ValueError, OverflowError):
            continue
    return int(fallback_mtime) if fallback_mtime is not None else None


def _catalog_roots(vault: Path) -> Iterable[Path]:
    for parts in _CATALOG_ROOT_PARTS:
        yield vault.joinpath(*parts)


def _sorted_catalog_paths(root: Path, limit: int) -> Iterable[Path]:
    """Walk a catalog root in stable depth-first order without reading notes."""

    if limit <= 0:
        return

    try:
        children = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError:
        return

    for path in children:
        try:
            if path.is_dir():
                if path.is_symlink():
                    continue
                for child in _sorted_catalog_paths(path, limit):
                    yield child
                    limit -= 1
                    if limit <= 0:
                        return
            elif path.is_file() and path.match("*.md"):
                yield path
                limit -= 1
        except OSError:
            continue


def _catalog_files(vault: Path) -> Iterable[Path]:
    seen = 0
    for root in _catalog_roots(vault):
        if not root.exists():
            continue
        remaining = MAX_CATALOG_FILES - seen
        for path in _sorted_catalog_paths(root, remaining):
            if seen >= MAX_CATALOG_FILES:
                return
            seen += 1
            yield path


def _relative_label(path: Path, vault: Path) -> str:
    try:
        return path.resolve().relative_to(vault).as_posix()
    except (OSError, ValueError):
        return path.name


def _read_note(
    path: Path, vault: Path
) -> tuple[str | None, VaultLearningDiagnostic | None]:
    label = _relative_label(path, vault)
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(vault) or not resolved.is_file():
            return None, VaultLearningDiagnostic("note_outside_vault", label)
        if resolved.stat().st_size > MAX_NOTE_BYTES:
            return None, VaultLearningDiagnostic("note_too_large", label)
        return resolved.read_text(encoding="utf-8-sig"), None
    except UnicodeDecodeError:
        return None, VaultLearningDiagnostic("invalid_utf8", label)
    except OSError:
        return None, VaultLearningDiagnostic("note_unreadable", label)


def _node_from_note(
    path: Path, vault: Path
) -> tuple[VaultLearningNode | None, VaultLearningDiagnostic | None]:
    raw, diagnostic = _read_note(path, vault)
    if raw is None:
        return None, diagnostic
    node, diagnostic = _node_from_text(raw, _relative_label(path, vault), fallback_title=path.stem)
    if node is not None and node.timestamp is None:
        try:
            node = replace(node, timestamp=int(path.stat().st_mtime))
        except OSError:
            pass  # Preserve the catalog's optional pathname fallback.
    return node, diagnostic


def _node_from_text(
    raw: str, label: str, *, fallback_mtime: float | None = None,
    fallback_title: str | None = None
) -> tuple[VaultLearningNode | None, VaultLearningDiagnostic | None]:
    """Parse captured text without filesystem, config, or catalog access."""
    metadata, body = parse_frontmatter(raw)
    record_id = _text(metadata.get("record_id"))
    if not record_id:
        return None, VaultLearningDiagnostic("record_id_required", label)
    if _text(metadata.get("schema_name")) != "agent_learning_record_v1":
        return None, VaultLearningDiagnostic("unsupported_schema", label)
    if not _truthy(metadata.get("sync_owned")):
        return None, VaultLearningDiagnostic("sync_owned_required", label)
    if _text(metadata.get("classification")) != "diagnostic-only":
        return None, VaultLearningDiagnostic("unsupported_classification", label)
    if _text(metadata.get("authority")) not in _AUTHORITIES:
        return None, VaultLearningDiagnostic("unsupported_authority", label)
    source_kind = _text(metadata.get("kind"))
    if source_kind not in _KINDS:
        return None, VaultLearningDiagnostic("unsupported_kind", label)
    origin_agent = _text(metadata.get("agent")).lower()
    if origin_agent not in _AGENTS:
        return None, VaultLearningDiagnostic("unsupported_agent", label)
    execution_status = _text(metadata.get("execution_status"))
    if source_kind == "skill-reference" and execution_status != "reference-only":
        return None, VaultLearningDiagnostic("skill_not_reference_only", label)
    summary = body.strip()[:MAX_SUMMARY_CHARS]
    graph_kind = (
        "skill-reference" if source_kind == "skill-reference" else "shared-memory"
    )
    return (
        VaultLearningNode(
            record_id=record_id,
            label=_text(metadata.get("title")) or (
                fallback_title if fallback_title is not None else PurePosixPath(label).stem),
            kind=graph_kind,
            origin_agent=origin_agent,
            area=_text(metadata.get("memory_area")) or "General",
            status=_text(metadata.get("status")) or "unknown",
            timestamp=_timestamp(metadata, fallback_mtime),
            related_record_ids=_strings(metadata.get("related_record_ids")),
            summary=summary,
            execution_status=execution_status or "not-applicable",
        ),
        None,
    )


def read_vault_learning(
    vault_dir: Path,
) -> tuple[list[VaultLearningNode], list[VaultLearningDiagnostic]]:
    """Read allowlisted normalized notes; individual failures stay diagnostic."""

    try:
        vault = vault_dir.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return [], [VaultLearningDiagnostic("vault_unavailable")]
    if not vault.is_dir():
        return [], [VaultLearningDiagnostic("vault_unavailable")]

    nodes: list[VaultLearningNode] = []
    diagnostics: list[VaultLearningDiagnostic] = []
    seen: set[str] = set()
    for path in _catalog_files(vault):
        node, diagnostic = _node_from_note(path, vault)
        if diagnostic is not None:
            diagnostics.append(diagnostic)
            continue
        if node is None:
            continue
        if node.record_id in seen:
            diagnostics.append(
                VaultLearningDiagnostic(
                    "duplicate_record_id", _relative_label(path, vault)
                )
            )
            continue
        seen.add(node.record_id)
        nodes.append(node)
    return nodes, diagnostics



def read_exact_vault_learning(
    request: ExactVaultNoteRequest,
    *,
    authorize: Callable[[ExactVaultNoteRequest], bool],
) -> tuple[VaultLearningNode | None, VaultLearningDiagnostic | None]:
    """Read one approved note without catalog traversal or implicit configuration.

    The application/operator must select a trusted policy and its authenticated
    principal OUTSIDE note/model/request data. The policy must bind every request
    field, including root identity, raw SHA256 and byte limit, and return exactly
    True. There is no default allow or approval token. A caller supplying its own
    allow callback is not independently authenticated; this in-process contract
    is not a sandbox against arbitrary or compromised Python callers.

    No HTTP/tool/CLI caller is wired here. The return proves normalized admission
    of the captured bytes, not live-session/model use or global ID uniqueness.
    Platforms without safe descriptor operations refuse without a weaker fallback.
    """
    from agent.learning_vault_exact import read_exact

    return read_exact(request, authorize=authorize)

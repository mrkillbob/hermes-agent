from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hermes_cli.secure_worker import (
    PackPolicy,
    SecurityBoundaryError,
    audit_profile_boundary,
    build_context_pack,
    destroy_context_pack,
    render_local_safe_profile,
    render_ox_profile,
    verify_proposed_diff,
    verify_context_pack,
)


PINNED_IMAGE = "hermes-secure-worker@sha256:" + "a" * 64


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    )
    return result.stdout.strip()


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "secure-worker@example.invalid")
    _git(repo, "config", "user.name", "Secure Worker Test")
    (repo / "src").mkdir()
    (repo / "src" / "logic.py").write_text("def answer():\n    return 42\n")
    (repo / "README.md").write_text("# safe fixture\n")
    _git(repo, "add", "src/logic.py", "README.md")
    _git(repo, "commit", "-qm", "fixture")
    return repo


@pytest.fixture
def policy() -> PackPolicy:
    return PackPolicy(
        policy_version="ox-pack-v1",
        max_file_bytes=4096,
        max_pack_bytes=8192,
        excluded_segments=(".git", ".env", "secrets", "credentials", "data"),
    )


def test_build_pack_copies_only_explicit_tracked_files_and_verifies(
    source_repo: Path, tmp_path: Path, policy: PackPolicy
) -> None:
    pack = tmp_path / "pack"
    manifest_path = tmp_path / "manifest.json"

    manifest = build_context_pack(
        source_repo, ["src/logic.py"], pack, manifest_path, policy
    )

    assert (pack / "src" / "logic.py").read_text().endswith("return 42\n")
    assert not (pack / "README.md").exists()
    assert (pack / ".git").is_dir()
    assert manifest.source_commit == _git(source_repo, "rev-parse", "HEAD")
    assert [entry.path for entry in manifest.files] == ["src/logic.py"]
    assert manifest.files[0].sha256 == hashlib.sha256(
        (source_repo / "src" / "logic.py").read_bytes()
    ).hexdigest()
    assert verify_context_pack(pack, manifest_path, policy).manifest_sha256


@pytest.mark.parametrize(
    "selected",
    ["../outside.py", "/tmp/outside.py", ".git/config", "src/../README.md"],
)
def test_pack_rejects_path_escape_and_excluded_paths(
    source_repo: Path, tmp_path: Path, policy: PackPolicy, selected: str
) -> None:
    with pytest.raises(SecurityBoundaryError):
        build_context_pack(
            source_repo, [selected], tmp_path / "pack", tmp_path / "manifest.json", policy
        )


def test_pack_rejects_symlink(source_repo: Path, tmp_path: Path, policy: PackPolicy) -> None:
    (source_repo / "link.py").symlink_to(source_repo / "src" / "logic.py")
    _git(source_repo, "add", "link.py")
    _git(source_repo, "commit", "-qm", "add link")
    with pytest.raises(SecurityBoundaryError, match="symlink"):
        build_context_pack(
            source_repo, ["link.py"], tmp_path / "pack", tmp_path / "manifest.json", policy
        )


def test_pack_rejects_dirty_source(source_repo: Path, tmp_path: Path, policy: PackPolicy) -> None:
    (source_repo / "src" / "logic.py").write_text("changed\n")
    with pytest.raises(SecurityBoundaryError, match="clean"):
        build_context_pack(
            source_repo,
            ["src/logic.py"],
            tmp_path / "pack",
            tmp_path / "manifest.json",
            policy,
        )


def test_pack_rejects_untracked_file(source_repo: Path, tmp_path: Path, policy: PackPolicy) -> None:
    (source_repo / "scratch.py").write_text("print('untracked')\n")
    with pytest.raises(SecurityBoundaryError, match="clean"):
        build_context_pack(
            source_repo, ["scratch.py"], tmp_path / "pack", tmp_path / "manifest.json", policy
        )


def test_pack_rejects_binary(source_repo: Path, tmp_path: Path, policy: PackPolicy) -> None:
    (source_repo / "src" / "blob.bin").write_bytes(b"safe-prefix\x00binary")
    _git(source_repo, "add", "src/blob.bin")
    _git(source_repo, "commit", "-qm", "binary")
    with pytest.raises(SecurityBoundaryError, match="binary"):
        build_context_pack(
            source_repo, ["src/blob.bin"], tmp_path / "pack", tmp_path / "manifest.json", policy
        )


@pytest.mark.parametrize(
    "secret",
    [
        "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz123456",
        "-----BEGIN PRIVATE KEY-----",
        "AWS_SECRET_ACCESS_KEY=abcdefghijklmnopqrstuvwxyz1234567890",
    ],
)
def test_pack_rejects_secret_like_content(
    source_repo: Path, tmp_path: Path, policy: PackPolicy, secret: str
) -> None:
    (source_repo / "src" / "logic.py").write_text(secret + "\n")
    _git(source_repo, "add", "src/logic.py")
    _git(source_repo, "commit", "-qm", "secret fixture")
    with pytest.raises(SecurityBoundaryError, match="secret"):
        build_context_pack(
            source_repo,
            ["src/logic.py"],
            tmp_path / "pack",
            tmp_path / "manifest.json",
            policy,
        )


def test_pack_enforces_file_and_total_budgets(
    source_repo: Path, tmp_path: Path, policy: PackPolicy
) -> None:
    (source_repo / "src" / "logic.py").write_text("x" * 5000)
    _git(source_repo, "add", "src/logic.py")
    _git(source_repo, "commit", "-qm", "oversize")
    with pytest.raises(SecurityBoundaryError, match="file budget"):
        build_context_pack(
            source_repo,
            ["src/logic.py"],
            tmp_path / "pack",
            tmp_path / "manifest.json",
            policy,
        )


def test_manifest_is_canonical_and_tampering_is_detected(
    source_repo: Path, tmp_path: Path, policy: PackPolicy
) -> None:
    pack = tmp_path / "pack"
    manifest_path = tmp_path / "manifest.json"
    build_context_pack(source_repo, ["src/logic.py"], pack, manifest_path, policy)
    raw = manifest_path.read_bytes()
    assert raw.endswith(b"\n")
    assert raw == json.dumps(
        json.loads(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode() + b"\n"

    (pack / "src" / "logic.py").write_text("tampered\n")
    with pytest.raises(SecurityBoundaryError, match="hash"):
        verify_context_pack(pack, manifest_path, policy)


def _fresh_attestation(now: datetime) -> dict[str, object]:
    return {
        "schema": "hermes.secure-worker.privacy-attestation.v1",
        "provider": "nous",
        "privacy_mode": True,
        "attested_at": (now - timedelta(minutes=5)).isoformat(),
        "expires_at": (now + timedelta(minutes=55)).isoformat(),
    }


def test_rendered_ox_profile_passes_when_every_boundary_is_present(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    now = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)
    config = render_ox_profile(
        pack_root=pack,
        broker_python="/opt/hermes-worker/bin/python",
        staging_owner="private-staging",
        staging_repo="ox-proposals",
        worker_image=PINNED_IMAGE,
    )

    report = audit_profile_boundary(
        config,
        pack_root=pack,
        privacy_attestation=_fresh_attestation(now),
        docker_available=True,
        admitted_worker_image=PINNED_IMAGE,
        now=now,
    )

    assert report.allowed is True
    assert report.reasons == ()
    assert config["mcp_servers"]["secure-github-staging"]["env"][
        "HERMES_STAGING_GITHUB_TOKEN"
    ] == "${HERMES_STAGING_GITHUB_TOKEN}"


def test_ox_broker_receives_only_explicit_staging_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.mcp_tool import _build_safe_env, _interpolate_env_vars

    config = render_ox_profile(
        tmp_path,
        "/opt/hermes-worker/bin/python",
        "private-staging",
        "ox-proposals",
        PINNED_IMAGE,
    )
    monkeypatch.setenv("HERMES_STAGING_GITHUB_TOKEN", "github_pat_staging_only")
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_real_repository")
    monkeypatch.setenv("GH_TOKEN", "github_pat_real_cli")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-cloud-key")

    broker = _interpolate_env_vars(
        config["mcp_servers"]["secure-github-staging"]
    )
    child_env = _build_safe_env(broker["env"])

    assert child_env["HERMES_STAGING_GITHUB_TOKEN"] == "github_pat_staging_only"
    assert "GITHUB_TOKEN" not in child_env
    assert "GH_TOKEN" not in child_env
    assert "OPENAI_API_KEY" not in child_env


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda cfg, tmp: cfg["terminal"].update(cwd=str(tmp / "production-repo")), "cwd"),
        (lambda cfg, tmp: cfg["terminal"].update(backend="local"), "Docker"),
        (lambda cfg, tmp: cfg["terminal"].update(docker_image="python:latest"), "pinned"),
        (lambda cfg, tmp: cfg["terminal"].update(docker_network=True), "network"),
        (lambda cfg, tmp: cfg["terminal"].update(docker_isolate_host_data=False), "host credential"),
        (lambda cfg, tmp: cfg["terminal"].update(container_persistent=True), "persistent"),
        (
            lambda cfg, tmp: cfg["terminal"].update(docker_persist_across_processes=True),
            "reuse",
        ),
        (lambda cfg, tmp: cfg["terminal"].update(docker_forward_env=["GH_TOKEN"]), "environment"),
        (lambda cfg, tmp: cfg["terminal"].update(docker_env={"TOKEN": "secret"}), "environment"),
        (lambda cfg, tmp: cfg["terminal"].update(docker_run_as_host_user=True), "host user"),
        (lambda cfg, tmp: cfg["terminal"].update(docker_volumes=[f"{tmp}:/host"]), "volume"),
        (lambda cfg, tmp: cfg["terminal"].update(docker_extra_args=["--privileged"]), "extra"),
        (lambda cfg, tmp: cfg["toolsets"].append("web"), "web"),
        (
            lambda cfg, tmp: cfg["mcp_servers"].update(
                arbitrary={"command": "npx", "args": ["-y", "anything"]}
            ),
            "MCP",
        ),
        (
            lambda cfg, tmp: cfg["mcp_servers"]["secure-github-staging"].update(
                env={"HERMES_STAGING_GITHUB_OWNER": ""}
            ),
            "MCP",
        ),
        (
            lambda cfg, tmp: cfg["mcp_servers"]["secure-github-staging"]["env"].update(
                HERMES_STAGING_GITHUB_TOKEN="github_pat_literal_secret"
            ),
            "MCP",
        ),
        (lambda cfg, tmp: cfg.update(fallback_providers=[{"provider": "anthropic"}]), "fallback"),
    ],
)
def test_remote_profile_mutations_fail_closed(
    tmp_path: Path, mutation, reason: str
) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    now = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)
    config = render_ox_profile(pack, "/pinned/python", "owner", "staging", PINNED_IMAGE)
    mutation(config, tmp_path)
    report = audit_profile_boundary(
        config,
        pack_root=pack,
        privacy_attestation=_fresh_attestation(now),
        docker_available=True,
        admitted_worker_image=PINNED_IMAGE,
        now=now,
    )
    assert report.allowed is False
    assert any(reason.casefold() in item.casefold() for item in report.reasons)


def test_remote_profile_denies_docker_down_without_host_fallback(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    now = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)
    config = render_ox_profile(pack, "/pinned/python", "owner", "staging", PINNED_IMAGE)
    report = audit_profile_boundary(
        config,
        pack_root=pack,
        privacy_attestation=_fresh_attestation(now),
        docker_available=False,
        admitted_worker_image=PINNED_IMAGE,
        now=now,
    )
    assert report.allowed is False
    assert any("Docker unavailable" in item for item in report.reasons)
    assert config["terminal"]["backend"] == "docker"


@pytest.mark.parametrize(
    "attestation",
    [
        None,
        {},
        {"schema": "wrong", "provider": "nous", "privacy_mode": True},
        {
            "schema": "hermes.secure-worker.privacy-attestation.v1",
            "provider": "nous",
            "privacy_mode": False,
            "attested_at": "2026-08-23T19:00:00+00:00",
            "expires_at": "2026-08-23T21:00:00+00:00",
        },
        {
            "schema": "hermes.secure-worker.privacy-attestation.v1",
            "provider": "nous",
            "privacy_mode": True,
            "attested_at": "2026-08-23T18:00:00+00:00",
            "expires_at": "2026-08-23T19:00:00+00:00",
        },
    ],
)
def test_remote_profile_denies_missing_invalid_or_stale_attestation(
    tmp_path: Path, attestation: dict[str, object] | None
) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    now = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)
    report = audit_profile_boundary(
        render_ox_profile(pack, "/pinned/python", "owner", "staging", PINNED_IMAGE),
        pack_root=pack,
        privacy_attestation=attestation,
        docker_available=True,
        admitted_worker_image=PINNED_IMAGE,
        now=now,
    )
    assert report.allowed is False
    assert any("attestation" in item.casefold() for item in report.reasons)


def test_local_safe_profile_has_no_remote_fallback_and_passes_without_attestation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "isolated-local-worktree"
    source.mkdir()
    config = render_local_safe_profile(
        source, model="hermes-qwen3-fast", worker_image=PINNED_IMAGE
    )
    report = audit_profile_boundary(
        config,
        pack_root=None,
        privacy_attestation=None,
        docker_available=True,
        admitted_worker_image=PINNED_IMAGE,
    )
    assert report.allowed is True
    assert config["fallback_providers"] == []


def test_local_content_addressed_worker_image_is_admitted(tmp_path: Path) -> None:
    """A private local build must not require a registry push to remain immutable."""

    source = tmp_path / "isolated-local-worktree"
    source.mkdir()
    local_image_id = "sha256:" + "b" * 64

    config = render_local_safe_profile(
        source, model="hermes-qwen3-fast", worker_image=local_image_id
    )
    report = audit_profile_boundary(
        config,
        pack_root=None,
        privacy_attestation=None,
        docker_available=True,
        admitted_worker_image=local_image_id,
    )

    assert report.allowed is True
    assert config["terminal"]["docker_image"] == local_image_id


def test_repo_capable_local_profile_rejects_remote_fallback(tmp_path: Path) -> None:
    source = tmp_path / "isolated-local-worktree"
    source.mkdir()
    config = render_local_safe_profile(
        source, model="hermes-qwen3-fast", worker_image=PINNED_IMAGE
    )
    config["fallback_providers"] = [{"provider": "openai-codex", "model": "gpt-5.6-sol"}]
    report = audit_profile_boundary(
        config,
        pack_root=None,
        privacy_attestation=None,
        docker_available=True,
        admitted_worker_image=PINNED_IMAGE,
    )
    assert report.allowed is False
    assert any("cloud fallback" in item.casefold() for item in report.reasons)


def _built_pack(source_repo: Path, tmp_path: Path, policy: PackPolicy) -> tuple[Path, Path]:
    pack = tmp_path / "pack"
    manifest = tmp_path / "manifest.json"
    build_context_pack(source_repo, ["src/logic.py"], pack, manifest, policy)
    return pack, manifest


def test_diff_verifier_accepts_safe_modified_approved_file(
    source_repo: Path, tmp_path: Path, policy: PackPolicy
) -> None:
    pack, manifest = _built_pack(source_repo, tmp_path, policy)
    (pack / "src" / "logic.py").write_text("def answer():\n    return 43\n")
    receipt = verify_proposed_diff(pack, manifest, policy)
    assert receipt.changed_paths == ("src/logic.py",)
    assert len(receipt.diff_sha256) == 64


def test_diff_verifier_rejects_unexpected_path(
    source_repo: Path, tmp_path: Path, policy: PackPolicy
) -> None:
    pack, manifest = _built_pack(source_repo, tmp_path, policy)
    (pack / "unexpected.py").write_text("print('new')\n")
    with pytest.raises(SecurityBoundaryError, match="outside approved pack"):
        verify_proposed_diff(pack, manifest, policy)


def test_diff_verifier_rejects_deletion(
    source_repo: Path, tmp_path: Path, policy: PackPolicy
) -> None:
    pack, manifest = _built_pack(source_repo, tmp_path, policy)
    (pack / "src" / "logic.py").unlink()
    with pytest.raises(SecurityBoundaryError, match="deletion"):
        verify_proposed_diff(pack, manifest, policy)


def test_diff_verifier_rejects_binary_or_secret_addition(
    source_repo: Path, tmp_path: Path, policy: PackPolicy
) -> None:
    pack, manifest = _built_pack(source_repo, tmp_path, policy)
    (pack / "src" / "logic.py").write_bytes(b"GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz123456\n")
    with pytest.raises(SecurityBoundaryError, match="secret"):
        verify_proposed_diff(pack, manifest, policy)
    (pack / "src" / "logic.py").write_bytes(b"prefix\x00binary")
    with pytest.raises(SecurityBoundaryError, match="binary"):
        verify_proposed_diff(pack, manifest, policy)


def test_diff_verifier_rejects_manifest_from_another_pack(
    source_repo: Path, tmp_path: Path, policy: PackPolicy
) -> None:
    pack, manifest = _built_pack(source_repo, tmp_path, policy)
    marker = pack / ".git" / "hermes-secure-worker-pack.json"
    marker.write_text('{"manifest_sha256":"' + "0" * 64 + '"}\n')
    with pytest.raises(SecurityBoundaryError, match="manifest binding"):
        verify_proposed_diff(pack, manifest, policy)


def test_destroy_removes_only_bound_pack_and_writes_content_free_receipt(
    source_repo: Path, tmp_path: Path, policy: PackPolicy
) -> None:
    pack, manifest = _built_pack(source_repo, tmp_path, policy)
    receipt = tmp_path / "destroy-receipt.json"
    source_commit = _git(source_repo, "rev-parse", "HEAD")
    destroy_context_pack(pack, manifest, receipt, quarantine_root=tmp_path / "quarantine")
    assert not pack.exists()
    row = json.loads(receipt.read_text())
    assert row["status"] == "destroyed"
    assert row["source_commit"] == source_commit
    assert set(row) == {
        "schema",
        "status",
        "timestamp",
        "source_commit",
        "manifest_sha256",
    }


def test_destroy_rejects_unbound_directory(tmp_path: Path) -> None:
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    with pytest.raises(SecurityBoundaryError, match="marker"):
        destroy_context_pack(
            ordinary,
            manifest,
            tmp_path / "receipt.json",
            quarantine_root=tmp_path / "quarantine",
        )
    assert ordinary.exists()

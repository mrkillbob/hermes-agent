from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from hermes_cli import secure_worker_cli


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    secure_worker_cli.register_cli(parser)
    return parser


def test_cli_registers_required_commands() -> None:
    parser = _parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    assert set(subparsers.choices) == {
        "audit",
        "pack",
        "verify",
        "profile-render",
        "attest",
        "destroy",
    }


def test_remote_audit_parser_accepts_manifest_and_policy_proofs(tmp_path: Path) -> None:
    parser = _parser()
    args = parser.parse_args(
        [
            "audit",
            "--config",
            str(tmp_path / "profile.yaml"),
            "--pack",
            str(tmp_path / "pack"),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--policy",
            str(tmp_path / "policy.json"),
            "--image-lock",
            str(tmp_path / "image-lock.json"),
        ]
    )
    assert args.manifest.endswith("manifest.json")
    assert args.policy.endswith("policy.json")


def test_attest_requires_explicit_privacy_confirmation(tmp_path: Path) -> None:
    parser = _parser()
    output = tmp_path / "attestation.json"
    args = parser.parse_args(["attest", "--output", str(output)])
    assert secure_worker_cli.cmd_attest(args) == 1
    assert not output.exists()


def test_attest_writes_bounded_machine_readable_attestation(tmp_path: Path) -> None:
    parser = _parser()
    output = tmp_path / "attestation.json"
    args = parser.parse_args(
        [
            "attest",
            "--output",
            str(output),
            "--confirm-privacy-mode",
            "--ttl-minutes",
            "30",
        ]
    )
    assert secure_worker_cli.cmd_attest(args) == 0
    row = json.loads(output.read_text())
    assert row["privacy_mode"] is True
    assert row["provider"] == "nous"


def test_attest_rejects_excessive_ttl(tmp_path: Path) -> None:
    parser = _parser()
    output = tmp_path / "attestation.json"
    args = parser.parse_args(
        [
            "attest",
            "--output",
            str(output),
            "--confirm-privacy-mode",
            "--ttl-minutes",
            "121",
        ]
    )
    assert secure_worker_cli.cmd_attest(args) == 1
    assert not output.exists()


def test_profile_render_refuses_existing_destination(tmp_path: Path) -> None:
    parser = _parser()
    output = tmp_path / "profile.yaml"
    output.write_text("owned by user\n")
    args = parser.parse_args(
        [
            "profile-render",
            "--kind",
            "local-safe",
            "--cwd",
            str(tmp_path),
            "--output",
            str(output),
            "--worker-image",
            "worker@sha256:" + "a" * 64,
        ]
    )
    assert secure_worker_cli.cmd_profile_render(args) == 1
    assert output.read_text() == "owned by user\n"

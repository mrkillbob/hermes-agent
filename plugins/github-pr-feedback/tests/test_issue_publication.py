from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from github_pr_feedback import cli_issue_publication as publication


@pytest.fixture
def packet(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "remote",
            "add",
            "origin",
            "https://github.com/mrkillbob/luna-bot.git",
        ],
        check=True,
    )
    monkeypatch.chdir(root)
    (root / ".github/ISSUE_TEMPLATE").mkdir(parents=True)
    (root / ".github/ISSUE_TEMPLATE/hermes-worker-test-failure.yml").write_text(
        "body:\n" + "".join(f"  - id: {field}\n" for field in publication._TEMPLATE_IDS)
    )
    (root / "tests").mkdir()
    (root / "tests/test_pure.py").write_text("def test_contract():\n    assert False\n", encoding="utf-8")
    (root / "owner.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    sha = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    (root / ".venv/bin/python").chmod(0o700)
    subprocess.run(
        ["git", "-C", str(root), "config", "status.showUntrackedFiles", "no"],
        check=True,
    )
    data = {
        "schema_version": 1,
        "repository": "mrkillbob/luna-bot",
        "expected_stable_head": sha,
        "worktree": str(root),
        "test_nodeid": "tests/test_pure.py::test_contract",
        "source_owner": "owner.py",
        "expected_exit_code": 1,
        "task_id": "t_ab123456",
        "profile": "lunabot-issue-reporter",
    }
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return (
        path,
        data,
        SimpleNamespace(
            enabled=True,
            github_identity=SimpleNamespace(expected_login="mrkillbobbot"),
            targets={data["repository"]: SimpleNamespace(local_path=root)},
        ),
    )


class GitHub:
    def __init__(self, sha):
        self.sha = sha
        self.login = "mrkillbobbot"
        self.private = True
        self.issues = ()
        self.prs = ()
        self.created = []
        self.reads = 0

    def viewer_login(self):
        return self.login

    def repository_is_private(self, repository):
        return self.private

    def get_branch_head(self, repository, branch):
        self.reads += 1
        return self.sha

    def issue_publication_landscape(self, repository):
        return self.issues, self.prs

    def create_verified_luna_issue(
        self, repository, *, expected_stable_head, title, body
    ):
        self.created.append((title, body))
        return {
            "number": 17,
            "html_url": "https://github.com/mrkillbob/luna-bot/issues/17",
        }


@pytest.mark.parametrize(
    "case",
    [
        "success",
        "wrong_identity",
        "stale_head",
        "duplicate_issue",
        "duplicate_pr",
        "public_repo",
        "passing_repro",
        "dry_run",
        "legacy_duplicate",
        "missing_worktree",
        "head_changes_before_write",
        "wire_transport",
        "wrong_policy_identity",
    ],
)
def test_publication_requires_verified_failure_and_never_republishes(
    packet, monkeypatch, capsys, case
):
    path, data, policy = packet
    if case == "wire_transport":
        from github_pr_feedback.github_client import GitHubClient

        client = GitHubClient()
        calls = []

        def response(argv):
            calls.append(argv)
            if "--paginate" in argv:
                return [
                    [{"number": 1, "body": "first"}],
                    [{"number": 2, "body": "second"}],
                ]
            return {
                "number": 17,
                "html_url": "https://github.com/mrkillbob/luna-bot/issues/17",
            }

        monkeypatch.setattr(client, "_json", response)
        monkeypatch.setattr(client, "viewer_login", lambda: "mrkillbobbot")
        monkeypatch.setattr(client, "repository_is_private", lambda _: True)
        monkeypatch.setattr(
            client, "get_branch_head", lambda *_: data["expected_stable_head"]
        )
        issues, pulls = client.issue_publication_landscape(data["repository"])
        assert len(issues) == len(pulls) == 2
        client.create_verified_luna_issue(
            data["repository"],
            expected_stable_head=data["expected_stable_head"],
            title="Failure Δ",
            body="Structural UTF-8 Δ",
        )
        assert all("--paginate" in argv and "--slurp" in argv for argv in calls[:2])
        assert "state=all" in calls[0][-1] and "state=open" in calls[1][-1]
        assert "--raw-field" in calls[-1] and "body=Structural UTF-8 Δ" in calls[-1]
        return
    github = GitHub(data["expected_stable_head"])
    factory_calls = []

    def factory(_policy):
        factory_calls.append(True)
        return github

    if case == "wrong_policy_identity":
        policy.github_identity.expected_login = "mrkillbob"
    if case == "wrong_identity":
        github.login = "mrkillbob"
    if case == "stale_head":
        github.sha = "b" * 40
    if case == "public_repo":
        github.private = False
    marker = publication.fingerprint_marker(data)
    if case == "duplicate_issue":
        github.issues = ({"number": 9, "body": marker},)
    if case == "duplicate_pr":
        github.prs = ({"number": 10, "body": marker},)
    if case == "legacy_duplicate":
        github.issues = (
            {
                "number": 9,
                "body": f"hermes-worker:mrkillbob/luna-bot:{'c' * 40}:{publication.command_fingerprint(data)}",
            },
        )
    if case == "missing_worktree":
        data.pop("worktree")
        path.write_text(json.dumps(data), encoding="utf-8")
    if case == "head_changes_before_write":

        def changing_head(*_):
            github.reads += 1
            return github.sha if github.reads == 1 else "c" * 40

        github.get_branch_head = changing_head
    if case == "passing_repro":
        monkeypatch.setattr(
            publication, "_run_reproduction", lambda *_: {"exit_code": 0}
        )
    if case in {
        "wrong_identity",
        "wrong_policy_identity",
        "stale_head",
        "public_repo",
        "head_changes_before_write",
    }:
        with pytest.raises(ValueError):
            publication.publish_issue(
                policy,
                path,
                expected_stable_head=data["expected_stable_head"],
                client_factory=factory,
            )
    else:
        result = publication.publish_issue(
            policy,
            path,
            expected_stable_head=data["expected_stable_head"],
            client_factory=factory,
            dry_run=case == "dry_run",
        )
        assert (
            result["status"]
            == {
                "success": "created",
                "duplicate_issue": "duplicate",
                "duplicate_pr": "competing_pr",
                "passing_repro": "idle",
                "dry_run": "validated",
                "legacy_duplicate": "duplicate",
                "missing_worktree": "created",
            }[case]
        )
    assert len(github.created) == (case in {"success", "missing_worktree"})
    if case == "dry_run":
        assert not factory_calls
        import argparse
        from github_pr_feedback import cli

        parser = argparse.ArgumentParser()
        cli.setup_cli(None, parser)
        args = parser.parse_args([
            "publish-issue",
            "--repository",
            data["repository"],
            "--packet",
            str(path),
            "--expected-stable-head",
            data["expected_stable_head"],
            "--dry-run",
        ])
        monkeypatch.setattr(cli, "_load_policy_from_context", lambda _: policy)
        monkeypatch.setattr(
            cli, "_github_client", lambda _: pytest.fail("dry-run accessed credentials")
        )
        assert cli.handle_cli_with_context(object(), args) == 0
        assert json.loads(capsys.readouterr().out)["github_accessed"] is False
    if case == "wrong_policy_identity":
        assert not factory_calls
    if github.created:
        body = github.created[0][1]
        assert str(Path.cwd()) not in body
        assert "assert False" not in body
        assert marker in body
        assert github.reads >= 2


@pytest.mark.parametrize(
    "change",
    [
        {"repository": "mrkillbob/hermes-agent"},
        {"expected_stable_head": "b" * 40},
        {"test_nodeid": "tests/test_live_broker.py::test_trade"},
        {"source_owner": "../../secret.py"},
        {"actual_result": "token=secret"},
        {"expected_exit_code": 0},
    ],
)
def test_packet_rejects_unsupported_scope_and_payload_before_client(packet, change):
    path, data, policy = packet
    data.update(change)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        publication.publish_issue(
            policy,
            path,
            expected_stable_head=data["expected_stable_head"],
            client_factory=lambda _: pytest.fail("credentials accessed"),
            dry_run=True,
        )

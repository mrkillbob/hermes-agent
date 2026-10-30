"""Retry producers share the existing caller-repository scan reservation."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import argparse
import json
import os
import subprocess

import pytest


@pytest.fixture
def retry_calls(tmp_path, monkeypatch):
    from github_pr_feedback import cli
    from github_pr_feedback.caller_repository import CallerRepository
    from github_pr_feedback.controller import ScanResult

    def git(path, *args):
        return subprocess.check_output(["git", "-C", str(path), *args], text=True, encoding="utf-8").strip()

    roots = {}
    for name in ("alpha", "beta"):
        root = tmp_path / name
        root.mkdir()
        git(root, "init", "--quiet")
        git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", f"core.hooksPath={os.devnull}", "commit", "--quiet", "--allow-empty", "-m", "fixture")
        git(root, "remote", "add", "origin", f"https://github.com/fixture/{name}.git")
        roots[name] = root
    linked = tmp_path / "linked"
    git(roots["alpha"], "worktree", "add", "--quiet", "--detach", str(linked))
    control_home = tmp_path / "control"
    monkeypatch.setattr(cli, "get_default_hermes_root", lambda: control_home)
    opened, produced, closed = [], [], []
    fail = {"producer": False}

    class Context:
        def __init__(self, path, repo):
            self.path, self.repo = path, repo

        def session_cwd(self, *, require_session=False):
            return str(self.path)

        def get_config(self, key, default=None):
            if key == "enabled":
                return True
            if key == "repositories":
                return [{"base_repository": f"fixture/{self.repo}", "local_path": str(roots[self.repo])}]
            return default

    def close_ledger():
        with cli._exclusive_scan_lock() as acquired:
            assert not acquired
        closed.append(True)

    def open_ledger():
        opened.append(True)
        return SimpleNamespace(close=close_ledger)

    def produce(receipt):
        # The physical producer and the ledger close must still be inside the
        # same nonblocking reservation; another descriptor cannot take it.
        with cli._exclusive_scan_lock() as acquired:
            assert not acquired
        produced.append(receipt)
        if fail["producer"]:
            raise RuntimeError("synthetic producer failure")
        return ScanResult(1, {})

    class Repair:
        def __init__(self, *args, **kwargs):
            pass

        def scan(self, *, retry_receipt):
            return produce(retry_receipt)

    monkeypatch.setattr(cli.FeedbackLedger, "for_current_profile", open_ledger)
    monkeypatch.setattr(cli, "_load_policy_from_context", lambda ctx: object())
    monkeypatch.setattr(cli, "_github_client", lambda policy: None)
    monkeypatch.setattr(cli, "KanbanSubprocessClient", lambda: None)
    monkeypatch.setattr(cli, "RepairController", Repair)
    monkeypatch.setattr(cli, "_controller", lambda policy, ledger: SimpleNamespace(retry_failed=produce))

    def invoke(repo, kind, path=None):
        context = Context(path or roots[repo], repo)
        _, handler = cli.cli_bindings(context)
        return handler(argparse.Namespace(github_pr_feedback_action="retry", repository=f"fixture/{repo}",
                       pr_number=17, feedback_kind=kind, feedback_id="120", head_sha="a" * 40))

    return SimpleNamespace(cli=cli, binding=CallerRepository.resolve(Context(roots["alpha"], "alpha")),
                           linked=linked, invoke=invoke, opened=opened, produced=produced, closed=closed, fail=fail)


@pytest.mark.parametrize("kind", ["review_comment", "pr_repair"])
def test_retry_reserves_exact_caller_before_any_producer_and_preserves_parallel_repositories(retry_calls,
                                                                                          capsys, kind):
    from github_pr_feedback.caller_repository import repository_scope
    from github_pr_feedback.policy import FeedbackReceipt

    c = retry_calls
    with repository_scope(c.binding), c.cli._exclusive_scan_lock() as acquired:
        assert acquired
        # The contender enters through the registered CLI with a linked
        # checkout: same common Git directory, different worktree path.
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(c.invoke, "alpha", kind, c.linked).result(timeout=10) == 1
        assert c.opened == c.produced == c.closed == []
        assert json.loads(capsys.readouterr().out) == {"status": "scan_already_running"}
        assert c.invoke("beta", kind) == 0
        assert c.produced == [FeedbackReceipt("fixture/beta", 17, kind, "120", "a" * 40)]
        capsys.readouterr()
    assert c.invoke("alpha", kind, c.linked) == 0
    assert c.produced[-1] == FeedbackReceipt("fixture/alpha", 17, kind, "120", "a" * 40)
    assert len(c.opened) == len(c.closed) == 2
    assert json.loads(capsys.readouterr().out) == {"created": 1, "skipped": {}, "status": "ok"}


@pytest.mark.parametrize("kind", ["review_comment", "pr_repair"])
@pytest.mark.parametrize("failure_stage", ["factory", "producer"])
def test_retry_failure_closes_ledger_before_releasing_caller_reservation(retry_calls, monkeypatch,
                                                                       kind, failure_stage):
    from github_pr_feedback.caller_repository import repository_scope

    c = retry_calls
    c.fail["producer"] = failure_stage == "producer"
    def failed_factory(*args, **kwargs):
        raise RuntimeError("synthetic producer failure")
    with monkeypatch.context() as patch:
        if failure_stage == "factory":
            patch.setattr(c.cli, "RepairController" if kind == "pr_repair" else "_controller", failed_factory)
        with pytest.raises(RuntimeError, match="synthetic producer failure"):
            c.invoke("alpha", kind)
    assert len(c.opened) == len(c.closed) == 1
    with repository_scope(c.binding), c.cli._exclusive_scan_lock() as acquired:
        assert acquired
    c.fail["producer"] = False
    assert c.invoke("alpha", kind) == 0
    assert len(c.opened) == len(c.closed) == 2

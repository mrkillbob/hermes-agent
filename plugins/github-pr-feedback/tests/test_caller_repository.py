"""Caller binding regressions; local Git fixtures only, no provider or agent calls."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import argparse
import subprocess
import threading

import pytest


def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


@pytest.fixture
def repos(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    git(a, "init", "-b", "same-branch")
    git(a, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-m", "fixture")
    git(a, "remote", "add", "origin", "https://github.com/fixture/alpha.git")
    # B intentionally shares A's object and branch name. SHA != repo identity.
    subprocess.check_call(["git", "clone", "--quiet", str(a), str(b)])
    git(b, "remote", "set-url", "origin", "git@github.com:fixture/beta.git")
    assert git(a, "rev-parse", "HEAD") == git(b, "rev-parse", "HEAD")
    return a, b


class Context:
    def __init__(self, cwd, settings=None):
        self.cwd, self.settings = cwd, settings or {}

    def session_cwd(self, *, require_session=False):
        return str(self.cwd) if self.cwd else ""

    def get_config(self, key, default=None):
        return self.settings.get(key, default)


def target(path, repository):
    return dict(base_repository=repository, head_repository=repository,
                local_path=str(path), owner_login="fixture", branch_prefixes=[])


def test_shared_commit_and_branch_do_not_authorize_other_repository(repos):
    from github_pr_feedback.caller_repository import CallerRepository, RepositoryBindingError
    a, b = repos
    binding = CallerRepository.resolve(Context(a), "fixture/alpha")
    with pytest.raises(RepositoryBindingError):
        binding.verify_workspace(b, "fixture/alpha")
    with pytest.raises(RepositoryBindingError):
        CallerRepository.resolve(Context(a), "fixture/beta")


def test_nested_and_linked_worktree_keep_common_directory(repos, tmp_path):
    from github_pr_feedback.caller_repository import CallerRepository
    a, _ = repos
    nested = a / "nested"
    nested.mkdir()
    worktree = tmp_path / "linked"
    git(a, "worktree", "add", "--detach", str(worktree), "HEAD")
    original = CallerRepository.resolve(Context(nested))
    linked = CallerRepository.resolve(Context(worktree))
    assert original.common_dir == linked.common_dir
    assert original.repository == linked.repository == "fixture/alpha"
    original.verify_workspace(worktree, original.repository)


@pytest.mark.parametrize("cwd", [None, "not-a-checkout"])
def test_missing_identity_has_no_process_or_global_fallback(cwd, tmp_path, monkeypatch):
    from github_pr_feedback.caller_repository import CallerRepository, RepositoryBindingError
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RepositoryBindingError):
        CallerRepository.resolve(Context(cwd))


def test_wrong_configured_path_refuses_before_handler_or_github(repos, monkeypatch):
    from github_pr_feedback import cli
    a, b = repos
    ctx = Context(a, dict(enabled=True, repositories=[target(b, "fixture/alpha")]))
    reached = []
    monkeypatch.setattr(cli, "handle_cli_with_context", lambda *_: reached.append(True))
    _, handler = cli.cli_bindings(ctx)
    assert handler(argparse.Namespace(github_pr_feedback_action="scan", repository=None)) == 1
    assert reached == []


def test_explicit_other_repository_refuses_before_any_handler(repos, monkeypatch):
    from github_pr_feedback import cli
    a, b = repos
    ctx = Context(a, dict(enabled=True, repositories=[target(a, "fixture/alpha"), target(b, "fixture/beta")]))
    def forbidden(*_args, **_kwargs):
        pytest.fail("wrong-repo call reached an effectful handler")
    monkeypatch.setattr(cli, "handle_cli_with_context", forbidden)
    _, handler = cli.cli_bindings(ctx)
    assert handler(argparse.Namespace(github_pr_feedback_action="scan", repository="fixture/beta")) == 1


def test_merge_lane_is_filtered_before_even_constructing_client(monkeypatch):
    from github_pr_feedback import cli
    def forbidden(*_args, **_kwargs):
        pytest.fail("other repo merge lane reached client construction")
    monkeypatch.setattr(cli, "_github_client", forbidden)
    result = cli._run_merge_scan_for_policy(
        SimpleNamespace(targets={"fixture/alpha": object()}),
        SimpleNamespace(repository="fixture/beta"), object())
    assert result["processed"] == 0


def test_concurrent_registered_calls_keep_policy_context_and_parent_cwd(repos, monkeypatch):
    from github_pr_feedback import cli
    from github_pr_feedback.caller_repository import current_repository
    a, b = repos
    settings = dict(enabled=True, repositories=[target(a, "fixture/alpha"), target(b, "fixture/beta")])
    barrier = threading.Barrier(2)
    original_cwd = Path.cwd()
    def inspect(ctx, args):
        barrier.wait(timeout=5)
        binding = current_repository()
        selected = ctx.get_config("repositories")
        assert len(selected) == 1
        assert selected[0]["base_repository"] == binding.repository
        binding.verify_workspace(selected[0]["local_path"], binding.repository)
        return binding.repository
    monkeypatch.setattr(cli, "handle_cli_with_context", inspect)
    handlers = [cli.cli_bindings(Context(root, settings))[1] for root in (a, b)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fn, argparse.Namespace(github_pr_feedback_action="scan", repository=None)) for fn in handlers]
        assert {f.result(timeout=10) for f in futures} == {"fixture/alpha", "fixture/beta"}
    assert current_repository() is None
    assert Path.cwd() == original_cwd


def test_bound_task_dispatch_refuses_wrong_repo_even_with_same_head(repos):
    from github_pr_feedback.caller_repository import CallerRepository, repository_scope, RepositoryBindingError
    from github_pr_feedback.cli import KanbanSubprocessClient
    a, b = repos
    class NeverRun:
        def run(self, _argv):
            pytest.fail("wrong-workspace task reached subprocess")
    with repository_scope(CallerRepository.resolve(Context(a))):
        client = KanbanSubprocessClient(NeverRun())
    with pytest.raises(RepositoryBindingError):
        client.create_or_get_task(SimpleNamespace(repository_path=b, evidence={"repository": "fixture/alpha"}))


def test_worktree_materializer_rejects_before_git_or_fetch(repos):
    from github_pr_feedback.caller_repository import CallerRepository, repository_scope, RepositoryBindingError
    from github_pr_feedback.controller import LocalGitRepository
    a, b = repos
    class NeverRun:
        def run(self, _argv):
            pytest.fail("wrong source reached Git materialization")
    with repository_scope(CallerRepository.resolve(Context(a))):
        local = LocalGitRepository(runner=NeverRun())
    with pytest.raises(RepositoryBindingError):
        local._ensure_exact_head(b, SimpleNamespace(repository="fixture/alpha", head_sha=git(a, "rev-parse", "HEAD")))


def test_git_environment_cannot_redirect_identity_probe(repos, monkeypatch):
    from github_pr_feedback.caller_repository import CallerRepository, RepositoryBindingError
    a, b = repos
    monkeypatch.setenv("GIT_DIR", str(b / ".git"))
    with pytest.raises(RepositoryBindingError):
        CallerRepository.resolve(Context(a))


def test_other_repository_push_url_is_refused(repos):
    from github_pr_feedback.caller_repository import CallerRepository, RepositoryBindingError
    a, _ = repos
    git(a, "remote", "set-url", "--push", "origin", "https://github.com/fixture/beta.git")
    with pytest.raises(RepositoryBindingError):
        CallerRepository.resolve(Context(a))


def test_public_materialization_refuses_before_mutable_lease(repos):
    from github_pr_feedback.caller_repository import CallerRepository, repository_scope, RepositoryBindingError
    from github_pr_feedback.controller import PooledLocalGitRepository
    a, b = repos
    class NeverClaim:
        def claim_worktree_slot(self, *_args, **_kwargs):
            pytest.fail("wrong repo reached mutable lease claim")
    with repository_scope(CallerRepository.resolve(Context(a))):
        pool = PooledLocalGitRepository(NeverClaim())
    with pytest.raises(RepositoryBindingError):
        pool.prepare_receipt_worktree(b, SimpleNamespace(repository="fixture/alpha", head_sha=git(a, "rev-parse", "HEAD")))


def settings_for(a, b):
    return dict(enabled=True, repositories=[target(a, "fixture/alpha"), target(b, "fixture/beta")],
                reviewer_logins=["fixture-reviewer"], reviewer_associations=[],
                not_before="2026-08-24T00:00:00Z", assignee="fixture-worker", board="fixture-board")


def test_scoped_policy_parses_with_only_other_repository_maintenance_lanes(repos, monkeypatch):
    from github_pr_feedback import cli
    a, b = repos
    settings = settings_for(a, b)
    # These lanes are deliberately incomplete: no parser/client should consume B's policy.
    settings.update(merge_maintainers=[dict(repository="fixture/beta", enabled=True)],
                    release_maintenances=[dict(repository="fixture/beta", enabled=True)],
                    repair_steward=dict(repositories=["fixture/beta"], enabled=True))
    policies = []
    def inspect(ctx, _args):
        policy = cli._load_policy_from_context(ctx)
        policies.append(policy)
        return 0
    monkeypatch.setattr(cli, "handle_cli_with_context", inspect)
    assert cli.cli_bindings(Context(a, settings))[1](argparse.Namespace(repository=None)) == 0
    policy = policies[0]
    assert set(policy.targets) == {"fixture/alpha"}
    assert policy.merge_policies() == policy.release_policies() == ()
    assert policy.repair_steward is None


def test_real_scan_routing_is_concurrent_and_single_repository(repos, monkeypatch, tmp_path):
    from github_pr_feedback import cli
    from github_pr_feedback.caller_repository import current_repository
    a, b = repos
    # Native capacity admission rejects symlink ancestors; macOS /var is one.
    physical_home = tmp_path.resolve() / "control-home"
    physical_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path.resolve()))
    monkeypatch.setenv("HERMES_HOME", str(physical_home))
    barrier = threading.Barrier(2)
    requests = []
    class OfflineGitHub:
        def __init__(self, repository):
            self.repository = repository
        def list_open_pull_requests(self, repository, _owner):
            assert repository == self.repository
            requests.append(repository)
            barrier.wait(timeout=5)
            return ()
    monkeypatch.setattr(cli, "_github_client", lambda _policy: OfflineGitHub(current_repository().repository))
    handlers = [cli.cli_bindings(Context(root, settings_for(a, b)))[1] for root in (a, b)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fn, argparse.Namespace(github_pr_feedback_action="scan", repository=None)) for fn in handlers]
        assert [f.result(timeout=15) for f in futures] == [0, 0]
    assert sorted(requests) == ["fixture/alpha", "fixture/beta"]


def test_standalone_cli_boundary_supplies_cwd_without_gateway_fallback(repos, monkeypatch):
    from hermes_cli import plugin_session
    from hermes_cli.main import _attach_plugin_cli_command
    from github_pr_feedback import cli
    a, b = repos
    manager = SimpleNamespace(_cli_ref=None)
    class HostContext(Context):
        def session_cwd(self, *, require_session=False):
            return plugin_session.context_cwd(manager, require_session=require_session)
    ctx = HostContext(None, settings_for(a, b))
    # Outside the actual CLI dispatch boundary, missing managed identity refuses.
    monkeypatch.setattr(plugin_session, "session_cwd", lambda: str(b))
    monkeypatch.setattr(plugin_session, "_active_policy", lambda: None)
    monkeypatch.setattr("agent.runtime_cwd.scoped_session_cwd", lambda: "")
    monkeypatch.setattr(cli, "handle_cli_with_context", lambda c, _args: c.binding.repository)
    setup, handler = cli.cli_bindings(ctx)
    assert handler(argparse.Namespace(repository=None)) == 1
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command")
    _attach_plugin_cli_command(commands, dict(name="feedback-fixture", help="fixture", setup_fn=setup, handler_fn=handler))
    monkeypatch.chdir(a)
    args = parser.parse_args(["feedback-fixture", "scan"])
    assert args.func(args) == "fixture/alpha"
    assert plugin_session.context_cwd(manager, require_session=True) == ""


def test_managed_caller_is_session_workspace_even_when_process_is_elsewhere(repos, monkeypatch):
    from hermes_cli import plugin_session
    from github_pr_feedback.caller_repository import CallerRepository
    a, b = repos
    monkeypatch.chdir(b)
    monkeypatch.setattr(plugin_session, "_active_policy", lambda: SimpleNamespace(cwd=str(a)))
    monkeypatch.setattr("agent.runtime_cwd.scoped_session_cwd", lambda: "")
    manager = SimpleNamespace(_cli_ref=None)
    ctx = SimpleNamespace(session_cwd=lambda **kwargs: plugin_session.context_cwd(manager, **kwargs))
    assert CallerRepository.resolve(ctx).root == a


def test_administrative_batch_is_not_granted_by_command_name(repos, monkeypatch):
    from github_pr_feedback import cli
    a, b = repos
    def forbidden(*_args):
        pytest.fail("ungranted admin call reached batch handler")
    monkeypatch.setattr(cli, "handle_cli_with_context", forbidden)
    handler = cli.cli_bindings(Context(a, settings_for(a, b)))[1]
    assert handler(argparse.Namespace(github_pr_feedback_action="scan-admin", all_configured=True)) == 1


def test_selected_deployment_cannot_relocate_to_another_clone(repos, tmp_path):
    from github_pr_feedback.caller_repository import CallerRepository, ScopedContext, RepositoryBindingError
    a, b = repos
    clone = tmp_path / "same-repo-other-clone"
    subprocess.check_call(["git", "clone", "--quiet", str(a), str(clone)])
    git(clone, "remote", "set-url", "origin", "https://github.com/fixture/alpha.git")
    settings = settings_for(a, b)
    settings["merge_maintainer"] = dict(repository="fixture/alpha", enabled=True,
                                      post_merge=dict(enabled=True, deployment_path=str(clone)))
    scoped = ScopedContext(Context(a, settings), CallerRepository.resolve(Context(a)))
    with pytest.raises(RepositoryBindingError):
        scoped.get_config("merge_maintainer")


def _resolve_in_child(root):
    from github_pr_feedback.caller_repository import CallerRepository, current_repository, repository_scope
    assert current_repository() is None
    binding = CallerRepository.resolve(Context(root))
    with repository_scope(binding):
        value = current_repository().repository
    assert current_repository() is None
    return value


def test_separate_processes_have_independent_repository_context(repos):
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context
    with ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn")) as pool:
        assert set(pool.map(_resolve_in_child, repos)) == {"fixture/alpha", "fixture/beta"}


def test_worker_cannot_opt_into_granted_administrative_mode(repos, monkeypatch):
    from github_pr_feedback import cli
    a, b = repos
    settings = {**settings_for(a, b), "administrative_batch": True}
    monkeypatch.setenv("HERMES_KANBAN_TASK", "")  # presence, even empty, is protected
    monkeypatch.setattr(cli, "handle_cli_with_context", lambda *_: pytest.fail("worker escaped caller mode"))
    assert cli.cli_bindings(Context(a, settings))[1](argparse.Namespace(github_pr_feedback_action="scan-admin", all_configured=True)) == 1


def test_bound_scan_never_reconciles_legacy_source_only_leases(repos):
    from github_pr_feedback.caller_repository import CallerRepository, repository_scope
    from github_pr_feedback.controller import PooledLocalGitRepository
    class SharedSourceLedger:
        def leased_worktree_slots(self):
            pytest.fail("source-only legacy lease is not positive repository ownership")
    with repository_scope(CallerRepository.resolve(Context(repos[0]))):
        pool = PooledLocalGitRepository(SharedSourceLedger())
    assert pool.reconcile_leases(SimpleNamespace(task_status=lambda *_: "done")) == 0


def test_maintenance_receipt_workspace_must_belong_to_caller(repos, tmp_path):
    from github_pr_feedback.caller_repository import CallerRepository, repository_scope, RepositoryBindingError
    from github_pr_feedback.cli import _validate_maintenance_command_evidence
    from github_pr_feedback.release_maintenance import maintenance_worktree_path
    a, b = repos
    root = tmp_path / "maintenance"
    head = git(a, "rev-parse", "HEAD")
    workspace = maintenance_worktree_path(root, "fixture/alpha", head, "audit-fast")
    workspace.parent.mkdir(parents=True)
    # Deterministic path and identical HEAD still do not prove repo/common-dir.
    subprocess.check_call(["git", "clone", "--quiet", str(b), str(workspace)])
    git(workspace, "remote", "set-url", "origin", "https://github.com/fixture/beta.git")
    maintenance = SimpleNamespace(repository="fixture/alpha", lanes=(SimpleNamespace(name="fast", command=("true",)),))
    evidence = (SimpleNamespace(argv=("true",), cwd=str(workspace)),)
    with repository_scope(CallerRepository.resolve(Context(a))):
        with pytest.raises(RepositoryBindingError):
            _validate_maintenance_command_evidence(maintenance, "fast", evidence,
                                                   worktree_root=root, head_sha=head)


def test_selected_policy_and_explicit_selector_use_one_canonical_case(repos, monkeypatch):
    from github_pr_feedback import cli
    a, b = repos
    settings = settings_for(a, b)
    settings["repositories"][0].update(base_repository="Fixture/Alpha", head_repository="Fixture/Alpha")
    def inspect(ctx, args):
        policy = cli._load_policy_from_context(ctx)
        assert set(policy.targets) == {"fixture/alpha"}
        assert args.repository == "fixture/alpha"
        return 0
    monkeypatch.setattr(cli, "handle_cli_with_context", inspect)
    args = argparse.Namespace(repository="Fixture/Alpha")
    assert cli.cli_bindings(Context(a, settings))[1](args) == 0
    assert args.repository == "Fixture/Alpha"  # caller arguments are not mutated

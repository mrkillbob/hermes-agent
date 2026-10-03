from github_pr_feedback.supplementary_ci import SupplementaryPlan


def test_reviewed_plan_requires_explicit_supplementary_set():
    plan = SupplementaryPlan.parse("acme/widgets", {
        "pr_number": 17, "base_sha": "b" * 40, "head_sha": "a" * 40,
        "changed_files": [{"path": "web/src/app.ts", "status": "modified"}],
        "commands": [{"id": "web-build", "argv": ["npm", "run", "build"], "cwd": "web"}],
    }, (("Checks", ("Run checks",)),))
    assert plan.commands[0].argv == ("npm", "run", "build")


import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import pytest
from github_pr_feedback.ci_runner import (CIAuditReceipt, CIAuditIdentity, CommandEvidence,
                                         CompletedCommand, LocalCIRunner, _receipt_id)
from github_pr_feedback.github_client import CheckState, ExactChangeScope
from github_pr_feedback.ledger import FeedbackLedger
from github_pr_feedback.supplementary_ci import supplementary_blockers

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
JOBS = (("Checks", ("Run checks",)),)


def reviewed_plan(commands=None):
    return SupplementaryPlan.parse("acme/widgets", {
        "pr_number": 17, "base_sha": "b" * 40, "head_sha": "a" * 40,
        "changed_files": [{"path": "web/src/app.ts", "status": "modified"}],
        "commands": [{"id": "web-build", "argv": ["npm", "run", "build"], "cwd": "web"}] if commands is None else commands,
    }, JOBS)


def targeted_receipt(plan, *, completed=NOW, status="passed", commands=None, identity=None, digest=None):
    identity = identity or CIAuditIdentity(plan.repository, plan.pr_number, plan.base_sha, plan.head_sha)
    commands = tuple(CommandEvidence(c.argv, c.cwd, 0, 1, False, "d" * 64, "e" * 64, "passed") for c in plan.commands) if commands is None else commands
    digest = digest or plan.digest
    return CIAuditReceipt(_receipt_id(identity, digest, status, completed, commands, ci_mode="standard"),
                          identity, digest, status, completed - timedelta(seconds=1), completed,
                          CheckState(True, True, 1), commands)


@pytest.mark.parametrize("case", ["valid", "missing", "stale", "future", "failed", "wrong-head", "wrong-digest", "wrong-command", "timeout", "empty-known"])
def test_targeted_evidence_only_satisfies_its_exact_reviewed_contract(case):
    plan = reviewed_plan([] if case == "empty-known" else None)
    receipt = targeted_receipt(plan) if case != "empty-known" else None
    if case == "missing": receipt = None
    elif case == "stale": receipt = targeted_receipt(plan, completed=NOW-timedelta(hours=1))
    elif case == "future": receipt = targeted_receipt(plan, completed=NOW+timedelta(seconds=1))
    elif case == "failed": receipt = targeted_receipt(plan, status="failed")
    elif case == "wrong-head": receipt = targeted_receipt(plan, identity=CIAuditIdentity(plan.repository, 17, plan.base_sha, "f"*40))
    elif case == "wrong-digest": receipt = targeted_receipt(plan, digest="f"*64)
    elif case == "wrong-command": receipt = targeted_receipt(plan, commands=(replace(receipt.commands[0], argv=("npm", "run", "test")),))
    elif case == "timeout": receipt = targeted_receipt(plan, commands=(replace(receipt.commands[0], timed_out=True),))
    assert bool(supplementary_blockers(plan, receipt, now=NOW, max_age_seconds=60)) == (case not in {"valid", "empty-known"})


@pytest.mark.parametrize("case", ["pass", "failure", "dirty", "changed-scope"])
def test_supplementary_runner_records_only_targeted_commands_and_real_failures(tmp_path, case):
    plan = reviewed_plan()
    (tmp_path / "web").mkdir()
    ledger = FeedbackLedger(tmp_path / "ledger.sqlite3")
    scope = ExactChangeScope(plan.repository, 17, plan.base_sha, plan.head_sha, plan.changed_files)
    state = SimpleNamespace(repository=plan.repository, number=17, base_sha=plan.base_sha, head_sha=plan.head_sha, head_repository=plan.repository, state="OPEN", merged=False)
    calls = []
    class Commands:
        def run(self, argv, *, cwd, env, timeout):
            calls.append((argv, cwd))
            return CompletedCommand(1 if case == "failure" else 0, "actual output", "", 1, False)
    class Inspector:
        def head_sha(self, worktree): return plan.head_sha
        def is_clean(self, worktree): return case != "dirty"
    github = SimpleNamespace(get_merge_state=lambda *a: state,
                             get_check_state=lambda *a: CheckState(True, True, 1),
                             get_exact_change_scope=lambda *a: replace(scope, head_sha="f"*40) if case == "changed-scope" else scope)
    receipt = LocalCIRunner(github, ledger, command_runner=Commands(), inspector=Inspector(), now=lambda: NOW).run_supplementary(plan, tmp_path)
    assert receipt is not None and receipt.status == ("passed" if case == "pass" else "failed")
    assert calls == ([(('npm','run','build'), tmp_path/'web')] if case in {"pass", "failure"} else [])
    assert ledger.latest_ci_receipt(plan.repository,17,plan.head_sha,manifest_digest=plan.digest,not_before=NOW-timedelta(hours=1)).receipt_id == receipt.receipt_id
    ledger.close()


@pytest.mark.parametrize("argv", [["bash", "scripts/run_tests.sh"], ["python", "scripts/run_local_ci_audit.py"]])
def test_supplementary_plan_rejects_duplicate_full_replay(argv):
    with pytest.raises(ValueError, match="focused|full local audit"):
        reviewed_plan([{"id":"wrong", "argv":argv, "cwd":"."}])


@pytest.mark.parametrize("outcome", ["ran", "all-skipped", "some-skipped", "no-summary", "wrong-platform", "all-skipped-alias", "some-skipped-alias"])
def test_targeted_native_tests_cannot_turn_skips_into_coverage(tmp_path, outcome):
    import sys
    (tmp_path/"scripts").mkdir()
    (tmp_path/"scripts/run_tests.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (tmp_path/"tests").mkdir()
    (tmp_path/"tests/test_native.py").write_text("def test_native(): pass\n", encoding="utf-8")
    plan = reviewed_plan([{"id":"native","argv":["bash","./scripts/run_tests.sh" if outcome.endswith("-alias") else "scripts/run_tests.sh","tests/test_native.py"],"cwd":"."}])
    plan = replace(plan, runner_platform="win32" if sys.platform != "win32" else "darwin") if outcome=="wrong-platform" else plan
    state=SimpleNamespace(repository=plan.repository,number=17,base_sha=plan.base_sha,head_sha=plan.head_sha,head_repository=plan.repository,state="OPEN",merged=False)
    scope=ExactChangeScope(plan.repository,17,plan.base_sha,plan.head_sha,plan.changed_files)
    github=SimpleNamespace(get_merge_state=lambda *a:state,get_check_state=lambda *a:CheckState(True,True,1),get_exact_change_scope=lambda *a:scope)
    outputs={"ran":"Summary: 1 files, 2 tests passed, 0 failed", "all-skipped":"Summary: 1 files, 0 tests passed, 0 failed, 2 skipped", "some-skipped":"Summary: 1 files, 1 tests passed, 0 failed, 1 skipped", "no-summary":"No workload evidence"}
    calls=[]
    class Commands:
        def run(self,argv,**kwargs):
            calls.append(argv);return CompletedCommand(0,outputs.get(outcome.removesuffix("-alias"),""),"",1,False)
    inspector=SimpleNamespace(head_sha=lambda p:plan.head_sha,is_clean=lambda p:True)
    ledger=FeedbackLedger(tmp_path/"ledger.sqlite3")
    receipt=LocalCIRunner(github,ledger,command_runner=Commands(),inspector=inspector,now=lambda:NOW).run_supplementary(plan,tmp_path)
    assert receipt.status == ("passed" if outcome=="ran" else "failed")
    if outcome=="wrong-platform":assert calls==[]
    ledger.close()


@pytest.mark.parametrize("argv", [["bash","./scripts/run_tests.sh"], ["bash","scripts/run_tests.sh","-k","tests/test_native.py"], ["bash","other/run_tests.sh","tests/test_native.py"]])
def test_runner_aliases_cannot_omit_or_hide_effective_focused_targets(argv):
    with pytest.raises(ValueError, match="focused|canonical|alias"):
        reviewed_plan([{"id":"native","argv":argv,"cwd":"."}])


def test_windows_relative_cwd_producer_roundtrips_to_canonical_plan(tmp_path):
    from pathlib import PureWindowsPath
    from github_pr_feedback.ci_runner import _command_evidence
    plan=reviewed_plan([{"id":"desktop","argv":["npm","run","build"],"cwd":"apps/desktop"}])
    actual=_command_evidence(plan.commands[0].argv,PureWindowsPath("C:/source/apps/desktop"),
                             PureWindowsPath("C:/source"),CompletedCommand(0,"built","",1,False))
    receipt=targeted_receipt(plan,commands=(actual,))
    ledger=FeedbackLedger(tmp_path/"ledger.sqlite3");ledger.record_ci_receipt(receipt)
    readback=ledger.latest_ci_receipt(plan.repository,plan.pr_number,plan.head_sha,manifest_digest=plan.digest,not_before=NOW-timedelta(seconds=1))
    assert supplementary_blockers(plan,readback,now=NOW,max_age_seconds=60)==()
    ledger.close()


@pytest.mark.parametrize("argv,cwd", [
    (["bash",".\\scripts\\run_tests.sh",".\\tests\\test_native.py"],"."),
    (["bash","./SCRIPTS/RUN_TESTS.SH","./tests/test_native.py"],"."),
    (["python3","./scripts/run_tests_parallel.py","tests/test_native.py"],"."),
])
def test_supported_runner_spellings_have_one_focused_command_identity(argv,cwd):
    from github_pr_feedback.supplementary_ci import canonical_command
    normalized,relative,focused=canonical_command(argv,cwd)
    assert focused and relative=="."
    assert normalized[-1]=="tests/test_native.py"
    assert normalized[1] in {"scripts/run_tests.sh","scripts/run_tests_parallel.py"}


@pytest.mark.parametrize("cwd", ["../apps/desktop", "apps/../desktop", "C:\\apps\\desktop", "\\\\host\\share", "/apps/desktop", "apps\\..\\desktop"])
def test_relative_cwd_normalization_never_erases_escape_or_traversal(cwd):
    from github_pr_feedback.supplementary_ci import canonical_cwd
    with pytest.raises(ValueError):canonical_cwd(cwd)


@pytest.fixture
def project_native_platform(request):
    # This standalone plugin's conftest only handles its historical markers.
    # Reuse the project's current marker resolver, without faking the host.
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[3]/"tests/_fixtures/platform_gating.py"
    spec=importlib.util.spec_from_file_location("supplementary_platform_gate",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    reason=module._platforms_gate_reason(request.node)
    if reason:pytest.skip(reason)


@pytest.mark.platforms("windows")
def test_native_windows_nested_cwd_real_producer_ledger_and_evaluator(tmp_path, project_native_platform):
    import sys, subprocess
    from github_pr_feedback.ci_runner import GitRepositoryInspector
    from github_pr_feedback.github_client import RequiredCIGate
    from github_pr_feedback.merge_controller import evaluate_merge
    from test_merge_controller import policy, eligible_snapshot, pr_state
    assert sys.platform=="win32"
    source=tmp_path/"source";source.mkdir()
    subprocess.run(["git","init","--quiet",str(source)],check=True)
    target=source/"apps/desktop/value.txt";target.parent.mkdir(parents=True);target.write_text("before", encoding="utf-8")
    def commit():
        subprocess.run(["git","-C",str(source),"add","."],check=True)
        subprocess.run(["git","-C",str(source),"-c","user.name=CI","-c","user.email=ci@example.invalid","commit","-qm","fixture"],check=True)
        return subprocess.check_output(["git","-C",str(source),"rev-parse","HEAD"],text=True, encoding="utf-8", errors="replace").strip()
    base=commit();target.write_text("after", encoding="utf-8");head=commit()
    plan=SupplementaryPlan.parse("acme/widgets",{"pr_number":17,"base_sha":base,"head_sha":head,
        "changed_files":[{"path":"apps/desktop/value.txt","status":"modified"}],
        "commands":[{"id":"nested","argv":[sys.executable,"-c","print('actual native command')"],"cwd":"apps/desktop"}],
        "runner_platform":"win32"},JOBS)
    scope=ExactChangeScope(plan.repository,17,base,head,plan.changed_files)
    state=pr_state(base_sha=base,head_sha=head)
    github=SimpleNamespace(get_merge_state=lambda *a:state,get_check_state=lambda *a:CheckState(True,True,1),get_exact_change_scope=lambda *a:scope)
    ledger=FeedbackLedger(tmp_path/"ledger.sqlite3")
    produced=LocalCIRunner(github,ledger,inspector=GitRepositoryInspector(),now=lambda:NOW).run_supplementary(plan,source)
    assert produced.status=="passed" and produced.commands[0].cwd=="apps/desktop"
    readback=ledger.latest_ci_receipt(plan.repository,17,head,manifest_digest=plan.digest,not_before=NOW-timedelta(seconds=1))
    selected=replace(policy(),required_workflow_path=".github/workflows/ci.yaml",required_workflow_jobs=JOBS,supplementary_plan=plan)
    gate=RequiredCIGate(71,17,base,head,NOW,".github/workflows/ci.yaml", (("Checks",82,("Run checks",)),))
    snapshot=eligible_snapshot(pull_request=state,base_head_sha=base,ci_receipt=None,hosted_ci_gate=gate,
        change_scope=scope,coverage_plan_digest=plan.digest,supplementary_receipt=readback)
    assert evaluate_merge(selected,snapshot,now=NOW).eligible
    ledger.close()


@pytest.mark.parametrize("bad", ["target-directory", "missing-target", "escaping-target", "runner-alias"])
def test_canonical_runtime_resolution_rejects_unknown_or_escaping_file_identity(tmp_path,bad):
    from github_pr_feedback.supplementary_ci import canonical_command
    source=tmp_path/"source";(source/"scripts").mkdir(parents=True);(source/"tests").mkdir()
    runner=source/"scripts/run_tests.sh";runner.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    target=source/"tests/test_native.py"
    if bad=="target-directory":target.mkdir()
    elif bad=="escaping-target":
        outside=tmp_path/"outside.py";outside.write_text("pass\n", encoding="utf-8");target.symlink_to(outside)
    elif bad=="runner-alias":
        target.write_text("pass\n", encoding="utf-8");(source/"scripts/alias.sh").symlink_to(runner)
    argv=("bash","scripts/alias.sh" if bad=="runner-alias" else "./scripts/run_tests.sh","tests/test_native.py")
    with pytest.raises(ValueError,match="canonical|focused|alias"):
        canonical_command(argv,".",source=source)

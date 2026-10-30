"""Scheduled callers select a workspace explicitly; no live Hermes/GitHub calls."""
from pathlib import Path
import importlib.util
import json
import subprocess
import sys

import pytest


@pytest.fixture
def wrapper():
    path = Path(__file__).resolve().parents[1] / "scripts" / "github-pr-feedback-scan.py"
    spec = importlib.util.spec_from_file_location("cron_workspace_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repositories(tmp_path):
    roots = []
    for name in ("alpha", "beta"):
        root = tmp_path / name
        root.mkdir()
        subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin",
                        f"https://github.com/fixture/{name}.git"], check=True)
        roots.append(root.resolve())
    return roots


_BOUND_CHILD = '''import argparse,json,os,sys
sys.path.insert(0,sys.argv[1])
from github_pr_feedback.caller_repository import bind_cli,current_repository
settings=json.loads(sys.argv[2])
class Context:
    def session_cwd(self,**kwargs): return os.getcwd()
    def get_config(self,key,default=None): return settings.get(key,default)
def handler(ctx,args):
    print(json.dumps({"status":"ok","repository":current_repository().repository,
                      "workspace":str(ctx.binding.root)}))
    return 0
raise SystemExit(bind_cli(Context(),handler)(argparse.Namespace(repository=sys.argv[3])))
'''


@pytest.mark.parametrize("workspace_index,expected_repository", [(0, "alpha"), (1, "beta"), (0, "beta")])
def test_outside_cron_cwd_enters_only_explicit_repository(
    wrapper, repositories, tmp_path, monkeypatch, capsys, workspace_index, expected_repository
):
    outside = tmp_path / "scheduler-home"
    outside.mkdir()
    monkeypatch.chdir(outside)
    workspace = repositories[workspace_index]
    expected = f"fixture/{expected_repository}"
    settings = {"repositories": [dict(base_repository=f"fixture/{name}", head_repository=f"fixture/{name}",
                                     local_path=str(root))
                                  for name, root in zip(("alpha", "beta"), repositories)]}
    monkeypatch.setenv("HERMES_EXECUTABLE", sys.executable)
    monkeypatch.setenv("HERMES_PR_FEEDBACK_REPOSITORY", expected)
    monkeypatch.setattr(sys, "argv", ["cron-wrapper", "--workspace", str(workspace)])
    original_run = subprocess.run
    plugin_root = str(Path(__file__).resolve().parents[1])
    def child(argv, **kwargs):
        assert argv == [sys.executable, "github-pr-feedback", "scan", "--repository", expected]
        return original_run([sys.executable, "-c", _BOUND_CHILD, plugin_root,
                             json.dumps(settings), expected], timeout=15, **kwargs)
    monkeypatch.setattr(wrapper.subprocess, "run", child)
    result = wrapper.main()
    payload = json.loads(capsys.readouterr().out)
    if workspace_index == 0 and expected_repository == "beta":
        assert result == 1
        assert payload["status"] == "rejected"
    else:
        assert result == 0
        assert payload == {"status": "ok", "repository": expected, "workspace": str(workspace)}
    assert Path.cwd() == outside


@pytest.mark.parametrize("kind", ["relative", "missing", "file"])
def test_invalid_workspace_never_invokes_child(wrapper, tmp_path, monkeypatch, kind):
    workspace = {"relative": Path("relative"), "missing": tmp_path / "missing", "file": tmp_path / "file"}[kind]
    if kind == "file":
        workspace.write_text("not a checkout", encoding="utf-8")
    monkeypatch.setenv("HERMES_EXECUTABLE", sys.executable)
    monkeypatch.setattr(sys, "argv", ["cron-wrapper", "--workspace", str(workspace)])
    monkeypatch.setattr(wrapper.subprocess, "run", lambda *_a, **_k: pytest.fail("invalid workspace spawned child"))
    assert wrapper.main() == 127


@pytest.mark.parametrize("extra", [[], ["--workspace", ".", "--all-configured"]])
def test_missing_workspace_or_admin_arguments_do_not_select_global_scan(wrapper, monkeypatch, extra):
    monkeypatch.setenv("HERMES_EXECUTABLE", sys.executable)
    monkeypatch.setattr(sys, "argv", ["cron-wrapper", *extra])
    monkeypatch.setattr(wrapper.subprocess, "run", lambda *_a, **_k: pytest.fail("unscoped/admin cron spawned child"))
    with pytest.raises(SystemExit) as rejected:
        wrapper.main()
    assert rejected.value.code == 2

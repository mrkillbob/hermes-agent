"""Native CLI boolean worktree flags must not become feedback workspace paths."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from github_pr_feedback.caller_repository import bind_cli
from github_pr_feedback.intake_cli import setup_cli
from hermes_cli._parser import build_top_level_parser
from hermes_cli.plugin_session import attach_cli_handler, context_cwd


@pytest.fixture
def caller_cli(tmp_path, monkeypatch):
    def repository(name):
        root = tmp_path / name
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "remote",
                "add",
                "origin",
                "https://github.com/mrkillbob/hermes-agent.git",
            ],
            check=True,
        )
        return root

    root, foreign = repository("caller"), repository("foreign")
    monkeypatch.chdir(root)
    manager = SimpleNamespace(_cli_ref=None)
    settings = [{"base_repository": "mrkillbob/hermes-agent", "local_path": str(root)}]
    ctx = SimpleNamespace(
        session_cwd=lambda **kwargs: context_cwd(manager, **kwargs),
        get_config=lambda key, default=None: (
            settings if key == "repositories" else default
        ),
    )
    parser, subparsers, _ = build_top_level_parser()
    plugin = subparsers.add_parser("github-pr-feedback")
    setup_cli(ctx, plugin)
    reached = []

    def handler(scoped, args):
        reached.append((scoped.binding.root, args.repository, args.worktree))
        return 0

    attach_cli_handler(plugin, bind_cli(ctx, handler))
    args = parser.parse_args(
        ["github-pr-feedback", "scan-intake", "--repository", "mrkillbob/hermes-agent"]
    )
    return args, reached, root, foreign, settings


def test_native_default_worktree_flag_reaches_bound_handler(caller_cli, capsys):
    args, reached, root, _, _ = caller_cli
    assert args.worktree is False
    assert args.func(args) == 0
    assert reached == [(root, "mrkillbob/hermes-agent", False)]
    assert args.worktree is False
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "field,kind,accepted",
    [
        ("worktree", "true", False),
        ("worktree", "integer_zero", False),
        ("worktree", "string", True),
        ("worktree", "path", True),
        ("worktree", "foreign", False),
        ("worktree", "relative", False),
        ("repository_path", "false", False),
        ("repository_path", "string", True),
        ("repository_path", "path", True),
        ("repository_path", "foreign", False),
        ("repository_path", "relative", False),
        ("worktree", "wrong_remote", False),
        ("worktree", "ambient_override", False),
    ],
)
def test_supplied_workspace_guards_survive_native_namespace(
    caller_cli, monkeypatch, capsys, field, kind, accepted
):
    args, reached, root, foreign, settings = caller_cli
    values = {
        "true": True,
        "false": False,
        "integer_zero": 0,
        "string": str(root),
        "path": Path(root),
        "foreign": str(foreign),
        "relative": "relative",
        "wrong_remote": str(root),
        "ambient_override": str(root),
    }
    setattr(args, field, values[kind])
    if kind == "wrong_remote":
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "remote",
                "set-url",
                "origin",
                "https://github.com/example/other.git",
            ],
            check=True,
        )
    if kind == "ambient_override":
        monkeypatch.setenv("GIT_DIR", str(root / ".git"))
    before = vars(args).copy()
    assert args.func(args) == (0 if accepted else 1)
    assert bool(reached) is accepted
    assert vars(args) == before
    output = capsys.readouterr().out
    if accepted:
        assert reached == [(root, "mrkillbob/hermes-agent", args.worktree)]
        assert output == ""
    else:
        assert json.loads(output) == {
            "status": "rejected",
            "reason": "caller_repository_binding_failed",
        }
    assert settings[0]["local_path"] == str(root)

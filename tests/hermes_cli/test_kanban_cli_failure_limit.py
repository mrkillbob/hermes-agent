"""Manual dispatch must inherit the configured breaker unless a flag overrides it."""
import argparse
import contextlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def configured_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    (home / "config.yaml").write_text("kanban:\n  failure_limit: 7\n")
    return home


def parse(command, explicit=None):
    from hermes_cli.kanban_parser import build_parser

    parser = argparse.ArgumentParser()
    build_parser(parser.add_subparsers())
    argv = ["kanban", command]
    argv += ["--dry-run", "--json"] if command == "dispatch" else ["--force"]
    if explicit is not None:
        argv += ["--failure-limit", str(explicit)]
    return parser.parse_args(argv)


@pytest.mark.parametrize("command", ["dispatch", "daemon"])
@pytest.mark.parametrize("explicit,expected", [(None, 7), (1, 1), (2, 2)])
def test_manual_dispatch_config_and_explicit_precedence(configured_home, monkeypatch, command, explicit, expected):
    from hermes_cli import kanban_db as kb, kanban_db_connect as kbc
    from hermes_cli import kanban_db_dispatch as dispatch, kanban_ops

    captured = {}
    monkeypatch.setattr(kbc, "connect_closing", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(kb, "init_db", lambda: None)

    def tick(conn, **kwargs):
        captured.update(kwargs)
        return kb.DispatchResult()

    monkeypatch.setattr(dispatch, "dispatch_once", tick)
    monkeypatch.setattr(dispatch, "run_daemon", lambda **kwargs: captured.update(kwargs))
    args = parse(command, explicit)
    handler = kanban_ops._cmd_dispatch if command == "dispatch" else kanban_ops._cmd_daemon
    assert handler(args) == 0
    assert captured["failure_limit"] == expected


def test_shared_manual_dispatch_limits_include_configured_breaker(configured_home):
    from hermes_cli.kanban_dispatch_tick import configured_dispatch_kwargs

    assert configured_dispatch_kwargs()["failure_limit"] == 7


@pytest.mark.parametrize("value,expected", [(None, None), ("invalid", None), (0, None), (-1, None), ("8", 8)])
def test_configured_breaker_uses_existing_positive_integer_normalization(configured_home, value, expected):
    from hermes_cli import kanban_db_dispatch as dispatch
    from hermes_cli.kanban_dispatch_tick import configured_dispatch_kwargs

    (configured_home / "config.yaml").write_text(json.dumps({"kanban": {"failure_limit": value}}))
    expected = dispatch.DEFAULT_FAILURE_LIMIT if expected is None else expected
    assert configured_dispatch_kwargs()["failure_limit"] == expected


def test_forced_daemon_does_not_resolve_unrelated_capacity(configured_home, monkeypatch):
    from hermes_cli import kanban_db as kb, kanban_db_dispatch as dispatch, kanban_ops

    def unrelated_capacity():
        pytest.fail("daemon failure-limit resolution must not probe capacity")

    monkeypatch.setattr("hermes_cli.kanban_dispatch_tick.configured_dispatch_kwargs", unrelated_capacity)
    monkeypatch.setattr(kb, "init_db", lambda: None)
    captured = {}
    monkeypatch.setattr(dispatch, "run_daemon", lambda **kwargs: captured.update(kwargs))
    assert kanban_ops._cmd_daemon(parse("daemon")) == 0
    assert captured["failure_limit"] == 7


def test_forced_daemon_unreadable_config_refuses_before_db(configured_home, monkeypatch):
    from hermes_cli import kanban_db as kb, kanban_ops

    (configured_home / "config.yaml").write_text("kanban: [unterminated\n")
    monkeypatch.setattr(kb, "init_db", lambda: pytest.fail("unreadable config reached DB init"))
    assert kanban_ops._cmd_daemon(parse("daemon")) == 1


def test_deprecated_daemon_refuses_without_force_before_config_or_db(configured_home, monkeypatch):
    from hermes_cli import kanban_db as kb, kanban_ops

    def forbidden():
        pytest.fail("deprecated daemon admission must precede config and DB work")

    monkeypatch.setattr("hermes_cli.kanban_dispatch_config.load_dispatch_section", forbidden)
    monkeypatch.setattr(kb, "init_db", forbidden)
    args = parse("daemon")
    args.force = False
    assert kanban_ops._cmd_daemon(args) == 2

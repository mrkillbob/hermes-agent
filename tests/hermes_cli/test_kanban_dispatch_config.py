"""Invalid dispatcher configuration must refuse work instead of dropping caps."""
import argparse
import json
from pathlib import Path

import pytest
import hermes_yaml as yaml

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli.config import FailedConfigRead
from hermes_cli.kanban_dispatch_tick import configured_dispatch_kwargs


@pytest.mark.parametrize("cfg", [
    FailedConfigRead({"kanban": {"max_in_progress": 2}}, error=OSError("unreadable")),
    None, [], {}, {"kanban": None}, {"kanban": []}, {"kanban": "invalid"},
    {"kanban": {"max_in_progress": "invalid"}},
    {"kanban": {"max_spawn": 0}},
    {"kanban": {"max_in_progress_per_profile": False}},
    {"kanban": {"max_in_progress_per_model": 1.5}},
    {"kanban": {"max_in_progress_by_profile": []}},
    {"kanban": {"max_in_progress_by_model": {"local/test": 0}}},
    {"kanban": {"priority_runtime_guard": []}},
    {"kanban": {"priority_runtime_guard": {"enabled": "false"}}},
    {"kanban": {"priority_runtime_guard": {"max_in_progress": 0}}},
    {"kanban": {"priority_runtime_guard": {"max_in_progress": 1.5}}},
    {"kanban": {"priority_runtime_guard": {"max_in_progress": False}}},
    {"kanban": {"priority_runtime_guard": {"enabled": True}}},
    {"kanban": {"priority_runtime_guard": {"enabled": True, "project_roots": "invalid"}}},
    {"kanban": {"priority_runtime_guard": {"enabled": True, "project_roots": ["relative"]}}},
    {"kanban": {"priority_runtime_guard": {"enabled": True, "project_roots": ["/synthetic"],
                                             "entrypoints": []}}},
    {"kanban": {"priority_runtime_guard": {"enabled": True, "project_roots": ["/synthetic"],
                                             "entrypoints": ["/outside/main.py"]}}},
    {"kanban": {"priority_runtime_guard": {"enabled": True, "project_roots": ["/synthetic\0"]}}},
    {"kanban": {"priority_runtime_guard": {"enabled": True, "project_roots": ["/synthetic"],
                                             "entrypoints": ["main\0.py"]}}},
])
def test_invalid_loaded_config_refuses_instead_of_returning_uncapped_limits(monkeypatch, cfg):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: cfg)
    with pytest.raises((ValueError, OSError, TypeError)):
        configured_dispatch_kwargs()


def test_cli_malformed_yaml_never_claims_a_ready_card(tmp_path, monkeypatch, capsys):
    from hermes_cli import kanban_ops

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="Synthetic review", assignee="fixture")
    (home / "config.yaml").write_text("kanban: [unterminated\n")
    args = argparse.Namespace(dry_run=False, max=None, failure_limit=2, json=True)
    assert kanban_ops._cmd_dispatch(args) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "config_unavailable"
    with kbc.connect_closing() as conn:
        assert kb.get_task(conn, tid).status == "ready"
        assert not any(e.kind == "claimed" for e in kb.list_events(conn, tid))


def test_valid_config_limits_real_dispatch_to_host_capacity(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (home / "config.yaml").write_text("kanban:\n  max_in_progress: 1\n  max_spawn: 3\n")
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda name: True)
    kb.init_db()
    with kbc.connect_closing() as conn:
        first = kb.create_task(conn, title="First synthetic review", assignee="fixture")
        second = kb.create_task(conn, title="Second synthetic review", assignee="fixture")
        spawned = []
        result = kbd.dispatch_once(conn, spawn_fn=lambda task, *a: spawned.append(task.id),
                                   **configured_dispatch_kwargs())
        assert len(spawned) == 1 and len(result.spawned) == 1
        assert sorted(kb.get_task(conn, tid).status for tid in (first, second)) == ["ready", "running"]


@pytest.mark.parametrize("text", ["kanban: [unterminated\n", "[]\n", "kanban: []\n",
                                  "kanban:\n  max_in_progress: invalid\n"])
def test_unreadable_root_policy_is_not_discarded_for_a_named_profile(tmp_path, monkeypatch, text):
    root = tmp_path / "root"
    root.mkdir()
    (root / "config.yaml").write_text(text)
    monkeypatch.setattr("hermes_constants.get_default_hermes_root", lambda: root)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"kanban": {"max_spawn": 2}})
    with pytest.raises((yaml.YAMLError, ValueError, TypeError)):
        configured_dispatch_kwargs()


def test_profile_defaults_do_not_erase_the_root_host_cap(tmp_path, monkeypatch):
    root = tmp_path / "root"
    profile = root / "profiles" / "fixture"
    profile.mkdir(parents=True)
    (root / "config.yaml").write_text("kanban:\n  max_in_progress: 2\n")
    (profile / "config.yaml").write_text("kanban:\n  max_spawn: 3\n")
    monkeypatch.setenv("HERMES_HOME", str(profile))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hermes_constants.get_default_hermes_root", lambda: root)
    limits = configured_dispatch_kwargs()
    assert limits["max_in_progress"] == 2
    assert limits["max_spawn"] == 3
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda name: True)
    kb.init_db()
    with kbc.connect_closing() as conn:
        for i in range(3):
            kb.create_task(conn, title=f"Synthetic review {i}", assignee="fixture")
        spawned = []
        kbd.dispatch_once(conn, spawn_fn=lambda task, *a: spawned.append(task.id), **limits)
        assert len(spawned) == 2


def test_managed_host_cap_overrides_user_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (home / "config.yaml").write_text("kanban:\n  max_in_progress: 8\n")
    monkeypatch.setattr("hermes_cli.managed_scope.load_managed_config",
                        lambda: {"kanban": {"max_in_progress": 1}})
    monkeypatch.setattr("hermes_cli.profiles.profile_exists", lambda name: True)
    kb.init_db()
    with kbc.connect_closing() as conn:
        for i in range(2):
            kb.create_task(conn, title=f"Synthetic managed review {i}", assignee="fixture")
        spawned = []
        kbd.dispatch_once(conn, spawn_fn=lambda task, *a: spawned.append(task.id),
                          **configured_dispatch_kwargs())
        assert len(spawned) == 1


def test_malformed_managed_config_cannot_silently_drop_its_caps(tmp_path, monkeypatch):
    home, managed = tmp_path / "home", tmp_path / "managed"
    home.mkdir()
    managed.mkdir()
    (home / "config.yaml").write_text("kanban:\n  max_spawn: 2\n")
    (managed / "config.yaml").write_text("kanban: [unterminated\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("hermes_cli.managed_scope.get_managed_dir", lambda: managed)
    with pytest.raises(yaml.YAMLError):
        configured_dispatch_kwargs()


def test_remote_backend_ignores_local_managed_config_validation(tmp_path, monkeypatch):
    from hermes_cli.kanban_dispatch_config import load_dispatch_section

    home, managed = tmp_path / "home", tmp_path / "managed"
    home.mkdir()
    managed.mkdir()
    (managed / "config.yaml").write_text("kanban: [unterminated\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("hermes_cli.managed_scope.get_managed_dir", lambda: managed)
    monkeypatch.setattr("hermes_cli.config_backend.get_config_backend",
                        lambda: type("RemoteBackend", (), {"honors_managed_config": lambda self: False})())
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"kanban": {"max_spawn": 2}})
    monkeypatch.setattr("hermes_cli.kanban_dispatch_config.strict_shared_config", lambda section: section)

    assert load_dispatch_section()["max_spawn"] == 2

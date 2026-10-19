"""Workspace-container labels as the orphan reaper reads them (sibling of test_docker_environment.py)."""
import re
import subprocess

import pytest

from tests.tools.test_docker_environment import (
    _labels_in_run_args, _make_dummy_env, _mock_subprocess_run, _run_args_from_calls,
)
from tools.environments import docker as docker_env


@pytest.mark.parametrize("base", ["default", "profile:research-assistant-for-the-quarterly-ops"])
def test_workspace_container_labels_identify_it_to_the_orphan_reaper(monkeypatch, base):
    """The orphan reaper must recognise an idle-STOPPED workspace container from the labels the
    create path actually stamps. ``hermes-task-id`` is truncated to 63 chars, so a profile name of
    36+ chars cut the ``_ws_<digest>`` suffix and the next sweep ``docker rm``-ed the container,
    losing everything installed outside the mounts."""
    monkeypatch.setattr(docker_env, "find_docker", lambda: "/usr/bin/docker")
    calls = _mock_subprocess_run(monkeypatch)
    _make_dummy_env(task_id=f"{base}:ws:0123456789abcdef")
    labels = dict(lbl.split("=", 1) for lbl in _labels_in_run_args(_run_args_from_calls(calls)))

    def _inspect(cmd, **kwargs):
        key = re.search(r'Labels "([^"]+)"', cmd[cmd.index("--format") + 1]).group(1)
        return subprocess.CompletedProcess(cmd, 0, stdout=labels.get(key, "<no value>") + "\n", stderr="")

    monkeypatch.setattr(docker_env.subprocess, "run", _inspect)
    assert docker_env._is_idle_workspace_container("/usr/bin/docker", "cid"), labels


def test_non_workspace_container_is_not_an_idle_workspace(monkeypatch):
    monkeypatch.setattr(docker_env, "find_docker", lambda: "/usr/bin/docker")
    calls = _mock_subprocess_run(monkeypatch)
    _make_dummy_env(task_id="profile:research-assistant-for-the-quarterly-ops")
    labels = dict(lbl.split("=", 1) for lbl in _labels_in_run_args(_run_args_from_calls(calls)))

    def _inspect(cmd, **kwargs):
        key = re.search(r'Labels "([^"]+)"', cmd[cmd.index("--format") + 1]).group(1)
        return subprocess.CompletedProcess(cmd, 0, stdout=labels.get(key, "<no value>") + "\n", stderr="")

    monkeypatch.setattr(docker_env.subprocess, "run", _inspect)
    assert not docker_env._is_idle_workspace_container("/usr/bin/docker", "cid"), labels

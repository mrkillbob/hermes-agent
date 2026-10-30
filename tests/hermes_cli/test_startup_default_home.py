"""The fast-start paths resolve the same default home as everything else.

``HERMES_DATA_DIR_SUFFIX`` (and ``%LOCALAPPDATA%`` on Windows) move the platform default, so a hardcoded
``~/.hermes`` in the pre-import paths read a different config and active profile than the rest of Hermes.
"""
from __future__ import annotations

import os

import pytest

from hermes_constants import get_process_hermes_home


@pytest.fixture
def default_home(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("HERMES_DATA_DIR_SUFFIX", "-fastcheck")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    return get_process_hermes_home()


def test_the_pre_import_home_follows_the_platform_default(default_home):
    from hermes_cli import _startup_fast

    assert _startup_fast._resolved_home() == str(default_home)


def test_the_early_interface_config_is_read_from_the_platform_default_home(default_home):
    from hermes_cli.main import _early_interface_config_path

    assert _early_interface_config_path() == os.path.join(str(default_home), "config.yaml")

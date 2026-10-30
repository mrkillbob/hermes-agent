"""Structural skill path substitutions preserve native syntax and literal values."""

import ntpath
import os
import posixpath

import pytest

from agent.skill_path_env import expand_metadata_path


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("entry", [
    "$HOME/path", "${HOME}/path", "$UNKNOWN_SYNTHETIC/path", "${UNKNOWN_SYNTHETIC}",
    "${HOME", "${UNCLOSED/$HOME", "${UNCLOSED/${HOME}", "$", "$$HOME", "${HOME}${HOME}",
    "'$HOME'", "'$HOME", "%HOME%/path",
    "%%HOME%%", "%HOME", "$HOME-tail", "$HÖME", "${HOME}/$HOME/%HOME%",
])
def test_unoverridden_variables_match_native_expansion(monkeypatch, windows, entry):
    monkeypatch.setenv("HOME", "/synthetic")
    monkeypatch.delenv("UNKNOWN_SYNTHETIC", raising=False)
    native = ntpath.expandvars if windows else posixpath.expandvars
    assert expand_metadata_path(entry, {}, windows=windows) == native(entry)


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("entry", ["$HERMES_HOME/extra", "${HERMES_HOME}/extra"])
def test_destination_values_are_literal_and_do_not_change_environment(monkeypatch, windows, entry):
    monkeypatch.setenv("HERMES_HOME", "/creator")
    monkeypatch.setenv("SECOND", "wrong")
    before = dict(os.environ)
    destination = "/worker/$SECOND/%SECOND%"
    assert expand_metadata_path(entry, {"HERMES_HOME": destination}, windows=windows) == destination + "/extra"
    assert dict(os.environ) == before


def test_windows_percent_override_and_quoted_literal():
    values = {"HERMES_HOME": "worker%OTHER%$OTHER"}
    assert expand_metadata_path("%HERMES_HOME%", values, windows=True) == values["HERMES_HOME"]
    assert expand_metadata_path("'%HERMES_HOME%'", values, windows=True) == "'%HERMES_HOME%'"


@pytest.mark.parametrize("entry", ["$hermes_home", "${Hermes_Home}", "%hermes_home%"])
def test_windows_structural_names_are_case_insensitive(entry):
    assert expand_metadata_path(entry, {"HERMES_HOME": "destination"}, windows=True) == "destination"

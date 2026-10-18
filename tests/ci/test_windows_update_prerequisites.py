"""Dependent interpreter cells report the installer failure that prevented their work."""
import pytest

from tests.e2e.core.windows_update._machine import Journey
from tests.e2e.core.windows_update import test_interpreter as interpreter
from tests.e2e.core.windows_update import test_git_and_holders as git_holders


class _Machine:
    def evidence(self):
        return "synthetic machine: no install/update/gateway ran"


def test_failed_install_prerequisite_survives_missing_later_steps():
    journey = Journey(_Machine())
    original = AssertionError("install did not finish within 1500s\nlast phase: npm ci")

    def install():
        raise original

    journey.step("install", install)
    with pytest.raises(RuntimeError, match="journey step 'install' failed: install did not finish") as caught:
        journey.require_steps("install", "install_ok", "update", "update_ok")
    assert caught.value.__cause__ is original
    assert "last phase: npm ci" in str(caught.value)
    assert "never ran" not in str(caught.value)


def test_successful_prerequisites_do_not_invent_or_hide_later_failure():
    journey = Journey(_Machine())
    journey.step("install", lambda: "installed")
    journey.step("install_ok", lambda: None)
    original = RuntimeError("update failed in synthetic fixture")
    journey.results["update"] = original
    with pytest.raises(RuntimeError, match="journey step 'update' failed") as caught:
        journey.require_steps("install", "install_ok", "update", "update_ok")
    assert caught.value.__cause__ is original
    assert journey["install"] == "installed"


def test_unrun_prerequisite_keeps_the_missing_step_evidence():
    journey = Journey(_Machine())
    journey.step("install", lambda: "installed")
    with pytest.raises(RuntimeError, match="journey step 'update' never ran"):
        journey.require_steps("install", "update")


@pytest.mark.parametrize("cell", [interpreter.test_gateway_does_not_load_stale_in_tree_venv,
                                 interpreter.test_cli_turn_ignores_stale_venv_and_path_python])
def test_interpreter_cells_surface_original_install_failure(cell):
    journey = Journey(_Machine())
    original = AssertionError("install did not finish within 1500s; npm ci pending")
    journey.results["install"] = original
    with pytest.raises(RuntimeError, match="journey step 'install' failed") as caught:
        cell(journey)
    assert caught.value.__cause__ is original


@pytest.mark.parametrize("cell", [git_holders.test_update_fetches_into_the_installers_partial_clone,
                                 git_holders.test_list_venv_holders_reports_live_holders,
                                 git_holders.test_list_venv_holders_empty_once_they_exit])
@pytest.mark.parametrize("failed_step", ["install", "install_ok"])
def test_git_holder_cells_surface_original_installer_failure(cell, failed_step):
    journey = Journey(_Machine())
    original = AssertionError("synthetic installer failed while npm ci was pending")
    journey.results["install"] = "completed"
    journey.results[failed_step] = original

    with pytest.raises(RuntimeError, match=f"journey step '{failed_step}' failed") as caught:
        cell(journey)

    assert caught.value.__cause__ is original
    assert "never ran" not in str(caught.value)

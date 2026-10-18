"""Dependent interpreter cells report the installer failure that prevented their work."""
import pytest

from tests.e2e.core.windows_update._machine import Journey
from tests.e2e.core.windows_update import test_interpreter as interpreter


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

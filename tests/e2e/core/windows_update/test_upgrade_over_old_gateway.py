"""Elevated pre-runtime gateway upgrade, followed by unelevated ownership checks (#134889 W4).

The standard-user cold-install journey runs in its own test file/hosted matrix cell.
"""
from __future__ import annotations

import pytest

from tests.e2e.core.windows_update._machine import REQUIRES_OPT_IN, Journey, fail_with, one_shot_turn
from tests.fakes.fake_llm_provider import FakeLLMServer
from tests.e2e.core.windows_update._old_gateway import (
    PRE_RUNTIME_RELEASE,
    _assert_old_gateway_held_lock,
    _assert_status_sees,
    _assert_turn,
    _assert_update_replaced_gateway,
    _lock_held,
    _machine,
    _pid,
    _stopped,
    _token_control,
    _wait_new_gateway,
    _user_write_access,
)

pytestmark = [pytest.mark.platforms("windows"), pytest.mark.integration,
              pytest.mark.live_system_guard_bypass, REQUIRES_OPT_IN]


@pytest.fixture(scope="module")
def elevated(tmp_path_factory):
    with FakeLLMServer() as srv:
        machine = _machine(tmp_path_factory, srv, "w4e")
        j = Journey(machine)
        try:
            j.step("control", lambda: _token_control(machine))
            install = j.step("install", machine.install)
            if j.ok("install"):
                j.step("install_ok", lambda: j.require("install", install.returncode == 0,
                                                        f"install.ps1 ({PRE_RUNTIME_RELEASE[:11]}) exited "
                                                        f"{install.returncode}", install))
            if j.ok("install_ok"):
                with machine.gateway_phase():
                    j.step("installed_base", machine.installed_head)
                    j.step("spawn_old", machine.spawn_gateway)
                    old = j.step("old_state", machine.wait_gateway_running)
                    j.step("old_lock_held", lambda: _lock_held(machine))
                    machine.advance()  # the new release lands on main: an update is available
                    j.step("update", machine.update)
                    j.step("new_state", lambda: _wait_new_gateway(machine, _pid(old) or None))
                    j.step("status_admin", lambda: machine.hermes("gateway", "status", label="status-admin"))
                    j.step("status_user", lambda: machine.hermes("gateway", "status", label="status-user",
                                                                  as_user=True))
                    j.step("turn_admin", lambda: one_shot_turn(machine, srv, "turn-admin"))
                    j.step("user_access", lambda: _user_write_access(machine))
                    # The elevated gateway goes; the same user, unelevated, takes the lock next.
                    new_pid = _pid(j.results.get("new_state"))
                    j.step("stop_admin", lambda: machine.hermes("gateway", "stop", label="stop-admin"))
                    j.step("admin_stopped", lambda: _stopped(new_pid))
                    j.step("spawn_user", lambda: machine.spawn_gateway(as_user=True))
                    j.step("user_state", lambda: _wait_new_gateway(machine, new_pid or None))
                    j.step("user_status", lambda: machine.hermes("gateway", "status", label="status-user-gw",
                                                                  as_user=True))
                    j.step("user_turn", lambda: one_shot_turn(machine, srv, "turn-user", as_user=True))
                    user_pid = _pid(j.results.get("user_state"))
                    j.step("stop_user", lambda: machine.hermes("gateway", "stop", label="stop-user", as_user=True))
                    j.step("user_stopped", lambda: _stopped(user_pid))
                    machine.kill_owned()
            yield j
        finally:
            machine.teardown()


def test_elevated_harness_token_is_standard(elevated: Journey) -> None:
    assert elevated["control"] == "elevated=False admin=False"


def test_elevated_old_gateway_held_lock_before_update(elevated: Journey) -> None:
    _assert_old_gateway_held_lock(elevated)


def test_elevated_update_replaces_old_gateway(elevated: Journey) -> None:
    _assert_update_replaced_gateway(elevated)


def test_elevated_status_sees_new_gateway(elevated: Journey) -> None:
    _assert_status_sees(elevated, "status_admin", "new_state", "administrator console")
    _assert_status_sees(elevated, "status_user", "new_state", "same user, not elevated")


def test_elevated_turn_replies_after_update(elevated: Journey) -> None:
    _assert_turn(elevated, "turn_admin", "administrator console")


def test_elevated_upgrade_leaves_gateway_files_usable_unelevated(elevated: Journey) -> None:
    m, verdicts = elevated.machine, elevated["user_access"]
    elevated["update"]
    assert any(p.name == "gateway.lock" for p in verdicts), fail_with(
        m, f"no gateway.lock after the upgrade under {m.hermes_home}")
    broken = [f"{p}: {why}" for p, why in verdicts.items() if why]
    assert not broken, fail_with(
        m, f"after an elevated upgrade the same user, not elevated, cannot write: {'; '.join(broken)}")


def test_elevated_upgrade_then_user_gateway_takes_lock(elevated: Journey) -> None:
    m = elevated.machine
    stop = elevated["stop_admin"]
    assert stop.returncode == 0 and elevated["admin_stopped"], fail_with(
        m, f"`hermes gateway stop` left the elevated gateway (pid {_pid(elevated['new_state'])}) running", stop)
    elevated["spawn_user"]
    state = elevated["user_state"]
    sha = str(state.get("code_sha") or "")
    assert m.next.startswith(sha) and len(sha) >= 7, fail_with(
        m, f"the unelevated gateway serves {sha!r}, expected {m.next}")
    _assert_status_sees(elevated, "user_status", "user_state", "same user, not elevated")
    _assert_turn(elevated, "user_turn", "same user, not elevated")
    assert elevated["stop_user"].returncode == 0 and elevated["user_stopped"], fail_with(
        m, f"the unelevated `hermes gateway stop` left its gateway (pid {_pid(state)}) running",
        elevated["stop_user"])

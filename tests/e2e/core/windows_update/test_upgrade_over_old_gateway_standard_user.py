"""Pre-runtime install, old gateway, update and new gateway under a standard-user token (#134889 W4).

One cold-install journey per file gives it its own unchanged hosted file timeout.
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
)

pytestmark = [pytest.mark.platforms("windows"), pytest.mark.integration,
              pytest.mark.live_system_guard_bypass, REQUIRES_OPT_IN]


@pytest.fixture(scope="module")
def standard_user(tmp_path_factory):
    with FakeLLMServer() as srv:
        machine = _machine(tmp_path_factory, srv, "w4u")
        j = Journey(machine)
        try:
            j.step("control", lambda: _token_control(machine))
            install = j.step("install", lambda: machine.install(as_user=True))
            if j.ok("install"):
                j.step("install_ok", lambda: j.require("install", install.returncode == 0,
                                                        f"install.ps1 ({PRE_RUNTIME_RELEASE[:11]}) as a standard "
                                                        f"user exited {install.returncode}", install))
            if j.ok("install_ok"):
                with machine.gateway_phase():
                    j.step("installed_base", machine.installed_head)
                    j.step("spawn_old", lambda: machine.spawn_gateway(as_user=True))
                    old = j.step("old_state", machine.wait_gateway_running)
                    j.step("old_lock_held", lambda: _lock_held(machine))
                    machine.advance()
                    j.step("update", lambda: machine.update(as_user=True))
                    j.step("new_state", lambda: _wait_new_gateway(machine, _pid(old) or None))
                    j.step("status_user", lambda: machine.hermes("gateway", "status", label="status-user",
                                                                  as_user=True))
                    j.step("user_turn", lambda: one_shot_turn(machine, srv, "turn-user", as_user=True))
                    new_pid = _pid(j.results.get("new_state"))
                    j.step("stop_user", lambda: machine.hermes("gateway", "stop", label="stop-user", as_user=True))
                    j.step("user_stopped", lambda: _stopped(new_pid))
                    machine.kill_owned()
            yield j
        finally:
            machine.teardown()


def test_standard_user_harness_token_is_standard(standard_user: Journey) -> None:
    assert standard_user["control"] == "elevated=False admin=False"


def test_standard_user_old_gateway_held_lock_before_update(standard_user: Journey) -> None:
    _assert_old_gateway_held_lock(standard_user)


def test_standard_user_update_replaces_old_gateway(standard_user: Journey) -> None:
    _assert_update_replaced_gateway(standard_user)


def test_standard_user_status_and_turn_after_update(standard_user: Journey) -> None:
    _assert_status_sees(standard_user, "status_user", "new_state", "standard user")
    _assert_turn(standard_user, "user_turn", "standard user")
    m = standard_user.machine
    assert standard_user["stop_user"].returncode == 0 and standard_user["user_stopped"], fail_with(
        m, f"`hermes gateway stop` left the gateway (pid {_pid(standard_user['new_state'])}) running",
        standard_user["stop_user"])

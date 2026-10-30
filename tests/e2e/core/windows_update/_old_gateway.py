"""Shared ownership and acceptance checks for pre-runtime Windows gateway upgrades.

Each collecting test module owns one independent cold-install journey. Helpers here preserve
real token, lock, gateway identity, status and turn checks without collecting extra tests.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from tests.e2e.core.windows._helpers import wait_until
from tests.e2e.core.windows_update._machine import Machine, Journey, fail_with, new_machine
from tests.fakes.fake_llm_provider import FakeLLMServer

# The first parent of the #106742 merge (eb75627faf5): the last main before the unified runtime.
PRE_RUNTIME_RELEASE = os.environ.get("HERMES_E2E_W4_BASE", "d04056d609bb53530843a6c630493243b2ebdc24")
_STATUS_LINE = re.compile(r"^.*[✓✗].*[Gg]ateway.*$", re.MULTILINE)
_RESTART_FAILURE = re.compile(
    r"^.*(recovery failed|restart could not be verified|restart incomplete|not verified alive).*$", re.MULTILINE)


def _status_line(text: str) -> str:
    found = _STATUS_LINE.findall(text)
    return found[0].strip() if found else "<no status line>"


def _alive(pid: int) -> bool:
    import psutil

    try:
        return psutil.Process(pid).is_running()
    except psutil.Error:
        return False


def _gateway_files(m: Machine) -> list[Path]:
    """What a gateway opens for writing at start: its home, lock, pid and state records, logs, the
    per-user host lock dir, and every top-level entry the elevated run left in the home."""
    home = m.hermes_home
    paths = [home, home / "gateway.lock", home / "gateway.pid", home / "gateway_state.json", home / "logs"]
    lock_dir = m.profile / ".local" / "state" / "hermes" / "gateway-locks"
    if lock_dir.is_dir():
        paths += [lock_dir, *sorted(lock_dir.iterdir())]
    skip = {"hermes-agent", "tools", "bin"}  # code and toolchain: read+execute (test_paths_and_acls.py)
    paths += [p for p in sorted(home.iterdir()) if p.name not in skip]
    seen, out = set(), []
    for p in paths:
        key = os.path.normcase(str(p))
        if key not in seen and p.exists():
            seen.add(key)
            out.append(p)
    return out


def _user_write_access(m: Machine) -> dict[Path, str]:
    from tests.e2e.core.windows_update._tokens import FILE_READ_WRITE, standard_user_access

    return standard_user_access(_gateway_files(m), FILE_READ_WRITE)


def _lock_held(m: Machine) -> bool:
    """True while some process holds ``gateway.lock`` (a byte-range lock or a sharing deny)."""
    import msvcrt

    path = m.hermes_home / "gateway.lock"
    try:
        fh = open(path, "r+b")
    except PermissionError:
        return True
    except OSError:
        return False
    with fh:
        fh.seek(1024 * 1024)  # gateway/status.py _WINDOWS_LOCK_OFFSET, the same in both releases
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return True
        fh.seek(1024 * 1024)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    return False


def _machine(tmp_path_factory, srv: FakeLLMServer, label: str) -> Machine:
    from tests.e2e.core.windows_update._tokens import grant_user_profile_acl, hand_to_user

    machine = new_machine(tmp_path_factory.mktemp(label), srv.base_url, label=label, system_git=True,
                          base_ref=PRE_RUNTIME_RELEASE)
    # A real profile's DACL (the user owns their home), and the origin + gitconfig the user can
    # read: harness facts, so a standard-user verdict is about Hermes and nothing else.
    grant_user_profile_acl(machine.profile)
    hand_to_user(machine.root)
    return machine


def _token_control(m: Machine) -> str:
    from tests.e2e.core.windows_update._tokens import harness_control, probe_token_elevation

    harness_control(m.root)
    return probe_token_elevation(m.profile, m.env(as_user=True), m.logs / "00-token-probe.log")


def _wait_new_gateway(m: Machine, old_pid: int | None) -> dict:
    return m.wait_gateway_running(not_pid=old_pid)


def _pid(state) -> int:
    return int(state.get("pid") or 0) if isinstance(state, dict) else 0


def _stopped(pid: int) -> bool:
    try:
        wait_until(lambda: not _alive(pid), 90, f"gateway pid {pid} to exit", interval=0.5)
    except AssertionError:
        return False
    return True


def _assert_old_gateway_held_lock(j: Journey) -> None:
    m, state = j.machine, j["old_state"]
    assert j["installed_base"] == m.head, fail_with(
        m, f"the install is not the pre-runtime release: checkout {j['installed_base']}, expected {m.head}")
    assert str(state.get("code_sha") or "")[:7] == m.head[:7] or not state.get("code_sha"), fail_with(
        m, f"the gateway before the update serves {state.get('code_sha')!r}, expected the base {m.head}")
    assert j["old_lock_held"], fail_with(
        m, f"the old gateway (pid {state.get('pid')}) is running but nothing holds gateway.lock")


def _assert_update_replaced_gateway(j: Journey) -> None:
    m, run = j.machine, j["update"]
    failure = _RESTART_FAILURE.search(run.stdout)
    assert run.returncode == 0 and failure is None and m.installed_head() == m.next, fail_with(
        m, f"hermes update over the running pre-runtime gateway failed (rc={run.returncode}, checkout "
           f"{m.installed_head()}, expected {m.next}): "
           f"{failure.group(0).strip() if failure else '<no restart message>'}", run)
    state = j["new_state"]
    sha = str(state.get("code_sha") or "")
    assert len(sha) >= 7 and m.next.startswith(sha), fail_with(
        m, f"after the update the gateway (pid {state.get('pid')}) serves code_sha={sha!r}, expected {m.next}")
    assert _pid(state) != _pid(j["old_state"]), fail_with(m, "the old gateway was never replaced")


def _assert_status_sees(j: Journey, status_key: str, state_key: str, who: str) -> None:
    m, status, state = j.machine, j[status_key], j[state_key]
    line = _status_line(status.stdout)
    assert status.returncode == 0 and line.startswith("✓") and str(_pid(state)) in line, fail_with(
        m, f"`hermes gateway status` ({who}) does not see the running gateway (pid {_pid(state)}): {line!r}",
        status)


def _assert_turn(j: Journey, key: str, who: str) -> None:
    m, turn = j.machine, j[key]
    assert turn.ok, fail_with(
        m, f"a turn ({who}) after the upgrade got no reply (reply printed={turn.reply_id in turn.run.stdout}, "
           f"prompt reached provider={turn.reached_wire})", turn.run)

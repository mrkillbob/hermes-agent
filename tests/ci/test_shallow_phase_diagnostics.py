"""Shallow-upgrade harness selection must not execute unrelated update phases."""
from contextlib import ExitStack, contextmanager
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest

from tests.e2e.core.upgrade.git import test_shallow_install as shallow


@pytest.fixture
def world(monkeypatch):
    events = []
    state = SimpleNamespace(evidence="first attempt", head="target", returncode=0)
    request = SimpleNamespace(command="fetch", body_bytes=10)

    def update():
        events.append("update")
        return CompletedProcess(["synthetic-update"], state.returncode, "output", "")

    def forbidden(*args, **kwargs):
        pytest.fail("an unrelated phase was executed")

    w = SimpleNamespace(
        install_shape={"shallow": "true"}, install_bytes=10,
        srv=SimpleNamespace(mark=lambda: 0, since=lambda mark: [request]),
        set_main=lambda target: events.append(("target", target)),
        update=update, head=lambda: state.head,
        diag=lambda *args: state.evidence, publish=forbidden, version=forbidden,
    )

    @contextmanager
    def enter(*args, **kwargs):
        events.append("enter")
        try:
            yield w
        finally:
            events.append("exit")

    monkeypatch.setattr(shallow.G, "world", enter)
    monkeypatch.setattr(shallow.G, "n1_tag", lambda: "release")
    monkeypatch.setattr(shallow.G, "n1_base", lambda: "base")
    monkeypatch.setattr(shallow.I, "head_sha", lambda: "target")
    return w, state, events


def test_first_leg_stops_before_second_update_and_freezes_evidence(tmp_path, world):
    w, state, events = world
    with ExitStack() as stack:
        run = shallow._depth1(tmp_path, stack)
        frozen = run["diag1"]
        state.evidence = "later attempt"
        state.head = "later-target"
        w.install_shape["shallow"] = "false"
        shallow.test_stale_depth1_install_updates_without_fetching_the_whole_history(run)
        assert run["diag1"] == frozen
        assert "first attempt" in frozen and "later attempt" not in frozen
        assert run["shape0"] == {"shallow": "true"}
        assert events == ["enter", ("target", "target"), "update"]
    assert events[-1] == "exit"


def test_failed_first_leg_refuses_second_update(tmp_path, world):
    _, state, events = world
    state.returncode = 1
    with ExitStack() as stack:
        run = shallow._depth1(tmp_path, stack)
        with pytest.raises(AssertionError, match="N-1 -> HEAD update failed"):
            shallow.test_second_update_after_the_unshallowing_update_succeeds(run)
        assert events.count("update") == 1
    assert events[-1] == "exit"


def test_second_leg_runs_after_successful_first_update(tmp_path, world):
    w, state, events = world

    def publish(*args):
        events.append("publish")
        state.head = "second-target"
        state.evidence = "second attempt"
        return state.head

    def version():
        events.append("version")
        return CompletedProcess(["synthetic-version"], 0, "version", "")

    with ExitStack() as stack:
        run = shallow._depth1(tmp_path, stack)
        w.publish, w.version = publish, version
        shallow.test_second_update_after_the_unshallowing_update_succeeds(run)
        assert events == ["enter", ("target", "target"), "update", "publish", "update", "version"]
        assert "first attempt" in run["diag1"] and "second attempt" not in run["diag1"]


def test_setup_exception_closes_entered_world(tmp_path, world):
    w, _, events = world

    def failed_update():
        raise RuntimeError("synthetic setup failure")

    w.update = failed_update
    with pytest.raises(RuntimeError, match="synthetic setup failure"):
        with ExitStack() as stack:
            shallow._depth1(tmp_path, stack)
    assert events[-1] == "exit"

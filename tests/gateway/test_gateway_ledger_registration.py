"""Every gateway startup route registers the runtime owner, including CLI/Desktop ensure."""
import os

import pytest


@pytest.mark.parametrize("failure", [None, "lock", "pid", "host"])
def test_shared_gateway_claim_registers_only_the_owner(tmp_path, monkeypatch, failure):
    import atexit
    import hermes_constants
    from gateway import run as gateway_run, run_bootstrap, status
    from hermes_cli import gateway_multiplex_mode, process_identity

    home = tmp_path / "machine-root"
    home.mkdir()
    monkeypatch.setattr(hermes_constants, "get_default_hermes_root", lambda: home)
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: home)
    monkeypatch.setattr(status, "get_running_pid", lambda: None)
    monkeypatch.setattr(status, "acquire_gateway_runtime_lock", lambda: failure != "lock")
    monkeypatch.setattr(status, "release_gateway_runtime_lock", lambda: None)
    def write_pid():
        if failure == "pid":
            raise FileExistsError("another runtime won the PID claim")

    def claim_host(**_):
        if failure == "host":
            raise RuntimeError("host role refused")

    monkeypatch.setattr(status, "write_pid_file", write_pid)
    monkeypatch.setattr(atexit, "register", lambda *_: None)
    monkeypatch.setattr(gateway_run, "_claim_host_gateway_role", claim_host)
    monkeypatch.setattr(gateway_multiplex_mode, "publish_pending_multiplex_decision", lambda: None)
    attached = []
    monkeypatch.setattr(process_identity, "attach_self_to_kill_on_close_job", lambda: attached.append(os.getpid()))

    if failure == "host":
        with pytest.raises(RuntimeError, match="host role refused"):
            run_bootstrap._start_gateway_claim_pid_file()
    else:
        assert run_bootstrap._start_gateway_claim_pid_file() is (failure is None)
    entries = process_identity.ledger_entries(all_installs=True, verified_only=True)
    if failure is not None:
        assert entries == []
        assert attached == []
        return
    assert len(entries) == 1
    entry = entries[0]
    assert entry["pid"] == os.getpid()
    assert entry["purpose"] == "gateway"
    assert entry["create_time"] is not None
    assert entry["hermes_home"] == hermes_constants.hermes_home_key(home)
    assert entry["install"] == process_identity.install_id()
    assert attached == [os.getpid()]

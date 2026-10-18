"""Catalog failure bundles contain only bounded lifecycle facts from their own home."""
import json

# E2E dependency selection belongs at collection, before the per-test home-I/O guard.
from tests.e2e.core.providers import _catalog_helpers as helpers


def test_snapshot_filters_log_payloads_and_state_values(tmp_path):
    from tests.e2e.core.providers._catalog_diagnostics import snapshot
    from hermes_cli.gateway_client_diagnostics import PREFIX

    logs = tmp_path / ".hermes" / "logs"
    logs.mkdir(parents=True)
    (logs / "agent.log").write_text("private raw environment and prompt\n" + PREFIX + json.dumps({
        "phase": "websocket_open_failed", "monotonic_s": 1.25,
        "error_type": "ConnectionRefusedError", "errno": 61, "token": "private-token"}) + "\n")
    (logs.parent / "gateway_state.json").write_text(json.dumps({
        "gateway_state": "running", "pid": 123, "api_origin": "http://private-host", "secret": "private-key"}))
    evidence = snapshot(tmp_path)
    assert evidence["events"] == [{"phase": "websocket_open_failed", "monotonic_s": 1.25,
                                    "error_type": "ConnectionRefusedError", "errno": 61}]
    assert evidence["gateway_state"] == "running"
    assert "private" not in json.dumps(evidence) and "123" not in json.dumps(evidence)


def test_snapshot_bounds_rows_and_ignores_malformed_records(tmp_path):
    from tests.e2e.core.providers._catalog_diagnostics import snapshot
    from hermes_cli.gateway_client_diagnostics import PREFIX

    logs = tmp_path / ".hermes" / "logs"
    logs.mkdir(parents=True)
    (logs / "agent.log").write_text((PREFIX + json.dumps({
        "phase": "reader_close_started", "monotonic_s": 1}) + "\n") * 100 +
        PREFIX + "not-json\n" + PREFIX + json.dumps({"phase": "private-unrecognized"}) + "\n")
    evidence = snapshot(tmp_path)
    assert 0 < len(evidence["events"]) <= 12
    assert "private" not in json.dumps(evidence)


def test_turn_failure_retains_connection_diagnostics_without_changing_exit(monkeypatch, tmp_path):
    from hermes_cli.gateway_client_diagnostics import PREFIX

    logs = tmp_path / ".hermes" / "logs"
    logs.mkdir(parents=True)
    (logs / "agent.log").write_text(PREFIX + json.dumps({"phase": "websocket_open_failed"}) + "\n")

    class Process:
        def __init__(self, argv, **kwargs):
            self.args = argv
            kwargs["stderr"].write("Error: Gateway connection failed; no local fallback\n")

        def wait(self, *, timeout):
            assert timeout == helpers.TURN_TIMEOUT
            return 1

    monkeypatch.setattr(helpers.subprocess, "Popen", Process)
    evidence = {}
    result = helpers.run_hermes(tmp_path, tmp_path, {}, "-z", "fixture", diagnostics=evidence)
    assert result.returncode == 1
    assert result.stderr == "Error: Gateway connection failed; no local fallback\n"
    assert evidence["events"][-1]["phase"] == "websocket_open_failed"
    assert evidence["cli_timed_out"] is False and evidence["final_marker_seen"] is False


def test_timeout_captures_terminal_phase_before_kill(monkeypatch, tmp_path):
    from hermes_cli.gateway_client_diagnostics import PREFIX

    logs = tmp_path / ".hermes" / "logs"
    logs.mkdir(parents=True)
    path = logs / "agent.log"
    path.write_text(PREFIX + json.dumps({"phase": "message_complete_received"}) + "\n")

    class Process:
        def __init__(self, argv, **kwargs):
            self.args = argv
            kwargs["stdout"].write(helpers.FINAL)
            self.killed = False

        def wait(self, *, timeout=None):
            if not self.killed:
                raise helpers.subprocess.TimeoutExpired(self.args, timeout)
            return -9

        def kill(self):
            self.killed = True
            path.write_text("")  # diagnostics must have been collected before teardown

    monkeypatch.setattr(helpers.subprocess, "Popen", Process)
    evidence = {}
    result = helpers.run_hermes(tmp_path, tmp_path, {}, "-z", "fixture", diagnostics=evidence)
    assert result.returncode == -9 and "TIMEOUT after 75.0s" in result.stderr
    assert evidence["cli_timed_out"] is True and evidence["final_marker_seen"] is True
    assert evidence["events"][-1]["phase"] == "message_complete_received"


def test_real_logging_delivers_phase_records_to_the_fixture_snapshot(monkeypatch, tmp_path):
    import hermes_logging
    from hermes_cli.gateway_client_diagnostics import record
    from tests.e2e.core.providers._catalog_diagnostics import snapshot

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    hermes_logging._reset_queued_handlers()
    try:
        hermes_logging.setup_logging(hermes_home=home, log_level="INFO", force=True)
        record("websocket_open_failed", error=ConnectionRefusedError(61, "private-peer-response"))
        hermes_logging._stop_queue_listener()  # drain queued records without timing assumptions
        evidence = snapshot(tmp_path)
        assert evidence["events"][-1]["phase"] == "websocket_open_failed"
        assert evidence["events"][-1]["errno"] == 61
        assert "private-peer-response" not in (home / "logs" / "agent.log").read_text()
    finally:
        hermes_logging._reset_queued_handlers()

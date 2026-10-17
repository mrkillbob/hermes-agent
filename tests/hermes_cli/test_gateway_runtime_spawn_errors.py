"""Keep native gateway launch failures diagnosable without exposing child secrets."""
from __future__ import annotations

import errno
import json
import subprocess
import time

import pytest


@pytest.mark.parametrize("code", [errno.EACCES, errno.ENOENT])
def test_spawn_failure_records_numeric_cause_without_private_exception(tmp_path, monkeypatch, code):
    from hermes_cli import gateway_runtime_start as start
    from hermes_cli.gateway_runtime_service import RuntimeStartError

    failure = OSError(code, "private-secret-value", "/private/profile-path")
    failure.winerror = 5 if code == errno.EACCES else 2
    calls = []

    def refuse(*args, **kwargs):
        calls.append(kwargs)
        raise failure

    monkeypatch.setattr(start.subprocess, "Popen", refuse)
    with pytest.raises(RuntimeStartError):
        start.spawn_unmanaged_gateway(tmp_path, deadline=time.monotonic() + 10)

    text = (tmp_path / "logs" / "gateway-stdio.log").read_text()
    report = json.loads(text.splitlines()[-1])
    assert report == {"event": "gateway_spawn_failed", "errno": code, "winerror": failure.winerror}
    assert "private-secret-value" not in text and "/private/profile-path" not in text
    assert len(calls) == 1


@pytest.mark.platforms("windows")
def test_native_gateway_spawn_keeps_breakaway_for_an_inert_child(tmp_path, monkeypatch):
    from hermes_cli import gateway_runtime_start as start
    from hermes_cli._subprocess_compat import windows_detach_flags
    from hermes_cli.gateway_runtime_service import RuntimeStartError

    original = subprocess.Popen
    flags = []
    witness = tmp_path / "native-child.json"
    program = "import json,os,pathlib; pathlib.Path(os.environ['HERMES_HOME'],'native-child.json').write_text(json.dumps({'breakaway':os.environ.get('_HERMES_GATEWAY_BREAKAWAY')}))"

    def inert(argv, **kwargs):
        flags.append(kwargs["creationflags"])
        return original([argv[0], "-c", program], **kwargs)

    monkeypatch.setattr(start.subprocess, "Popen", inert)
    try:
        child = start.spawn_unmanaged_gateway(tmp_path, deadline=time.monotonic() + 10)
    except RuntimeStartError:
        diagnostic = (tmp_path / "logs" / "gateway-stdio.log").read_text()
        pytest.fail(f"Native inert gateway launch refused: {diagnostic}")
    try:
        assert child.wait(timeout=10) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
    assert flags == [windows_detach_flags()]
    assert json.loads(witness.read_text()) == {"breakaway": "1"}

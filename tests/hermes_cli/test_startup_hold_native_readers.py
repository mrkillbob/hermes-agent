"""Native file APIs must honor holds without platform-specific flag assumptions."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.platforms('any')


@pytest.mark.parametrize('raw,held', [(None, False), (b'{"action":"drain"}\r\n', False),
    (b'{}', True), (b'{"startup_hold":{}}\r\n', True), (b'{broken', True),
    (b'{"action":"drain"}\x1a', True)])
def test_cold_native_boot_reads_absent_valid_and_malformed_markers(tmp_path, raw, held):
    marker = tmp_path / '.drain_request.json'
    if raw is not None:
        marker.write_bytes(raw)
    code = ('import sys; sys.argv=["hermes","gateway","status"]; import hermes_bootstrap; '
            'from hermes_cli.startup_hold import active; print(int(active()))')
    from pm.lifetime_launch import popen
    with popen([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[2],
               env={**os.environ, 'HERMES_HOME': str(tmp_path), 'HERMES_DISABLE_LAZY_INSTALLS': '1'},
               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        out, err = child.communicate(timeout=30)
    assert child.returncode == 0, (out, err)
    assert out.strip() == str(int(held)), (out, err)
    assert (marker.read_bytes() if marker.exists() else None) == raw


def test_native_marker_restoration_preserves_crlf_exactly(tmp_path, monkeypatch):
    from gateway import drain_startup
    from hermes_cli import startup_hold
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(drain_startup, '_checkout_root', lambda: tmp_path)
    monkeypatch.setattr(drain_startup, '_refuse_legacy_writers', lambda *args: None)
    (tmp_path / 'ESTOP').touch()
    marker = tmp_path / '.drain_request.json'
    prior = b'{\r\n  "action": "drain",\r\n  "principal": "native-fixture"\r\n}\r\n'
    marker.write_bytes(prior)
    assert startup_hold.read_marker_bytes() == prior
    value = drain_startup.begin()
    drain_startup.restore(hold_id=value['hold_id'])
    assert marker.read_bytes() == prior

"""Synthetic-client probes survive thread shutdown without changing child outcomes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig

import pytest

from tests.waiting import wait_until


def test_probe_captures_a_real_thread_shutdown_wait_without_locals(tmp_path):
    from tests.e2e.core.providers._catalog_process_probe import read_probe

    sink = tmp_path / "threads.txt"
    child = tmp_path / "blocked.py"
    child.write_text(
        "import threading, sys\n"
        "from tests.e2e.core.providers._catalog_process_probe import arm_probe\n"
        f"arm_probe({str(sink)!r}, 2.0)\n"
        "private_token = 'fixture-secret-must-not-leave-child'\n"
        "threading.Thread(target=lambda: sys.stdin.buffer.read(1)).start()\n"
        "print('returning', flush=True)\n"
        "raise SystemExit(7)\n"
    )
    # The fixture calls the same bootstrap used by sitecustomize; only this owned
    # child is blocked, and EOF releases it without signals or process inspection.
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    output = tmp_path / "stdout.txt"
    with output.open("w") as stdout, subprocess.Popen(
            [sys.executable, str(child)], env=env, stdin=subprocess.PIPE,
            stdout=stdout, stderr=subprocess.STDOUT, text=True) as proc:
        try:
            assert wait_until(lambda: "returning" in output.read_text(), timeout=10)
            assert wait_until(lambda: sink.exists() and "threading.py" in sink.read_text(), timeout=10)
            evidence = read_probe(sink, proc.pid)
            frames = [frame for thread in evidence["threads"] for frame in thread["frames"]]
            assert any(frame["function"] == "_shutdown" for frame in frames)
            assert proc.poll() is None
            assert evidence["delay_s"] == 2 and evidence["probe_monotonic_s"] > 0
            assert "fixture-secret" not in json.dumps(evidence)
            assert str(tmp_path) not in json.dumps(evidence)
            assert read_probe(sink, proc.pid + 1) == {"status": "identity_mismatch"}
        finally:
            proc.stdin.close()
            proc.wait(timeout=10)
        assert proc.returncode == 7


def test_probe_shim_does_not_arm_in_an_unrelated_child(tmp_path):
    from tests.e2e.core.providers._catalog_process_probe import client_probe

    env = {**os.environ, "HERMES_HOME": str(tmp_path / ".hermes")}
    with client_probe(tmp_path, env, 75, ("-z", "fixture")) as (child_env, sink):
        proc = subprocess.run([sys.executable, "-c", "print('unchanged')"], env=child_env,
                              capture_output=True, text=True, timeout=10, check=False)
        assert proc.returncode == 0 and proc.stdout == "unchanged\n" and not proc.stderr
        assert not sink.exists()
    assert not sink.parent.exists()


def test_probe_parser_bounds_and_rejects_unowned_or_payload_data(tmp_path):
    from tests.e2e.core.providers._catalog_process_probe import read_probe

    sink = tmp_path / "threads.txt"
    sink.write_text(json.dumps({"pid": 123, "delay_s": 70, "probe_monotonic_s": 2.5}) + "\n" +
                    ('Thread 0xabc (most recent call first):\n'
                     '  File "/private/fixture-secret/custom.py", line 7 in secret-function\n'
                     f'  File "{sysconfig.get_path("stdlib")}/threading.py", line 9 in _shutdown\n' * 1000))
    evidence = read_probe(sink, 123)
    assert 0 < len(evidence["threads"]) <= 16
    assert all(len(thread["frames"]) <= 12 for thread in evidence["threads"])
    assert "fixture-secret" not in json.dumps(evidence)
    assert "secret-function" not in json.dumps(evidence)
    assert any(frame["file"] == "stdlib/threading.py"
               for thread in evidence["threads"] for frame in thread["frames"])
    sink.write_text(json.dumps({"pid": 123}) + '\nThread 0xabc (most recent call first):\n' +
                    f'  File "{sysconfig.get_path("stdlib")}/threading.py", line 9 in _shutdown\n' * 13)
    bounded = read_probe(sink, 123)
    assert len(bounded["threads"][0]["frames"]) == 12 and bounded["truncated"]


def test_probe_rejects_spoofed_runtime_roots_and_function_payload(tmp_path):
    from tests.e2e.core.providers._catalog_process_probe import read_probe

    sink = tmp_path / "threads.txt"
    stdlib = Path(sysconfig.get_path("stdlib"))
    sink.write_text(json.dumps({"pid": 123, "delay_s": 70, "probe_monotonic_s": 2.5}) + "\n"
                    'Thread 0xabc (most recent call first):\n'
                    '  File "/private/fixture_secret/lib/python3.14/sk_secret.py", line 7 in fixture_secret\n'
                    f'  File "{stdlib}/threading.py", line 9 in fixture_secret\n'
                    f'  File "{stdlib}/fixture_secret.py", line 10 in fixture_secret\n'
                    f'  File "{stdlib}/../fixture_secret.py", line 11 in fixture_secret\n')
    evidence = read_probe(sink, 123)
    assert "secret" not in json.dumps(evidence)
    assert evidence["threads"][0]["frames"][0]["file"] == "stdlib/threading.py"
    assert evidence["threads"][0]["frames"][1]["file"].startswith("stdlib/sha256:")


@pytest.mark.parametrize("argv,matching_home,eligible", [
    (["python", "-m", "hermes_cli.main", "-z", "fixture"], True, True),
    (["python", "-m", "hermes_cli.main", "-z", "fixture"], False, False),
    (["python", "-m", "hermes_cli.main", "gateway", "run"], True, False),
    (["python", "-I", "-c", "fixture"], True, False),
    (["python", "-m", "hermes_cli.main", "-z", "fixture", "extra"], True, False),
    (["python", "-m", "hermes_cli.main", "-z", "different"], True, False),
])
def test_probe_eligibility_is_exact_and_isolated_reexec_is_unavailable(monkeypatch, tmp_path, argv,
                                                                    matching_home, eligible):
    from tests.e2e.core.providers import _catalog_process_probe as probe

    calls = []
    monkeypatch.setattr(sys, "orig_argv", argv)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / (".hermes" if matching_home else "other")))
    monkeypatch.setattr(probe, "arm_probe", lambda sink, delay: calls.append((sink, delay)))
    expected = ["python", "-m", "hermes_cli.main", "-z", "fixture"]
    probe.arm_if_client(str(tmp_path), "owned-sink", 0, expected, os.getppid())
    assert bool(calls) is eligible
    if eligible:
        assert calls == [("owned-sink", 0.1)]
        calls.clear()
        probe.arm_if_client(str(tmp_path), "owned-sink", 0, expected, os.getppid() + 1)
        assert not calls

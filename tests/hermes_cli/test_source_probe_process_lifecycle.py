"""Offline regressions for Git helper survival at probe timeout and CLI exit."""
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import psutil
import pytest

from tests.waiting import wait_until

pytestmark = [pytest.mark.platforms("posix"), pytest.mark.live_system_guard_bypass]
# The RED case deliberately reparents fixture-owned children; teardown signals
# only the exact PIDs written by our shim, after checking their process identity.


def _shim(tmp_path, *, detached=False):
    marker = tmp_path / "processes.json"
    script = tmp_path / "git_shim.py"
    script.write_text(textwrap.dedent(f"""\
        import json, os, subprocess, sys, threading
        from pathlib import Path
        child = subprocess.Popen([sys.executable, '-c', 'import threading; threading.Event().wait(300)'],
                                 start_new_session={detached!r})
        import psutil
        Path({str(marker)!r}).write_text(json.dumps([[pid, psutil.Process(pid).create_time()]
                                                    for pid in (os.getpid(), child.pid)]))
        threading.Event().wait(300)
        """))
    return script, marker


def _live(identity):
    pid, started = identity
    try:
        process = psutil.Process(pid)
        return (process.create_time() == started and process.is_running()
                and process.status() != psutil.STATUS_ZOMBIE)
    except psutil.NoSuchProcess:
        return False


def _reap_fixture(marker):
    if marker.exists():
        for identity in reversed(json.loads(marker.read_text())):
            if _live(identity):
                psutil.Process(identity[0]).kill()


@pytest.mark.parametrize("detached", [False, True])
def test_source_probe_timeout_reaps_pipe_holding_descendant(tmp_path, detached):
    from hermes_cli.source_check import _git_run

    script, marker = _shim(tmp_path, detached=detached)
    try:
        assert _git_run([str(script)], git=sys.executable, cwd=tmp_path, timeout=2) is None
        pids = json.loads(marker.read_text())
        wait_until(lambda: not any(_live(pid) for pid in pids), timeout=3,
                   what="source probe and pipe-holding helper to stop")
    finally:
        _reap_fixture(marker)


@pytest.mark.parametrize("consumer", ["source-check", "source-channel"])
def test_successful_source_probe_reaps_helpers_with_closed_pipes(tmp_path, consumer):
    from hermes_cli.source_check import _git_run
    from hermes_cli.source_releases import _run_git

    marker = tmp_path / "completed-parent.json"
    script = tmp_path / "completed_parent.py"
    script.write_text(textwrap.dedent(f"""\
        import json, os, subprocess, sys
        from pathlib import Path
        import psutil
        child = subprocess.Popen([sys.executable, '-c', 'import threading; threading.Event().wait(300)'],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        Path({str(marker)!r}).write_text(json.dumps([[pid, psutil.Process(pid).create_time()]
                                                    for pid in (os.getpid(), child.pid)]))
        print('source-probe-ok')
        """))
    try:
        result = (_git_run([str(script)], git=sys.executable, cwd=tmp_path)
                  if consumer == "source-check" else _run_git([sys.executable, str(script)], cwd=tmp_path))
        assert result.returncode == 0
        assert result.stdout == 'source-probe-ok\n'
        assert result.stderr == ''
        pids = json.loads(marker.read_text())
        # No owner shutdown is necessary: a completed optional probe owes no
        # background processes, even when they no longer hold captured pipes.
        wait_until(lambda: not any(_live(pid) for pid in pids), timeout=3,
                   what="source helper to stop when its successful parent completes")
    finally:
        _reap_fixture(marker)


@pytest.mark.parametrize("channel", ["main", "stable", "preview-fixture"])
def test_cli_shutdown_reaps_banner_probe_before_its_deadline(tmp_path, channel):
    script, marker = _shim(tmp_path)
    driver = tmp_path / "banner_driver.py"
    driver.write_text(textwrap.dedent(f"""\
        import sys
        from types import SimpleNamespace
        from pathlib import Path
        from hermes_cli import banner, source_check, source_releases
        from tests.waiting import wait_until
        import cli
        banner._skip_background_prefetch = lambda: False
        # Replace only remote metadata with fixture records. Release selection,
        # retirement checks, Git spawning and CLI teardown remain real.
        source_releases._json = lambda url: (
            {{'sha': 'a' * 40}} if '/commits/' in url else
            {{'tag_name': 'v1.2.3', 'draft': False, 'prerelease': False}})
        source_releases._resolve_channel = lambda *args: SimpleNamespace(
            requested={{'state': 'retired'}},
            terminal={{'name': 'stable', 'repository': 'fixture/fork', 'policy': 'preview',
                       'head': {{'sequence': 1}}}},
            manifest={{'request': {{'commit': 'a' * 40, 'sourceVersion': '1.2.3',
                                   'buildId': 'fixture', 'sequence': 1}}}})
        def check(**kwargs):
            if {channel!r} == 'main':
                source_check._git_run([{str(script)!r}], git=sys.executable, timeout=300)
            else:
                source_releases.resolve_source_target({channel!r},
                    [sys.executable, {str(script)!r}], {str(tmp_path)!r}, repository='fixture/fork')
            return {{'behind': None}}
        source_check.check_for_updates = check
        banner.prefetch_update_check()
        wait_until(lambda: Path({str(marker)!r}).exists(), what='probe helper to start')
        cli._run_cleanup(notify_session_finalize=False)
        from hermes_cli.probe_lifecycle import bind_probe_scope
        # A daemon returning from HTTP after shutdown must not start another Git helper.
        with bind_probe_scope(banner._prefetch_probes):
            assert source_check._git_run(['-c', "print('late')"], git=sys.executable) is None
        """))
    env = dict(os.environ, HOME=str(tmp_path), HERMES_HOME=str(tmp_path / "hermes"),
               PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    try:
        result = subprocess.run([sys.executable, str(driver)], env=env, capture_output=True,
                                text=True, timeout=30, check=False)
        assert result.returncode == 0, result.stderr
        pids = json.loads(marker.read_text())
        wait_until(lambda: not any(_live(pid) for pid in pids), timeout=3,
                   what="banner probe and helper to stop after CLI cleanup")
    finally:
        _reap_fixture(marker)


def test_closed_scope_refuses_later_source_channel_probes(tmp_path):
    from hermes_cli.probe_lifecycle import ProbeScope, bind_probe_scope
    from hermes_cli.source_releases import source_repository

    scope = ProbeScope()
    scope.close()
    with bind_probe_scope(scope), pytest.raises(subprocess.TimeoutExpired):
        source_repository([sys.executable, '-c', "print('https://github.com/fixture/fork.git')"],
                          cwd=tmp_path)


@pytest.mark.parametrize("text", [True, False])
def test_source_probe_preserves_cwd_environment_and_output_mode(tmp_path, monkeypatch, text):
    from hermes_cli.source_check import _git_run

    monkeypatch.setenv("GH_TOKEN", "fixture-probe-auth")
    command = "import os,sys; print(os.getcwd()); print(os.environ['GH_TOKEN']); sys.stdout.buffer.write(b'\\xff'); sys.exit(3)"
    result = _git_run(['-c', command], git=sys.executable, cwd=tmp_path, text=text)
    assert result.returncode == 3
    expected = str(tmp_path).encode() + b'\nfixture-probe-auth\n'
    assert (expected.decode() in result.stdout if text else expected in result.stdout)
    assert ('\ufffd' in result.stdout if text else b'\xff' in result.stdout)

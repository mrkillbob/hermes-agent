"""Who may adopt a pre-written claim (real processes): an OLD packaged Desktop's
``cmd.exe`` wrapper v1 overwrite is accepted by lineage only (R4), and with
``-HandoffRun`` only the Desktop bridge for that run is adopted (protocol 2).
"""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from tests.scripts.desktop_update.windows_handoff_support import SCRIPT, MARKER, POWERSHELL, _wait_for_log


@pytest.fixture
def sleeper():
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])
    yield proc
    proc.kill()
    proc.wait()


# -- R4: an old packaged Desktop + this script ---------------------------------

OLD_DESKTOP = r"""
import os, subprocess, sys, time
from pathlib import Path
# be3fd671d70 checkout.ts: spawn the cmd.exe wrapper (non-detached), then in the
# same tick overwrite the marker with the WRAPPER's pid as a v1 claim, then
# stay up (the -SelfTestMarker script ends before it would wait us out).
home, script, mode, foreign = Path(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4])
other_desktop, env_skew = int(sys.argv[5]), int(sys.argv[6])  # a Desktop that is NOT the wrapper's parent
started = int(time.time())
env = dict(os.environ, HERMES_UPDATE_STARTED_AT=str(started + env_skew))
ps = [os.environ['POWERSHELL'], '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script,
      '-InstallRoot', str(home / 'hermes-agent'), '-NoUi', '-SelfTestMarker', '-NoMarkerCleanup',
      '-DesktopPid', str(other_desktop or os.getpid())]
wrapper = ['cmd.exe', '/d', '/s', '/c'] + (['start', '', '/b'] if mode == 'start-b' else [])
child = subprocess.Popen(wrapper + ps, cwd=home, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
owner = foreign or child.pid
(home / '.hermes-update-in-progress').write_bytes(f'{owner}\n{started}\n'.encode())
(home / 'desktop.json').write_text(f'{os.getpid()} {child.pid} {started}', encoding='utf-8')
time.sleep(600)
"""


def _old_desktop(home: Path, mode: str, foreign: int = 0, other_desktop: int = 0,
                 env_skew: int = 0) -> tuple[subprocess.Popen, int, int, int]:
    program = home.parent / 'old_desktop.py'
    program.write_text(OLD_DESKTOP, encoding='utf-8')
    desktop = subprocess.Popen([sys.executable, str(program), str(home), str(SCRIPT), mode, str(foreign),
                                str(other_desktop), str(env_skew)],
                               env={**os.environ, 'HERMES_HOME': str(home), 'POWERSHELL': POWERSHELL})
    deadline = time.monotonic() + 30
    while not (home / 'desktop.json').exists():
        assert time.monotonic() < deadline and desktop.poll() is None, 'old desktop never spawned'
        time.sleep(0.05)
    time.sleep(0.2)
    desktop_pid, wrapper, started = map(int, (home / 'desktop.json').read_text(encoding='utf-8-sig').split())
    return desktop, wrapper, started, desktop_pid


@pytest.mark.platforms('windows')
@pytest.mark.parametrize('mode', ['start-b', 'waiting-wrapper'])
def test_r4_old_desktop_wrapper_claim_is_adopted_by_lineage(tmp_path: Path, mode: str) -> None:
    """`start /b` wrapper (exits at once: lineage via the startedAt it was given) and a wrapper
    still alive (lineage via its parent == the Desktop) both hand the claim to the script."""
    home = tmp_path / 'home'; home.mkdir()
    desktop, wrapper, started, desktop_pid = _old_desktop(home, mode)
    try:
        log = _wait_for_log(home, 'hand-off start:')
    finally:
        desktop.kill(); desktop.wait()
    assert 'marker=adopted' in log, log
    script_pid = log.split('hand-off start:')[1].split(' pid=')[1].split()[0]
    lines = (home / MARKER).read_bytes().decode().split('\n')
    assert lines[:2] == [script_pid, str(started)], lines
    assert lines[2].startswith('ct:'), lines
    assert f"desktop pid {desktop_pid}'s launcher pid {wrapper}" in log, log


@pytest.mark.platforms('windows')
def test_r4_old_desktop_lineage_never_adopts_an_unrelated_live_claim(
    tmp_path: Path, sleeper: subprocess.Popen,
) -> None:
    home = tmp_path / 'home'; home.mkdir()
    desktop, _, started, _ = _old_desktop(home, 'start-b', foreign=sleeper.pid)
    try:
        log = _wait_for_log(home, 'exiting without claiming')
    finally:
        desktop.kill(); desktop.wait()
    assert 'hand-off start:' not in log, log
    time.sleep(1)
    assert (home / MARKER).read_bytes().decode() == f'{sleeper.pid}\n{started}\n'


@pytest.mark.platforms('windows')
@pytest.mark.parametrize('env_matches', [True, False], ids=['env-matches', 'env-differs'])
def test_r4_live_wrapper_not_the_desktops_child_is_adopted_only_with_the_handoff_started_at(
    tmp_path: Path, sleeper: subprocess.Popen, env_matches: bool,
) -> None:
    """The lineage table's divergent row (round 5 D11) on real processes: the cmd.exe wrapper is
    our live parent but not the Desktop's child; line 2 == HERMES_UPDATE_STARTED_AT adopts, as
    in bash. Any other startedAt refuses."""
    home = tmp_path / 'home'; home.mkdir()
    desktop, wrapper, started, _ = _old_desktop(home, 'waiting-wrapper', other_desktop=sleeper.pid,
                                                env_skew=0 if env_matches else -7)
    try:
        log = _wait_for_log(home, 'hand-off start:' if env_matches else 'exiting without claiming')
    finally:
        desktop.kill(); desktop.wait()
    if env_matches:
        assert 'marker=adopted' in log, log
        assert f"desktop pid {sleeper.pid}'s launcher pid {wrapper}" in log, log
        lines = (home / MARKER).read_bytes().decode().split('\n')
        assert lines[1] == str(started) and lines[2].startswith('ct:'), lines
    else:
        assert 'hand-off start:' not in log, log
        assert (home / MARKER).read_bytes().decode() == f'{wrapper}\n{started}\n'


# -- the one launcher-lineage rule, shared with marker.sh ---------------------

_RULE_HARNESS = r"""
param([string]$MarkerPs1, [string]$Marker, [string]$Cases)
$MarkerPath = $Marker
$NoMarkerCleanup = $true
function Write-HandoffLog([string]$Message) { [Console]::Error.WriteLine($Message) }
. $MarkerPs1
$data = [System.IO.File]::ReadAllText($Cases) | ConvertFrom-Json
foreach ($c in $data.rule) {
    $facts = @{}
    foreach ($p in $c.facts.PSObject.Properties) { $facts[$p.Name] = [bool]$p.Value }
    [Console]::Out.WriteLine("$($c.id)=$([int][bool](Test-MarkerLauncherRule $facts))")
}
foreach ($c in $data.env) {
    $env:HERMES_UPDATE_STARTED_AT = $c.env
    [Console]::Out.WriteLine("env_$($c.id)=$([int][bool](Test-MarkerEnvStartedAt $c.line2))")
}
"""

_PASCAL = {'v1': 'V1', 'names_desktop': 'NamesDesktop', 'named_alive': 'NamedAlive',
           'named_is_our_parent': 'NamedIsOurParent', 'named_parent_is_desktop': 'NamedParentIsDesktop',
           'env_started_matches': 'EnvStartedMatches'}


# -- protocol 2: -HandoffRun --------------------------------------------------

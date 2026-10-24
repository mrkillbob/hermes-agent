"""Native ownership regressions for historical Windows updates (#372)."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from pm import lifetime
from pm.filesystem import long_root

pytestmark = pytest.mark.platforms("windows")


@pytest.mark.parametrize("verbatim_creator", [False, True])
def test_native_and_verbatim_paths_share_the_same_lifetime(tmp_path, verbatim_creator):
    domain = tmp_path / "installs" / "fixture"
    creator, reader = (long_root(domain), domain) if verbatim_creator else (domain, long_root(domain))
    with lifetime.activity(creator, create=True):
        pass
    _read_activity(reader)


def _read_activity(reader):
    try:
        owned = lifetime.acquire_activity(reader)
    except lifetime.LifetimeUnavailable as exc:
        with lifetime.gate(reader) as guard:
            pytest.fail(f"{exc}: recorded={guard._record()!r}, observed={guard._snapshot()!r}")
    try:
        with lifetime.gate(reader) as guard:
            assert guard.qualified
            assert guard.held()
    finally:
        owned.close()
    with lifetime.gate(reader) as guard:
        assert not guard.held()


def _legacy_python():
    for command in ([shutil.which("py"), "-3.11"], [shutil.which("python3.11")]):
        if command[0] is None:
            continue
        result = subprocess.run([*command, "-I", "-S", "-B", "-c",
                                 "import sys; print(sys.version_info[:2])"],
                                capture_output=True, text=True, timeout=15, check=False)
        if result.returncode == 0 and result.stdout.strip() == "(3, 11)":
            return command
    pytest.skip("Python 3.11 is required for the historical Windows handoff regression")


def test_python_311_qualification_accepts_the_current_worker(tmp_path):
    assert sys.version_info[:2] == (3, 14), "the current Windows worker must run Python 3.14"
    legacy = _legacy_python()
    repo = Path(__file__).resolve().parents[2]
    domain = tmp_path / "installs" / "fixture"
    code = "\n".join((
        "import json, sys",
        "from pathlib import Path",
        f"sys.path.insert(0, {str(repo)!r})",
        "from pm.lifetime import activity, gate",
        f"domain = Path({str(domain)!r})",
        "with activity(domain, create=True): pass",
        "with gate(domain) as guard: print(json.dumps(guard._snapshot()))",
    ))
    result = subprocess.run([*legacy, "-I", "-S", "-B", "-c", code],
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    print(f"Python 3.11 creator: {json.loads(result.stdout)!r}; worker: {sys.version}")
    _read_activity(domain)

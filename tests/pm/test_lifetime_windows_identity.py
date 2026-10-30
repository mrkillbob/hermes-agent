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
    generation = domain / "environments" / "generation"
    generation.mkdir(parents=True)
    with lifetime.activity(reader, generation=generation):
        with lifetime.gate(creator) as guard:
            assert guard.held(generation=long_root(generation))


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


def _create_with_legacy_python(domain, *, legacy_record=False):
    legacy = _legacy_python()
    repo = Path(__file__).resolve().parents[2]
    code = "\n".join((
        "import json, os, sys",
        "from pathlib import Path",
        f"sys.path.insert(0, {str(repo)!r})",
        "from pm.lifetime import activity, gate",
        f"domain = Path({str(domain)!r})",
        "with activity(domain, create=True): pass",
        "with gate(domain) as guard: print(json.dumps(guard._snapshot()))",
    ))
    if legacy_record:
        code += "\n" + "\n".join((
            "with gate(domain) as guard:",
            "    record = guard._snapshot()",
            "    for key, path in [('domain', domain), ('activities', guard.activities)]:",
            "        info = path.lstat()",
            "        record[key] = [info.st_dev, info.st_ino]",
            "    info = os.fstat(guard.fd)",
            "    record['gate'] = [info.st_dev, info.st_ino]",
            "    guard.marker.write_text(json.dumps(record), encoding='utf-8')",
            "    stale = {'version': 1, 'platform': 'nt', 'domain': record['domain'], 'generation': None}",
            "    (guard.activities / ('a' * 32)).write_text(json.dumps(stale), encoding='utf-8')",
        ))
    result = subprocess.run([*legacy, "-I", "-S", "-B", "-c", code],
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    print(f"Python 3.11 creator: {json.loads(result.stdout)!r}; worker: {sys.version}")


@pytest.mark.parametrize("legacy_record", [False, True])
def test_python_311_qualification_accepts_the_current_worker(tmp_path, legacy_record):
    assert sys.version_info[:2] == (3, 14), "the current Windows worker must run Python 3.14"
    domain = tmp_path / "installs" / "fixture"
    _create_with_legacy_python(domain, legacy_record=legacy_record)
    _read_activity(domain)


@pytest.mark.parametrize("entry", ["domain", "gate", "activities"])
def test_legacy_marker_cannot_requalify_a_replaced_object(tmp_path, entry):
    domain = tmp_path / "installs" / "fixture"
    _create_with_legacy_python(domain, legacy_record=True)
    guard = lifetime.Gate(domain)
    marker = guard.marker.read_bytes()
    target = {"domain": domain, "gate": guard.namespace / "gate", "activities": guard.activities}[entry]
    replaced = target.with_name(target.name + ".replaced")
    target.rename(replaced)
    target.mkdir() if replaced.is_dir() else target.touch()
    with pytest.raises(lifetime.LifetimeUnavailable, match="identity changed"):
        lifetime.acquire_activity(domain)
    assert guard.marker.read_bytes() == marker


def test_retirement_receipt_accepts_an_equivalent_windows_path(tmp_path):
    domain = tmp_path / "state"
    with lifetime.activity(domain, create=True):
        pass
    with lifetime.gate(domain) as guard:
        ownership = guard._snapshot()
        domain.rmdir()
        guard.confirm_removed(ownership)
    with lifetime.activity(long_root(domain), create=True):
        with lifetime.gate(domain) as guard:
            assert guard.qualified


def test_legacy_upgrade_cannot_certify_a_later_domain_replacement(tmp_path, monkeypatch):
    domain = tmp_path / "installs" / "fixture"
    _create_with_legacy_python(domain, legacy_record=True)
    inspect = lifetime.Gate._verified_snapshot

    def replace_after_verification(guard, record, **kwargs):
        verified = inspect(guard, record, **kwargs)
        assert verified is not None
        domain.rename(domain.with_name("original"))
        domain.mkdir()
        return verified

    monkeypatch.setattr(lifetime.Gate, "_verified_snapshot", replace_after_verification)
    with pytest.raises(lifetime.LifetimeUnavailable, match="identity changed"):
        lifetime.acquire_activity(domain)
    with lifetime.gate(domain) as guard:
        assert not guard.qualified

"""Bound disk retained by successful upgrade fixtures without running an installer.

Tiny allocations use the real fixture providers and pytest phase/report lifecycle.
Failed roots remain available inside the existing per-file runner attempt.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import textwrap
from pathlib import Path

import pytest

pytest_plugins = ("pytester",)
pytestmark = pytest.mark.platforms("posix")

_PAYLOAD_BYTES = 64 * 1024


def _probe(pytester, monkeypatch, provider: str, phase: str):
    repo = Path(__file__).resolve().parents[2]
    source = repo / "tests" / "e2e" / "core" / "upgrade" / "git"
    # Stage the same parent fixture hooks pytest would discover. Execute the
    # copied module; never inspect its source text or assert its shape.
    if (source / "conftest.py").is_file():
        shutil.copyfile(source / "conftest.py", pytester.path / "conftest.py")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(pytester.path), str(repo))))
    monkeypatch.delenv("HERMES_E2E_KEEP_ROOT", raising=False)
    if phase == "keep":
        monkeypatch.setenv("HERMES_E2E_KEEP_ROOT", "1")

    external = pytester.path / "external"
    external.mkdir()
    external_before = external.stat()
    sentinel = external / "shared-wheel"
    sentinel.write_bytes(b"external shared inode")
    sentinel.chmod(0o444)
    before = sentinel.stat()
    sibling = pytester.path / "unrelated"
    sibling.mkdir()
    (sibling / "witness").write_text("retain", encoding="utf-8")
    manifest = pytester.path / "allocated.json"

    support = textwrap.dedent(
        f"""
        import json
        import os
        from pathlib import Path
        from types import SimpleNamespace

        import pytest

        from tests.e2e.core.upgrade.git import test_hostile_lock as lock
        from tests.e2e.core.upgrade.git import test_hostile_commit as commit

        PHASE = {phase!r}
        PROVIDER = {provider!r}
        MANIFEST = Path({str(manifest)!r})
        SENTINEL = Path({str(sentinel)!r})
        EXTERNAL = Path({str(external)!r})

        rig = lock.rig
        world = commit.world

        def allocate(root):
            roots = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
            roots.append(str(root))
            MANIFEST.write_text(json.dumps(roots))
            (root / "payload.bin").write_bytes(b"x" * {_PAYLOAD_BYTES})
            locked = root / "read-only"
            locked.mkdir()
            (locked / "entry").write_bytes(b"retire owned data")
            locked.chmod(0o500)
            os.link(SENTINEL, root / "shared-wheel")
            (root / "external-link").symlink_to(EXTERNAL, target_is_directory=True)
            if PHASE == "setup":
                raise RuntimeError("synthetic setup refusal")

        class TinyRig:
            def __init__(self, root):
                self.root = root
                allocate(root)

        def tiny_install(root):
            allocate(root)
            sb_root = root / "sb"
            (sb_root / "wrap").mkdir(parents=True)
            (sb_root / "wrap" / "git").write_text("inert shim")
            return SimpleNamespace(root=sb_root), root / "origin.git"

        @pytest.fixture(scope="session")
        def provider():
            return SimpleNamespace(base_url="unused")

        @pytest.fixture(autouse=True)
        def inert_install_ports(monkeypatch):
            monkeypatch.setattr(lock, "Rig", TinyRig)
            monkeypatch.setattr(commit.P, "install_head", tiny_install)
            monkeypatch.setattr(commit.P, "configure", lambda *args: None)
            yield
            if PHASE == "teardown":
                raise RuntimeError("synthetic teardown refusal")
        """
    )
    probe = textwrap.dedent(
        f"""
        import json
        import os
        from pathlib import Path

        import pytest
        from probe_support import MANIFEST, PHASE

        @pytest.mark.parametrize("case", range({3 if phase == "passed" else 1}))
        def test_case(case, {provider}):
            value = {provider}
            root = Path(value["root"] if isinstance(value, dict) else value.root)
            if PHASE == "call":
                pytest.fail("synthetic call refusal")
            if PHASE == "crash":
                os._exit(17)
            if PHASE == "passed":
                roots = [Path(p) for p in json.loads(MANIFEST.read_text())]
                active = [p for p in roots if p.exists()]
                retained = sum((p / "payload.bin").stat().st_size for p in active)
                assert retained == {_PAYLOAD_BYTES}, (
                    "completed fixture payload accumulated: " + str(retained)
                )
                assert active == [root], "a prior successful fixture root remains"
        """
    )
    pytester.makepyfile(probe_support=support, test_probe=probe)
    result = pytester.runpytest_subprocess(
        "-p", "probe_support", "--rootdir", str(pytester.path),
        "--confcutdir", str(pytester.path), "-q", "--tb=short",
        "test_probe.py", timeout=20,
    )
    roots = [Path(p) for p in json.loads(manifest.read_text(encoding="utf-8"))]
    after = sentinel.stat()
    external_after = external.stat()
    assert (external_after.st_dev, external_after.st_ino, stat.S_IMODE(external_after.st_mode)) == (
        external_before.st_dev, external_before.st_ino, stat.S_IMODE(external_before.st_mode)
    )
    assert sentinel.read_bytes() == b"external shared inode"
    assert (after.st_dev, after.st_ino, stat.S_IMODE(after.st_mode)) == (
        before.st_dev, before.st_ino, stat.S_IMODE(before.st_mode)
    )
    assert (sibling / "witness").read_text(encoding="utf-8") == "retain"
    return result, roots


@pytest.mark.parametrize("provider", ["rig", "world"])
def test_completed_upgrade_fixture_payload_does_not_accumulate(pytester, monkeypatch, provider):
    result, roots = _probe(pytester, monkeypatch, provider, "passed")
    result.assert_outcomes(passed=3)
    assert len(roots) == 3
    assert not any(root.exists() for root in roots)


@pytest.mark.parametrize("provider", ["rig", "world"])
@pytest.mark.parametrize("phase", ["setup", "call", "teardown", "keep", "crash"])
def test_unsuccessful_or_explicitly_kept_fixture_evidence_remains(
    pytester, monkeypatch, provider, phase
):
    result, roots = _probe(pytester, monkeypatch, provider, phase)
    if phase == "setup":
        result.assert_outcomes(errors=1)
    elif phase == "call":
        result.assert_outcomes(failed=1)
    elif phase == "teardown":
        result.assert_outcomes(passed=1, errors=1)
    elif phase == "keep":
        result.assert_outcomes(passed=1)
    else:
        assert result.ret == 17
    if phase in ("setup", "call", "teardown"):
        result.stdout.fnmatch_lines([f"*synthetic {phase} refusal*"])
    assert len(roots) == 1
    assert (roots[0] / "payload.bin").read_bytes() == b"x" * _PAYLOAD_BYTES

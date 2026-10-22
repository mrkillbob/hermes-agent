from zipfile import ZipFile

import pytest

from scripts.fleet_install import (
    build_runner_bundle,
    liveness_marker_is_active,
    resolve_hermes_executable,
    runner_command,
)


def test_bundle_is_standalone_and_contains_no_checkout_or_secret_files(tmp_path):
    bundle = build_runner_bundle(tmp_path / "fleet-runner.pyz")

    with ZipFile(bundle) as archive:
        names = set(archive.namelist())

    assert "__main__.py" in names
    assert "fleet_runner.py" in names
    assert "hermes_cli/fleet_protocol.py" in names
    assert "hermes_cli/urllib_security.py" in names
    assert not any(name.startswith(".git/") or name.endswith(".env") for name in names)


def test_runner_uses_the_exact_verified_executable_and_bundle_command(tmp_path):
    executable = tmp_path / "hermes.exe"
    executable.write_text("stub", encoding="utf-8")
    bundle = tmp_path / "fleet-runner.pyz"
    bundle.write_bytes(b"bundle")

    assert resolve_hermes_executable(executable) == str(executable)
    assert runner_command("python.exe", bundle, "windows") == ["python.exe", str(bundle), "--node-id", "windows"]


def test_runner_refuses_to_start_without_the_desktop_liveness_marker(tmp_path):
    marker = tmp_path / "fleet-live"

    assert liveness_marker_is_active(marker) is False
    marker.write_text("live", encoding="utf-8")
    assert liveness_marker_is_active(marker) is True

    with pytest.raises(ValueError, match="does not exist"):
        resolve_hermes_executable(tmp_path / "missing-hermes.exe")


def test_bundle_executes_with_no_third_party_site_packages(tmp_path):
    import json
    import subprocess
    import sys
    bundle = build_runner_bundle(tmp_path / 'runner.pyz')
    code = ("import sys,json; sys.path.insert(0,sys.argv[1]); "
            "from hermes_cli.fleet_runner import FleetRunner; "
            "r=FleetRunner._execute([sys.executable,'-c','import sys; print(sys.stdin.read())'],'Public fixture π'); "
            "print(json.dumps({'rc':r.returncode,'text':r.stdout.strip()}))")
    done = subprocess.run([sys.executable, '-I', '-S', '-c', code, str(bundle)],
                          capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15, check=False,
                          env={**__import__('os').environ, 'PYTHONIOENCODING':'utf-8'})
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {'rc':0,'text':'Public fixture π'}


def test_bundle_probe_scope_executes_and_closes_without_site_packages(tmp_path):
    import json
    import subprocess
    import sys

    bundle = build_runner_bundle(tmp_path / 'runner.pyz')
    code = '''import sys,json
sys.path.insert(0,sys.argv[1])
from hermes_cli.probe_lifecycle import ProbeScope, bind_probe_scope
from hermes_cli._subprocess_compat import bounded_probe_run
scope = ProbeScope()
with bind_probe_scope(scope):
    result = bounded_probe_run([sys.executable, '-c',
        "import sys; print(sys.stdin.read()); sys.exit(3)"], timeout=10, input='Public fixture π')
scope.close()
with bind_probe_scope(scope):
    refused = bounded_probe_run([sys.executable, '-c', "raise AssertionError('closed probe ran')"], timeout=10)
print(json.dumps({'rc': result.returncode, 'text': result.stdout.strip(), 'refused': refused is None}))
'''
    done = subprocess.run([sys.executable, '-I', '-S', '-c', code, str(bundle)],
                          cwd=tmp_path, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=30, check=False,
                          env={**__import__('os').environ, 'PYTHONIOENCODING': 'utf-8'})
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {'rc': 3, 'text': 'Public fixture π', 'refused': True}

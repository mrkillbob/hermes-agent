"""The stdlib zipapp resolves target environments through its installed Hermes runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.fleet_install import build_runner_bundle


def _launcher(tmp_path, body):
    helper = tmp_path / 'helper.py'
    helper.write_text(body, encoding='utf-8')
    if os.name == 'nt':
        entry = tmp_path / 'hermes.cmd'
        entry.write_text(f'@"{sys.executable}" "{helper}" %*\r\n', encoding='utf-8')
    else:
        entry = tmp_path / 'hermes'
        entry.write_text(f'#!{sys.executable}\n' + body, encoding='utf-8')
        entry.chmod(0o700)
    return entry


def _bundle_call(tmp_path, launcher, env):
    bundle = build_runner_bundle(tmp_path / 'runner.pyz')
    code = ('import sys,json; sys.path.insert(0,sys.argv[1]); '
            'from hermes_cli.fleet_execution import profile_child_env; '
            'print(json.dumps(profile_child_env("worker",hermes_executable=sys.argv[2])))')
    return subprocess.run([sys.executable, '-I', '-S', '-c', code, str(bundle), str(launcher)],
                          cwd=tmp_path, env=env, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=45, check=False)


@pytest.mark.parametrize('launch_profile', ['default', 'launcher'])
def test_zipapp_resolves_real_target_environment_without_site_packages(tmp_path, launch_profile):
    root = tmp_path / '.hermes'
    target = root / 'profiles' / 'worker'
    launch = root if launch_profile == 'default' else root / 'profiles' / launch_profile
    target.mkdir(parents=True)
    launch.mkdir(parents=True, exist_ok=True)
    (target / 'config.yaml').write_text('model: fixture\n', encoding='utf-8')
    (target / '.env').write_text('TELEGRAM_BOT_TOKEN=target-fixture\n', encoding='utf-8')
    (launch / '.env').write_text('TELEGRAM_BOT_TOKEN=launch-fixture\n', encoding='utf-8')
    (root / 'active_profile').write_text('worker', encoding='utf-8')
    managed = tmp_path / 'managed'
    managed.mkdir()
    (managed / '.env').write_text('ORG_POLICY_FLAG=managed-fixture\n', encoding='utf-8')
    (target / '.env').write_text('TELEGRAM_BOT_TOKEN=target-fixture\nORG_POLICY_FLAG=user-fixture\n', encoding='utf-8')
    repo = str(Path(__file__).resolve().parents[2])
    launcher = _launcher(tmp_path, f'''import sys
sys.path.insert(0, {repo!r})
assert sys.argv[1:3] == ['-p', {launch_profile!r}]
from hermes_cli.subcommands.fleet import build_parser, cmd_fleet
import argparse
parser=argparse.ArgumentParser()
build_parser(parser.add_subparsers(dest='command'))
raise SystemExit(cmd_fleet(parser.parse_args(sys.argv[3:])))
''')
    env = {**os.environ, 'HERMES_HOME': str(launch), 'HOME': str(tmp_path),
           'USERPROFILE': str(tmp_path), 'HERMES_MANAGED_DIR': str(managed),
           'TELEGRAM_BOT_TOKEN': 'launch-fixture', 'TERMINAL_CWD': '/launch-only',
           'PYTHONIOENCODING': 'utf-8'}
    done = _bundle_call(tmp_path, launcher, env)
    assert done.returncode == 0, done.stderr
    child = json.loads(done.stdout)
    assert child['HERMES_HOME'] == str(target)
    assert child['TELEGRAM_BOT_TOKEN'] == 'target-fixture'
    assert child['ORG_POLICY_FLAG'] == 'managed-fixture'
    assert child['PYTHONIOENCODING'] == 'utf-8'
    assert 'TERMINAL_CWD' not in child


@pytest.mark.parametrize('body', [
    'print("not JSON: dummy-secret")',
    'print(\'{"version":1,"env":{"HERMES_HOME":"fixture","BAD":3}}\')',
    'import sys; print("dummy-secret"); sys.exit(2)',
])
def test_zipapp_rejects_invalid_helper_without_exposing_output(tmp_path, body):
    launcher = _launcher(tmp_path, body)
    done = _bundle_call(tmp_path, launcher, {**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    assert done.returncode != 0
    assert 'helper unavailable or invalid' in done.stderr
    assert 'dummy-secret' not in done.stderr
    assert not done.stdout

"""Audit coverage must follow exact commit trees and preserve every package result."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

HELPER = Path(__file__).resolve().parents[2] / 'scripts/ci/npm_audit.py'


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def commit(repo):
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=CI test', '-c', 'user.email=ci@example.invalid', 'commit', '-qm', 'fixture')
    return git(repo, 'rev-parse', 'HEAD')


def lock(repo, directory, version):
    target = repo / directory
    target.mkdir(parents=True, exist_ok=True)
    (target / 'package.json').write_text(json.dumps({'name': 'fixture', 'version': version}))
    (target / 'package-lock.json').write_text(json.dumps({'name': 'fixture', 'version': version, 'lockfileVersion': 3, 'packages': {}}))


def invoke(repo, base, head, fake_bin, tmp_path):
    output = tmp_path / 'outputs'
    report = tmp_path / 'report.json'
    result = subprocess.run([sys.executable, str(HELPER), '--base', base, '--head', head, '--report', str(report)], cwd=repo, env={**os.environ, 'PATH': str(fake_bin) + os.pathsep + os.environ['PATH'], 'GITHUB_OUTPUT': str(output)}, capture_output=True, text=True)
    assert report.exists(), result.stderr
    outputs = dict(line.split('=', 1) for line in output.read_text().splitlines())
    return result, json.loads(report.read_text()), outputs


def npm_stub(tmp_path, mode='clean'):
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    npm = fake_bin / 'npm'
    npm.write_text(f'''#!{sys.executable}
import json, pathlib, sys
assert sys.argv[1:] == ['audit', '--package-lock-only', '--ignore-scripts', '--audit-level=high', '--json']
mode = {mode!r}
website = pathlib.Path.cwd().name == 'website'
if website and mode == 'timeout':
    import time
    time.sleep(0.2)
if website and mode == 'malformed':
    print('not JSON'); sys.exit(1)
if website and mode == 'invalid-schema':
    print(json.dumps({{'metadata': {{'vulnerabilities': {{'high': 0, 'critical': 0}}}}, 'vulnerabilities': [{{'severity': 'high'}}]}})); sys.exit(1)
if website and mode == 'error':
    print(json.dumps({{'error': {{'code': 'ENOAUDIT'}}}})); sys.exit(1)
high = 30 if website and mode == 'high' else 0
print(json.dumps({{'metadata': {{'vulnerabilities': {{'high': high, 'critical': 0}}}}, 'vulnerabilities': {{'braces': {{'severity': 'high'}}}} if high else {{}}}}))
sys.exit(1 if high else 0)
''')
    npm.chmod(0o755)
    return fake_bin


@pytest.mark.parametrize('comparison', ['full', 'shallow', 'missing-base', 'missing-head', 'empty', 'deleted-only'])
def test_exact_tree_selection_never_falls_back_to_root(tmp_path, comparison):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    for directory in ['.', 'website', 'apps/space name', 'deleted']:
        lock(repo, directory, '1.0.0')
    base = commit(repo)
    for directory in ['website', 'apps/space name']:
        lock(repo, directory, '2.0.0')
    git(repo, 'rm', '-qr', 'deleted')
    head = commit(repo)
    def shallow_comparison():
        shallow = tmp_path / 'shallow'
        subprocess.run(['git', 'clone', '-q', '--depth=1', repo.as_uri(), str(shallow)], check=True, timeout=30)
        git(shallow, 'fetch', '-q', '--depth=1', 'origin', base)
        assert git(shallow, 'rev-parse', '--is-shallow-repository') == 'true'
        return shallow, base, head

    def deleted_comparison():
        git(repo, 'rm', '-qr', 'website')
        return repo, head, commit(repo)

    comparisons = {
        'full': lambda: (repo, base, head),
        'shallow': shallow_comparison,
        'missing-base': lambda: (repo, '0' * 40, head),
        'missing-head': lambda: (repo, base, '0' * 40),
        'empty': lambda: (repo, head, head),
        'deleted-only': deleted_comparison,
    }
    repo, base, head = comparisons[comparison]()
    result, report, outputs = invoke(repo, base, head, npm_stub(tmp_path), tmp_path)
    if comparison in ['full', 'shallow', 'empty', 'deleted-only']:
        assert result.returncode == 0
        expected = ['apps/space name', 'website']
        if comparison == 'empty':
            expected = ['.', 'apps/space name', 'website']
        elif comparison == 'deleted-only':
            expected = ['.', 'apps/space name']
        assert [entry['directory'] for entry in report['audits']] == expected
        assert outputs['exit_code'] == '0'
    else:
        assert result.returncode != 0
        assert report['errors']
        assert report['audits'] == []
        assert outputs['exit_code'] != '0'
        assert json.loads(outputs['review_status'])[0]['results'][0]['kind'] == 'action_required'


@pytest.mark.parametrize('mode', ['clean', 'high', 'malformed', 'error', 'invalid-schema', 'timeout'])
def test_all_directory_reports_are_valid_json_and_failures_survive(tmp_path, mode, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    for directory in ['.', 'website', 'z-clean']:
        lock(repo, directory, '1.0.0')
    base = commit(repo)
    for directory in ['.', 'website', 'z-clean']:
        lock(repo, directory, '2.0.0')
    head = commit(repo)
    if mode == 'timeout':
        from scripts.ci import npm_audit

        real_run = subprocess.run

        def short_website_audit(command, **kwargs):
            if command[:2] == ['npm', 'audit'] and kwargs.get('cwd') == 'website':
                kwargs['timeout'] = 0.02
            return real_run(command, **kwargs)

        monkeypatch.chdir(repo)
        monkeypatch.setenv('PATH', str(npm_stub(tmp_path, mode)) + os.pathsep + os.environ['PATH'])
        monkeypatch.setattr(subprocess, 'run', short_website_audit)
        report = npm_audit.audit(base, head)
        assert [entry['directory'] for entry in report['audits']] == ['.', 'website', 'z-clean']
        assert [entry['exit_code'] for entry in report['audits']] == [0, 1, 0]
        assert len(report['errors']) == 1 and 'timed out' in report['errors'][0]
        assert npm_audit.review_status(report)[0]['results'][0]['kind'] == 'action_required'
        return
    result, report, outputs = invoke(repo, base, head, npm_stub(tmp_path, mode), tmp_path)
    assert [entry['directory'] for entry in report['audits']] == ['.', 'website', 'z-clean']
    failed = mode != 'clean'
    assert (result.returncode != 0) == failed
    assert (outputs['exit_code'] != '0') == failed
    status = json.loads(outputs['review_status'])
    assert bool(status) == failed
    if mode == 'high':
        assert report['high'] == 30
        assert report['critical'] == 0
        assert '30 high' in status[0]['results'][0]['summary']
        assert 'braces' in status[0]['results'][0]['detail']
    if mode in ['malformed', 'error', 'invalid-schema']:
        assert report['errors']

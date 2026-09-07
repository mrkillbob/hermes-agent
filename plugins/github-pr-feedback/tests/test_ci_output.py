import hashlib
import os
import sys
import time

from github_pr_feedback.ci_runner import SubprocessCICommandRunner


def test_only_nonempty_failed_command_output_is_retained(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    runner = SubprocessCICommandRunner()
    runner.run(
        (sys.executable, '-c', "print('success detail')"),
        cwd=tmp_path, env=dict(os.environ), timeout=10,
    )
    output_root = tmp_path / 'home/github-pr-feedback/ci-output'
    assert not list(output_root.glob('*.log'))

    result = SubprocessCICommandRunner().run(
        (sys.executable, '-c', "import sys; print('failure detail'); sys.exit(7)"),
        cwd=tmp_path, env=dict(os.environ), timeout=10,
    )
    assert result.returncode == 7
    digest = hashlib.sha256(result.stdout.encode()).hexdigest()
    output = tmp_path / 'home/github-pr-feedback/ci-output' / (digest + '.log')
    assert output.read_text() == result.stdout
    assert output.stat().st_mode & 0o777 == 0o600
    assert list(output.parent.glob('*.log')) == [output]


def test_ci_diagnostics_are_bounded_by_age_count_and_size(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    from github_pr_feedback import ci_output

    monkeypatch.setattr(ci_output, '_MAX_DIAGNOSTIC_FILES', 10)
    monkeypatch.setattr(ci_output, '_MAX_DIAGNOSTIC_BYTES', 100)
    monkeypatch.setattr(ci_output, '_DIAGNOSTIC_TTL_SECONDS', 60)
    old = ci_output.retain_output('old')
    newest = [ci_output.retain_output(value) for value in ('new-a', 'new-b', 'new-c')]
    assert old is not None
    assert all(path is not None for path in newest)
    old_timestamp = time.time() - ci_output._DIAGNOSTIC_TTL_SECONDS - 1
    os.utime(old, (old_timestamp, old_timestamp))

    monkeypatch.setattr(ci_output, '_MAX_DIAGNOSTIC_FILES', 2)
    monkeypatch.setattr(ci_output, '_MAX_DIAGNOSTIC_BYTES', 6)
    ci_output.cleanup_outputs()

    retained = sorted(old.parent.glob('*.log'))
    assert old not in retained
    assert len(retained) <= 2
    assert sum(path.stat().st_size for path in retained) <= 6
    assert set(retained).issubset(newest)

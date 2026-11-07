"""Kanban worktree base refresh authenticates private HTTPS remotes without weakening GHSA isolation.

The dispatcher's hardened git env (``noninteractive_git_env``) deliberately disables credential
helpers, so ``_git`` alone can never fetch a private HTTPS base. The refresh must instead run
anonymously first and inject the user's stored credential through the env config channel only
after the remote refuses — the ``run_git_with_credential_fallback`` pattern from plugin installs.
"""

import base64
import subprocess
from pathlib import Path

import pytest

from hermes_cli import git_credentials
from hermes_cli import kanban_db_workspace as kbw


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True)


@pytest.fixture
def kanban_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A project repo tracking a local bare origin with one pushed commit."""
    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", str(origin))
    project = tmp_path / "project"
    _git("clone", "-q", str(origin), str(project))
    _git("-C", str(project), "config", "user.email", "t@example.com")
    _git("-C", str(project), "config", "user.name", "t")
    (project / "README.md").write_text("hello\n", encoding="utf-8")
    _git("-C", str(project), "add", "README.md")
    _git("-C", str(project), "commit", "-qm", "init")
    _git("-C", str(project), "push", "-q", "origin", "HEAD")
    return project


def _write_base_ref_config(home: Path, repo: Path, ref: str) -> None:
    (home / "config.yaml").write_text(
        f"kanban:\n  worktree_base_refs:\n    {repo.resolve()}: {ref}\n", encoding="utf-8")


def test_configured_base_refresh_fetches_new_remote_commit(kanban_home: Path, repo: Path) -> None:
    """E2E through real imports: a commit pushed to origin after the local clone must be visible
    to ``_configured_worktree_base`` — proving the fetch actually ran via the credential-aware
    wrapper (anonymous success path, local remote)."""
    _write_base_ref_config(kanban_home, repo, "origin/stable")
    _git("-C", str(repo), "push", "-q", "origin", "HEAD:refs/heads/stable")

    work = repo.parent / "pusher"
    _git("clone", "-q", str(repo.parent / "origin.git"), str(work))
    _git("-C", str(work), "config", "user.email", "t@example.com")
    _git("-C", str(work), "config", "user.name", "t")
    (work / "next.txt").write_text("next\n", encoding="utf-8")
    _git("-C", str(work), "add", "next.txt")
    _git("-C", str(work), "commit", "-qm", "next")
    _git("-C", str(work), "push", "-q", "origin", "HEAD:refs/heads/stable")
    tip_after = _git("-C", str(work), "rev-parse", "HEAD").stdout.strip()

    assert kbw._configured_worktree_base(repo) == tip_after


@pytest.mark.parametrize("outcome", ["refused", "not_found"])
def test_base_fetch_attaches_credential_only_after_anonymous_refusal(
        repo: Path, monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    """A private HTTPS base must run the fetch anonymously first and attach the stored credential
    only when the remote demands one; a non-credential failure surfaces as-is with the credential
    never resolved, let alone sent."""
    public_url = "https://github.com/acme/private-base.git"
    _git("-C", str(repo), "remote", "set-url", "origin", public_url)

    attempts: list[list[str]] = []
    real_run = subprocess.run

    def _auth_headers(env: dict) -> list[str]:
        count = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
        return [env[f"GIT_CONFIG_VALUE_{i}"] for i in range(count)
                if env.get(f"GIT_CONFIG_KEY_{i}") == "http.https://github.com/.extraheader"]

    def spy_run(argv, *a, **kw):
        if "fetch" not in argv:
            return real_run(argv, *a, **kw)
        attempts.append(_auth_headers(kw.get("env") or {}))
        if outcome == "refused" and len(attempts) == 1:
            return subprocess.CompletedProcess(
                argv, 128, stdout="",
                stderr="fatal: could not read Username for 'https://github.com': "
                       "terminal prompts disabled\n")
        if outcome == "not_found":
            return subprocess.CompletedProcess(
                argv, 128, stdout="", stderr=f"fatal: repository '{public_url}' not found\n")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", spy_run)
    if outcome == "not_found":
        monkeypatch.setattr(
            git_credentials, "resolve_git_basic_auth",
            lambda url: pytest.fail("credential must not be resolved for a non-credential failure"))
    else:
        monkeypatch.setattr(git_credentials, "resolve_git_basic_auth",
                            lambda url: ("x-access-token", "ghp_fake"))

    result = kbw._git_with_stored_credentials(
        repo, "fetch", "--no-tags", "origin",
        "+refs/heads/stable:refs/remotes/origin/stable", timeout=60)

    assert result.returncode == (0 if outcome == "refused" else 128)
    expected = base64.b64encode(b"x-access-token:ghp_fake").decode()
    assert attempts == ([[], [f"Authorization: basic {expected}"]]
                        if outcome == "refused" else [[]])


def test_local_verbs_stay_on_the_hardened_path(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only network verbs take the credential-fallback path; local verbs keep using the hardened
    ``_git`` env unchanged (no filter discovery, no credential machinery)."""
    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy_run(argv, *a, **kw):
        calls.append(list(argv))
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy_run)
    result = kbw._git_with_stored_credentials(repo, "remote", timeout=20)
    assert result.returncode == 0
    assert calls == [["git", "-C", str(repo), "remote"]]

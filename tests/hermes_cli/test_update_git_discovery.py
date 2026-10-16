"""Git trampoline recovery discovers portable installs without assuming an OS."""

from types import SimpleNamespace

import pytest

from hermes_cli import update_cmd_git


@pytest.mark.parametrize("source", ["path", "managed"])
@pytest.mark.parametrize("relative", [
    "mingw64/bin/git.exe",
    "clangarm64/libexec/git-core/git.exe",
    "mingw32/bin/git.exe",
])
def test_locate_real_git_in_portable_install(tmp_path, monkeypatch, source, relative):
    import hermes_constants
    import hermes_platform.resolver

    root = tmp_path / "versioned-portable-git"
    expected = root / relative
    expected.parent.mkdir(parents=True)
    expected.touch()
    monkeypatch.setattr(hermes_constants, "get_default_hermes_root", lambda: tmp_path / "shared")
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path / "profile")
    if source == "managed":
        (tmp_path / "shared").mkdir()
        root.rename(tmp_path / "shared" / "git")
        expected = tmp_path / "shared" / "git" / relative
    command = (str(root / "cmd" / "git.exe"),) if source == "path" else ()
    monkeypatch.setattr(hermes_platform.resolver, "locate_command", lambda *_: SimpleNamespace(command=command))
    monkeypatch.setattr(update_cmd_git, "_probe_fork_bomb", lambda argv: argv != [str(expected)])

    assert update_cmd_git._locate_real_git() == expected


@pytest.mark.parametrize("probe", [True, None])
def test_locate_real_git_rejects_broken_or_unprobeable_candidates(tmp_path, monkeypatch, probe):
    import hermes_platform.resolver

    candidate = tmp_path / "git.exe"
    candidate.touch()
    monkeypatch.setattr(hermes_platform.resolver, "locate_command", lambda *_: SimpleNamespace(command=()))
    monkeypatch.setattr(update_cmd_git, "_portable_git_candidates", lambda: [candidate])
    monkeypatch.setattr(update_cmd_git, "_probe_fork_bomb", lambda _: probe)

    assert update_cmd_git._locate_real_git() is None

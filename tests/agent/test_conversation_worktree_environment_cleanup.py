"""Cleanup may unlink an equivalent bootstrap link, never its source environment."""

from dataclasses import replace

import pytest

from agent.conversation_worktree import ConversationWorktreeManager
from hermes_cli.worktree_environment import _venv_python_path
from tests.agent.test_conversation_worktree_cleanup import git, prepared_binding


def usable_environment(path):
    python = _venv_python_path(path)
    python.parent.mkdir(parents=True)
    python.write_text("synthetic interpreter; never executed\n")
    python.chmod(0o755)
    path.joinpath("source-sentinel").write_text("preserve source environment\n")
    return path


@pytest.fixture(params=["local", "legacy", "managed_sibling"])
def managed_environment(
    prepared_binding,  # health: allow F811 -- pytest injects the imported prepared_binding fixture by parameter name
    request,
):
    manager, db, source, _remote, _binding, _sibling = prepared_binding
    source.joinpath(".gitignore").write_text("/.venv\n/venv\n")
    git(source, "add", ".gitignore")
    git(source, "commit", "-m", "Ignore local environments")
    git(source, "push", "origin", "stable")
    if request.param == "managed_sibling":
        environment = usable_environment(source.parent / "venvs" / "managed")
        source.joinpath(".venv").symlink_to(environment, target_is_directory=True)
    else:
        environment = usable_environment(
            source / ("venv" if request.param == "legacy" else ".venv")
        )
    binding = manager.bind_new_root_session(
        "managed-environment", conversation_kind="interactive"
    )
    assert binding.path.joinpath(".venv").is_symlink()
    assert binding.path.joinpath(".venv").resolve() == environment.resolve()
    return manager, db, source, binding, environment


def test_cleanup_unlinks_actual_bootstrap_environment_without_removing_source(
    managed_environment,
):
    manager, db, source, binding, environment = managed_environment
    identity = environment.stat().st_dev, environment.stat().st_ino
    before = environment.joinpath("source-sentinel").read_bytes()
    assert git(binding.path, "ls-files", "--others", "--ignored", "--exclude-standard") == ".venv"

    verdict = manager.inspect_cleanup(binding.root_session_id)
    assert verdict.allowed, verdict.reasons
    removed = manager.remove_after_explicit_request(binding.root_session_id)

    assert removed.removed, removed
    assert not binding.path.exists()
    assert db.get_conversation_worktree(binding.root_session_id).state == "removed"
    assert (environment.stat().st_dev, environment.stat().st_ino) == identity
    assert environment.joinpath("source-sentinel").read_bytes() == before
    assert git(source, "rev-parse", binding.branch) == binding.base_commit


@pytest.mark.parametrize("location", [".", "shared-environment"])
def test_cleanup_preserves_equivalent_environment_inside_removal_root(request, location):
    original, db, source, _remote, _binding, _sibling = request.getfixturevalue(
        "prepared_binding"
    )
    source.joinpath(".gitignore").write_text("/.venv\n/venv\n")
    git(source, "add", ".gitignore")
    git(source, "commit", "-m", "Ignore environment links")
    git(source, "push", "origin", "stable")
    manager = ConversationWorktreeManager(
        replace(original._policy, worktree_root=source.parent / "venvs"), db
    )
    binding = manager.bind_new_root_session(
        "inside-environment", conversation_kind="interactive"
    )
    environment = usable_environment(binding.path / location)
    git(binding.path, "add", ".")
    git(binding.path, "commit", "-m", "Retain environment files")
    git(binding.path, "push", "origin", f"HEAD:refs/heads/{binding.branch}")
    git(source, "merge", "--ff-only", binding.branch)
    git(source, "push", "origin", "stable")
    source.joinpath(".venv").symlink_to(environment, target_is_directory=True)
    binding.path.joinpath(".venv").symlink_to(environment, target_is_directory=True)
    assert git(binding.path, "ls-files", "--others", "--ignored", "--exclude-standard") == ".venv"
    before = environment.stat().st_dev, environment.stat().st_ino

    result = manager.remove_after_explicit_request(binding.root_session_id)

    assert not result.removed
    assert "ignored content requires preservation" in result.verdict.reasons
    assert (environment.stat().st_dev, environment.stat().st_ino) == before
    assert environment.joinpath("source-sentinel").read_text() == "preserve source environment\n"
    assert source.joinpath(".venv").resolve(strict=True) == environment


@pytest.mark.parametrize("layout", ["leaf_inside", "parent_inside_leaf_outside"])
def test_cleanup_preserves_interpreter_lookup_inside_removal_root(request, tmp_path, layout):
    manager, _db, source, _remote, _binding, _sibling = request.getfixturevalue(
        "prepared_binding"
    )
    source.joinpath(".gitignore").write_text("/.venv\n/venv\n")
    git(source, "add", ".gitignore")
    git(source, "commit", "-m", "Ignore environment links")
    git(source, "push", "origin", "stable")
    binding = manager.bind_new_root_session(
        "interpreter-layout", conversation_kind="interactive"
    )
    environment = source.parent / "venvs" / "external-managed"
    environment.mkdir(parents=True)
    interpreter = _venv_python_path(environment)
    if layout == "leaf_inside":
        executable = binding.path / "tracked-python"
        executable.write_text("synthetic interpreter; never executed\n")
        executable.chmod(0o755)
        interpreter.parent.mkdir()
        interpreter.symlink_to(executable)
    else:
        executable = tmp_path / "retained-external-python"
        executable.write_text("synthetic interpreter; never executed\n")
        executable.chmod(0o755)
        native = binding.path / "tracked-native-bin"
        native.mkdir()
        native.joinpath(interpreter.name).symlink_to(executable)
        interpreter.parent.symlink_to(native, target_is_directory=True)
    git(binding.path, "add", ".")
    git(binding.path, "commit", "-m", "Retain interpreter lookup files")
    git(binding.path, "push", "origin", f"HEAD:refs/heads/{binding.branch}")
    git(source, "merge", "--ff-only", binding.branch)
    git(source, "push", "origin", "stable")
    source.joinpath(".venv").symlink_to(environment, target_is_directory=True)
    binding.path.joinpath(".venv").symlink_to(environment, target_is_directory=True)
    assert git(binding.path, "ls-files", "--others", "--ignored", "--exclude-standard") == ".venv"
    paths = environment, interpreter.parent, interpreter
    identities = [(item.stat().st_dev, item.stat().st_ino) for item in paths]

    result = manager.remove_after_explicit_request(binding.root_session_id)

    assert not result.removed
    assert "ignored content requires preservation" in result.verdict.reasons
    assert [(item.stat().st_dev, item.stat().st_ino) for item in paths] == identities
    assert executable.read_text() == "synthetic interpreter; never executed\n"
    assert source.joinpath(".venv").resolve(strict=True) == environment


@pytest.mark.parametrize("hop", ["parent", "leaf"])
def test_cleanup_preserves_intermediate_interpreter_lookup_hop(
    managed_environment, tmp_path, hop,
):
    manager, _db, source, binding, environment = managed_environment
    python = _venv_python_path(environment)
    bridge = binding.path / "tracked-lookup-bridge"
    original = python.parent if hop == "parent" else python
    retained = tmp_path / "retained-interpreter-lookup"
    original.rename(retained)
    bridge.symlink_to(retained, target_is_directory=hop == "parent")
    original.symlink_to(bridge, target_is_directory=hop == "parent")
    git(binding.path, "add", ".")
    git(binding.path, "commit", "-m", "Retain interpreter lookup bridge")
    git(binding.path, "push", "origin", f"HEAD:refs/heads/{binding.branch}")
    git(source, "merge", "--ff-only", binding.branch)
    git(source, "push", "origin", "stable")
    assert python.is_file()
    assert not python.resolve().is_relative_to(binding.path)
    assert not python.parent.resolve().is_relative_to(binding.path)
    identities = [(item.stat().st_dev, item.stat().st_ino) for item in (environment, python)]

    result = manager.remove_after_explicit_request(binding.root_session_id)

    assert not result.removed
    assert "ignored content requires preservation" in result.verdict.reasons
    assert [(item.stat().st_dev, item.stat().st_ino) for item in (environment, python)] == identities
    assert bridge.is_symlink()
    assert python.read_text() == "synthetic interpreter; never executed\n"


def _ignored_file(case, _tmp_path):
    _manager, _db, source, binding, _environment = case
    exclude = source / ".git" / "info" / "exclude"
    with exclude.open("a") as stream:
        stream.write("\nprivate-output\n")
    preserved = binding.path / "private-output"
    preserved.write_text("unique ignored data")
    return preserved


def _foreign_link(case, tmp_path):
    _manager, _db, _source, binding, _environment = case
    foreign = usable_environment(tmp_path / "foreign")
    link = binding.path / ".venv"
    link.unlink()
    link.symlink_to(foreign, target_is_directory=True)
    return foreign / "source-sentinel"


def _real_directory(case, _tmp_path):
    _manager, _db, _source, binding, _environment = case
    link = binding.path / ".venv"
    link.unlink()
    return usable_environment(link) / "source-sentinel"


def _missing_python(case, _tmp_path):
    _manager, _db, _source, _binding, environment = case
    _venv_python_path(environment).unlink()
    return environment / "source-sentinel"


def _cyclic_python(case, _tmp_path):
    _manager, _db, _source, _binding, environment = case
    python = _venv_python_path(environment)
    python.unlink()
    python.symlink_to(python.name)
    return environment / "source-sentinel"


def _changed_source(case, tmp_path):
    _manager, _db, source, _binding, environment = case
    pointer = source / ("venv" if environment.name == "venv" else ".venv")
    pointer.rename(tmp_path / "retained-original-environment")
    return tmp_path / "retained-original-environment" / "source-sentinel"


@pytest.mark.parametrize(
    "mutation", [
        _ignored_file, _foreign_link, _real_directory, _missing_python,
        _cyclic_python, _changed_source,
    ]
)
def test_cleanup_preserves_non_equivalent_or_unique_ignored_content(
    managed_environment, mutation, tmp_path,
):
    manager, db, _source, binding, environment = managed_environment
    sentinel = environment / "source-sentinel"
    original = sentinel.read_bytes()
    preserved = mutation(managed_environment, tmp_path)
    before = preserved.read_bytes()

    verdict = manager.inspect_cleanup(binding.root_session_id)
    assert not verdict.allowed
    assert "ignored content requires preservation" in verdict.reasons
    removed = manager.remove_after_explicit_request(binding.root_session_id)

    assert not removed.removed
    assert binding.path.is_dir()
    assert db.get_conversation_worktree(binding.root_session_id).state == "ready"
    assert preserved.read_bytes() == before
    if sentinel.exists():
        assert sentinel.read_bytes() == original


@pytest.mark.parametrize("race", ["link", "target"])
def test_cleanup_refuses_environment_identity_changed_during_content_probe(
    managed_environment, monkeypatch, tmp_path, race,
):
    manager, _db, _source, binding, environment = managed_environment
    foreign = usable_environment(tmp_path / "foreign")
    original_git = manager._run_git
    changed = False

    def changing_probe(cwd, args, timeout, phase):
        nonlocal changed
        if args[0] == "ls-files" and not changed:
            changed = True
            if race == "link":
                link = binding.path / ".venv"
                link.unlink()
                link.symlink_to(foreign, target_is_directory=True)
            else:
                environment.rename(tmp_path / "retained-target")
                usable_environment(environment)
        return original_git(cwd, args, timeout, phase)

    monkeypatch.setattr(manager, "_run_git", changing_probe)
    removed = manager.remove_after_explicit_request(binding.root_session_id)

    assert changed
    assert not removed.removed
    assert "ignored content requires preservation" in removed.verdict.reasons
    assert binding.path.joinpath(".venv").resolve() == (
        foreign if race == "link" else environment
    )
    assert environment.joinpath("source-sentinel").is_file()
    assert foreign.joinpath("source-sentinel").is_file()
    if race == "target":
        assert tmp_path.joinpath("retained-target", "source-sentinel").is_file()


@pytest.mark.parametrize("change_hop", [False, True])
def test_cleanup_observes_external_interpreter_lookup_hops(
    managed_environment, tmp_path, monkeypatch, change_hop,
):
    manager, _db, _source, binding, environment = managed_environment
    python = _venv_python_path(environment)
    native = python.parent
    retained = tmp_path / "retained-native-bin"
    native.rename(retained)
    bridge = tmp_path / "external-bridge"
    bridge.symlink_to(retained, target_is_directory=True)
    alternate = tmp_path / "external-alternate"
    alternate.symlink_to(retained, target_is_directory=True)
    native.symlink_to(bridge, target_is_directory=True)
    before = python.stat().st_dev, python.stat().st_ino
    original_git = manager._run_git
    changed = False

    def changing_probe(cwd, args, timeout, phase):
        nonlocal changed
        if change_hop and args[0] == "ls-files" and not changed:
            changed = True
            bridge.unlink()
            bridge.symlink_to(alternate, target_is_directory=True)
        return original_git(cwd, args, timeout, phase)

    monkeypatch.setattr(manager, "_run_git", changing_probe)
    result = manager.remove_after_explicit_request(binding.root_session_id)

    assert result.removed is not change_hop
    assert changed is change_hop
    assert (python.stat().st_dev, python.stat().st_ino) == before
    assert python.read_text() == "synthetic interpreter; never executed\n"
    assert environment.joinpath("source-sentinel").read_text() == "preserve source environment\n"
    if change_hop:
        assert "ignored content requires preservation" in result.verdict.reasons
        assert binding.path.exists()


def test_cleanup_allows_repeated_outside_alias_without_a_symlink_cycle(
    managed_environment, tmp_path,
):
    manager, _db, _source, binding, environment = managed_environment
    python = _venv_python_path(environment)
    native = python.parent
    retained = tmp_path / "retained-native-bin"
    native.rename(retained)
    executable = tmp_path / "retained-python"
    retained.joinpath(python.name).rename(executable)
    alias = tmp_path / "outside-alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    native.symlink_to(alias / retained.name, target_is_directory=True)
    retained.joinpath(python.name).symlink_to(alias / executable.name)
    before = executable.stat().st_dev, executable.stat().st_ino
    assert python.resolve(strict=True) == executable

    result = manager.remove_after_explicit_request(binding.root_session_id)

    assert result.removed, result.verdict.reasons
    assert python.is_file()
    assert (executable.stat().st_dev, executable.stat().st_ino) == before


def test_cleanup_preserves_interpreter_through_native_case_alias(managed_environment):
    manager, _db, source, binding, environment = managed_environment
    alias = binding.path.with_name(binding.path.name.swapcase())
    if alias == binding.path or not alias.exists() or not alias.samefile(binding.path):
        pytest.skip("native filesystem does not expose case aliases")
    assert alias != binding.path
    python = _venv_python_path(environment)
    executable = binding.path / "tracked-python"
    python.rename(executable)
    python.symlink_to(alias / executable.name)
    git(binding.path, "add", ".")
    git(binding.path, "commit", "-m", "Retain interpreter referenced through case alias")
    git(binding.path, "push", "origin", f"HEAD:refs/heads/{binding.branch}")
    git(source, "merge", "--ff-only", binding.branch)
    git(source, "push", "origin", "stable")
    before = python.stat().st_dev, python.stat().st_ino
    assert not python.resolve(strict=True).is_relative_to(binding.path)

    result = manager.remove_after_explicit_request(binding.root_session_id)

    assert not result.removed
    assert "ignored content requires preservation" in result.verdict.reasons
    assert (python.stat().st_dev, python.stat().st_ino) == before
    assert executable.read_text() == "synthetic interpreter; never executed\n"

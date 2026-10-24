"""The first update marker must not make newly managed state look legacy."""
import pytest


@pytest.mark.parametrize("existing", [False, True])
def test_completion_qualifies_only_a_fresh_install(tmp_path, monkeypatch, existing):
    from hermes_cli.venv_sync import arm_completion
    from pm.environments import install_state_dir
    from pm.lifetime import gate

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    state = install_state_dir(project)
    if existing:
        state.mkdir(parents=True)
    pending = arm_completion(project)
    assert pending.read_text() == "source update tail not finished\n"
    with gate(state) as guard:
        assert guard.qualified is (not existing)
        assert not guard.held()

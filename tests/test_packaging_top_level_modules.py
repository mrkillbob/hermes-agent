"""Regression coverage for single-file runtime modules shipped in the wheel."""

from pathlib import Path
import tomllib


def test_every_state_runtime_is_declared_as_a_packaged_module() -> None:
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    packaged = set(project["tool"]["setuptools"]["py-modules"])
    state_modules = {path.stem for path in root.glob("hermes_state*.py")}

    assert state_modules <= packaged

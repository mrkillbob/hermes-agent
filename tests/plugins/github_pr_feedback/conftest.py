"""Load the real in-tree plugin package without changing global import paths."""

import importlib.util
import sys
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[3] / "plugins/github-pr-feedback/github_pr_feedback"
spec = importlib.util.spec_from_file_location(
    "github_pr_feedback", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)]
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


@pytest.fixture(autouse=True)
def configured_hermes_executable(tmp_path, monkeypatch):
    """An inert configured launcher; test transports must never execute it."""
    executable = tmp_path / "configured-hermes"
    executable.write_text("not an executable program\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setenv("HERMES_EXECUTABLE", str(executable))
    return executable

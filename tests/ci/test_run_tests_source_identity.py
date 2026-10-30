from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_worktree_runner_never_borrows_live_editable_venv(tmp_path: Path) -> None:
    repository = tmp_path / "worktree"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    source_scripts = Path(__file__).resolve().parents[2] / "scripts"
    source = source_scripts / "run_tests.sh"
    runner = scripts / "run_tests.sh"
    for name in (source.name, "_activation.sh", "run-in-hermes-env"):
        shutil.copy2(source_scripts / name, scripts / name)
    (repository / "setup-hermes.sh").write_text(
        '#!/bin/sh\nprintf "invoked\\n" > "$HOME/setup-invoked"\nexit 1\n',
        encoding="utf-8",
    )

    fake_python = tmp_path / "home" / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        '#!/bin/sh\nprintf "invoked\\n" > "$HOME/live-python-invoked"\nexit 0\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    (fake_python.parent / "activate").touch()

    completed = subprocess.run(
        ["bash", str(runner), "tests"],
        cwd=repository,
        env={"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "setup failed" in completed.stderr
    assert (tmp_path / "home" / "setup-invoked").is_file()
    assert not (tmp_path / "home" / "live-python-invoked").exists()
    assert "using Nix dev venv" not in completed.stdout

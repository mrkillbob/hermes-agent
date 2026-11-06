import json
import os
from pathlib import Path
from types import SimpleNamespace

import builtins
import hermes_cli.migrate as migrate_mod
import hermes_cli.setup_agent_import as sai


class DummyCP:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_cmd_migrate_agent_forwards_capture_dir(monkeypatch, tmp_path):
    called = {}

    def fake_mfa(home, name, extra, capture_dir=None):  # signature match
        called["args"] = {
            "home": home,
            "name": name,
            "extra": tuple(extra),
            "capture_dir": capture_dir,
        }
        return 0

    monkeypatch.setattr(migrate_mod, "migrate_from_agents", fake_mfa)

    args = SimpleNamespace(
        migrate_agent="claude-code",
        dry_run=True,
        overwrite=False,
        yes=True,
        activate_routines=False,
        migrate_secrets=False,
        source=None,
        bot=None,
        capture_dir=str(tmp_path),
    )

    # get_hermes_home returns a path; avoid importing config to keep test hermetic
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes-test"))

    rc = migrate_mod.cmd_migrate_agent(args)
    assert rc == 0
    assert called["args"]["capture_dir"] == tmp_path


def test__import_child_captures_raw_and_json(monkeypatch, tmp_path):
    # Arrange: fake subprocess.run to return some output
    def fake_run(argv, env=None, check=False, capture_output=False, text=False):
        if capture_output:
            return DummyCP(0, stdout="PREVIEW:\n{}\n", stderr="")
        return DummyCP(0)

    monkeypatch.setattr(sai.subprocess, "run", fake_run)

    # Minimal ImportSource
    source = sai.ImportSource("claude-code", "Claude Code", (".claude",))

    out_dir = tmp_path / "cap"
    rc = sai._import_child(source, tmp_path / "home", tuple(), from_setup=False, capture_dir=out_dir)

    assert rc == 0
    raw = (out_dir / "raw.txt").read_text()
    assert "PREVIEW" in raw
    # A fallback report.json is always created
    data = json.loads((out_dir / "report.json").read_text())
    assert data["source"] == "claude-code"

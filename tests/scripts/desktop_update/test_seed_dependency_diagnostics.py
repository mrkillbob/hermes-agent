"""Offline npm child/seed boundaries retain safe failure evidence before clipping.

No installer, registry, compiler, or Desktop process runs in these fixtures.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
MARKER = "HERMES_NPM_FAILURE_V1 "
SECRET = "private-token-do-not-publish"
KNOWN_LOG = "0 error code ENOSPC\n1 error syscall write\n2 error errno -28\n"


def load_seed():
    spec = importlib.util.spec_from_file_location("desktop_update_seed", ROOT / "apps/desktop/e2e/update/seed.py")
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    return seed


def synthetic_npm(tmp_path, logs):
    source = tmp_path / "source"
    (source / "web").mkdir(parents=True)
    (source / "node_modules").mkdir()
    (source / "node_modules/preserved-before-ci").write_text("existing tree")
    (source / "package.json").write_text('{"private":true,"workspaces":["web"]}')
    (source / "web/package.json").write_text('{"name":"fixture-web"}')
    (source / "package-lock.json").write_text(json.dumps({"packages": {
        "": {}, "web": {"name": "fixture-web"},
        "node_modules/fixture-web": {"link": True, "resolved": "web"},
    }}))
    npm = tmp_path / "npm"
    (npm / "bin").mkdir(parents=True)
    (npm / "node_modules/semver").mkdir(parents=True)
    (npm / "package.json").write_text('{"version":"12.0.2","type":"module"}')
    (npm / "node_modules/semver/index.js").write_text(
        'exports.satisfies = () => { throw Error("unexpected engine probe") }')
    cli = npm / "bin/npm-cli.js"
    # A real Node child is the only substitute: deterministic npm outputs, no network.
    cli.write_text("""import {writeFileSync,readFileSync,existsSync} from 'node:fs';
const plan = JSON.parse(process.env.FIXTURE_PLAN);
const calls = process.env.FIXTURE_CALLS;
const count = existsSync(calls) ? Number(readFileSync(calls,'utf8')) : 0;
writeFileSync(calls,String(count+1));
const dir = process.argv.find(arg=>arg.startsWith('--logs-dir=')).slice(11);
writeFileSync(process.env.FIXTURE_LOGDIR,dir);
const step = plan[count];
if (step.log !== null) writeFileSync(dir+'/attempt-'+count+'.log',step.log);
process.exitCode = step.status;
""")
    env = {key: value for key, value in os.environ.items()
           if not key.lower().startswith("npm_config_") and key not in {"NODE_OPTIONS", "npm_execpath"}}
    env.update(npm_execpath=str(cli), FIXTURE_PLAN=json.dumps(logs),
               FIXTURE_CALLS=str(tmp_path / "calls"), FIXTURE_LOGDIR=str(tmp_path / "logdir"))
    return source, env


@pytest.mark.parametrize("steps,expected_frames", [
    ([{"status": 152, "log": KNOWN_LOG}], 1),
    ([{"status": 152, "log": None}], 1),
    ([{"status": 152, "log": "discarded detail\n" * 5000 + "9999 error code ENOSPC\n"}], 1),
    ([{"status": 152, "log": f"0 error code ENOSPC {SECRET}\n1 error syscall write/{SECRET}\n"
      f"2 error errno -28 {SECRET}\n3 verbose argv https://user:{SECRET}@registry.invalid\n"
      f"4 error code {SECRET}\n"}], 1),
    ([{"status": 1, "log": "0 error code ENOTEMPTY\n"},
      {"status": 152, "log": "0 error code ECONNRESET\n"}], 2),
    ([{"status": 1, "log": "0 error code ENOTEMPTY\n"},
      {"status": 0, "log": None}], 0),
])
def test_source_builder_transports_failure_frames_to_seed_without_clipping(tmp_path, steps, expected_frames):
    source, env = synthetic_npm(tmp_path, steps)
    env["HERMES_VERBOSE"] = "0"
    command = [sys.executable, "-c", """
import os,sys,subprocess
from pathlib import Path
from hermes_cli.source_build import run_source_script
try:
    run_source_script(Path(sys.argv[1]),'scripts/build/node-deps.mjs',
                      '--source',sys.argv[2],'--workspace','web',env=dict(os.environ),label='Synthetic npm')
except subprocess.CalledProcessError as error:
    sys.exit(error.returncode)
""", str(ROOT), str(source)]
    cp = subprocess.run(command, env=env, text=True, capture_output=True, timeout=30, check=False)
    assert cp.returncode == int(steps[-1]["status"] != 0), cp.stderr
    # Production Node semantics are covered in Vitest; here the real Python
    # containment and seed consumer must preserve each admitted failure frame.
    prefix = "      " + MARKER
    frames = [json.loads(line.removeprefix(prefix)) for line in cp.stdout.splitlines() if line.startswith(prefix)]
    assert len(frames) == expected_frames, cp.stdout
    if cp.returncode:
        logs = tmp_path / "seed-logs"
        with pytest.raises(SystemExit) as stopped:
            load_seed()._fail("synthetic dependency build", cp, diagnostics_dir=logs)
        assert stopped.value.code == 1
        assert json.loads((logs / "node-dependency-failure.json").read_text())["failures"] == frames
        assert SECRET not in json.dumps(frames) and "registry.invalid" not in json.dumps(frames)


@pytest.mark.parametrize("mutation,accepted", [
    (None, True), ("unknown-key", False), ("malicious-code", False),
    ("bool-status", False), ("malicious-version", False), ("oversized", False),
    ("duplicate-key", False), ("malformed", False), ("malicious-signal", False),
    ("malicious-syscall", False), ("malicious-errno", False), ("bool-elapsed", False),
    ("negative-status", False), ("wrong-phase", False), ("wrong-flags", False),
    ("prefixed-noise", False), ("quoted-marker", False), ("wrong-indent", False),
])
def test_seed_failure_retains_only_validated_marker_before_double_clipping(tmp_path, mutation, accepted, capsys):
    seed = load_seed()
    frame = {"schema": 1, "phase": "npm_ci", "attempt": 1, "status": 152, "signal": None,
             "node_version": "26.7.0", "npm_version": "12.0.2", "elapsed_ms": 42,
             "log_present": True, "log_readable": True, "log_truncated": False,
             "codes": ["unknown"], "syscalls": ["unknown"], "errnos": ["unknown"]}
    changes = {"unknown-key": ("message", SECRET), "malicious-code": ("codes", [f"ENOSPC {SECRET}"]),
               "bool-status": ("status", True), "malicious-version": ("npm_version", f"12.0.2+{SECRET}"),
               "malicious-signal": ("signal", f"SIGTERM {SECRET}"), "malicious-syscall": ("syscalls", [f"write/{SECRET}"]),
               "malicious-errno": ("errnos", [f"-28 {SECRET}"]), "bool-elapsed": ("elapsed_ms", True),
               "negative-status": ("status", -1), "wrong-phase": ("phase", SECRET), "wrong-flags": ("log_readable", SECRET)}
    if mutation in changes:
        key, value = changes[mutation]
        frame[key] = value
    raw = json.dumps(frame)
    if mutation == "oversized":
        raw = " " * 4096 + raw
    elif mutation == "duplicate-key":
        raw = raw[:-1] + ',"status":0}'
    elif mutation == "malformed":
        raw = "{broken"
    prefix = {"prefixed-noise": "untrusted: ", "quoted-marker": '"', "wrong-indent": "   "}.get(mutation, "")
    cp = subprocess.CompletedProcess(["synthetic-build"], 1, "", prefix + MARKER + raw + "\n" + "wrapper noise\n" * 2000)
    logs = tmp_path / "logs"
    with pytest.raises(SystemExit) as stopped:
        seed._fail("synthetic desktop build", cp, diagnostics_dir=logs)
    assert stopped.value.code == 1
    assert MARKER not in capsys.readouterr().err  # It really preceded the clipped tail.
    saved = logs / "node-dependency-failure.json"
    assert saved.exists() == accepted
    if accepted:
        assert json.loads(saved.read_text()) == {"schema": 1, "failures": [frame]}
        assert saved.stat().st_mode & 0o777 == 0o600
        assert SECRET not in saved.read_text()

# Agent Importers 1.2.1 – Selective Bootstrap Acceptance

Scope: Evaluate Agent Importers 1.2.1 against current Hermes profiles and import only improvements. No blanket import, overwrite, auto‑sync, or credential migration.

What changed (selective patch):
- Core migrate supports --capture-dir for importer dry‑runs and archives raw output + JSON report.
  - hermes_cli/subcommands/migrate.py
  - hermes_cli/migrate.py
  - hermes_cli/setup_agent_import.py

Isolated test profile + dry‑run artifacts:
- Profile: sandboxed via HERMES_HOME=.hermes-test-121 (not committed).
- Artifacts path (committed for review): artifacts/importers-121-dryrun/
  - raw.txt – importer preview/log (to be generated in CI)
  - report.json – structured preview summary (CI), stub present now
  - README.md – how to reproduce locally

Behavior validation:
- Added tests to prove parity and new capability:
  - tests/hermes_cli/test_migrate_capture.py
    - Verifies --capture-dir flows to migrate_from_agents
    - Verifies _import_child writes raw.txt and report.json without altering behavior

Provenance/attribution:
- Import mechanics per official agent-importers plugin (NousResearch/hermes-official-plugins). No embedded importer logic added here; only capture plumbing.

Secret review:
- Capture occurs only on explicit request (--capture-dir). No credentials imported. Reports are dry‑run only by default; any secret‑looking values in logs should be redacted upstream by the plugin. This patch does not read or write ~/.hermes/.env.

How to reproduce artifacts locally:
```
export HERMES_HOME="$(pwd)/.hermes-test-121"
mkdir -p artifacts/importers-121-dryrun
python -P -m hermes_cli.main migrate claude-code --dry-run -y \
  --capture-dir artifacts/importers-121-dryrun
```

CI evidence to attach (post‑green):
- raw.txt + report.json uploaded under artifacts/importers-121-dryrun/
- Test run: python scripts/check; pytest -q

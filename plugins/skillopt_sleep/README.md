# SkillOpt-Sleep for Hermes

Opt-in native plugin integrating the pinned `skillopt==0.2.0` Python package.
Install this directory under the active profile's `plugins/skillopt-sleep`,
install `requirements.txt` in the Python environment shown by
`hermes --version-local`, then enable it:

```sh
hermes plugins enable skillopt-sleep --no-allow-tool-override
hermes skillopt-sleep status --project /path/to/project --json
hermes skillopt-sleep dry-run --project /path/to/project --json
```

The CLI and `/skillopt-sleep` slash command share the same arguments. No core
tools are added. The companion optional skill contains the full workflow.

## Current capability

- Read-only, project-filtered Hermes SQLite harvesting, with forced redaction.
- Real SkillOpt mining and mock replay, profile/project/target-scoped state,
  bounded task/session budgets, and separately staged proposals.
- Explicit adoption with finite improvement scores, candidate and target hashes,
  a shared target lock, backups, and next-session activation.
- Mock proposals are never eligible for adoption.

Live upstream CLI backends are deliberately unavailable in this integration.
SkillOpt 0.2.0's full-cycle API cannot accept a backend instance; its thread pools
also do not propagate Hermes's protected execution context. A live adapter must
preserve that context and distinguish text replay from real tool verification.
The current mock diagnostic does not establish self-improvement or CI evidence.
Automatic adoption and upstream cron scheduling are not exposed.

## Development

From a Hermes checkout with the optional requirements installed:

```sh
scripts/run_tests.sh tests/plugins/test_skillopt_sleep_plugin.py tests/plugins/test_skillopt_sleep_source.py
```

Tests use temporary profiles and real SQLite stores and run the installed engine
without network calls. Adoption gate fixtures use synthetic evaluation receipts;
they do not qualify a live provider. Tests skip when the optional engine is absent.

Upstream project: https://github.com/microsoft/SkillOpt

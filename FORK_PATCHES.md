# Fork patches (mrkillbob/hermes-agent)

Edits this fork carries that upstream (`NousResearch/hermes-agent`) does not have. Every
upstream sync must keep them. After merging `upstream/main`, run the guard tests below; a
failure means the sync dropped or reverted a patch — re-apply it, do not delete the test.
Resolve sync conflicts by hand (never `--theirs` a whole file listed here).

| Patch | Files | Guard test |
|---|---|---|
| Skill env passthrough is keyed per conversation session, not process-wide (sibling chats cannot inherit a skill's secret names) | `tools/env_passthrough.py` | `tests/tools/test_env_passthrough_fork_isolation.py` |

```bash
scripts/run_tests.sh tests/tools/test_env_passthrough_fork_isolation.py
```

Add a row (and a guard test in a fork-only file upstream never edits) for every new patch.

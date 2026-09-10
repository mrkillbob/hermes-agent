---
name: skillopt-sleep
description: "Inspect Hermes session-derived tasks and staged SkillOpt proposals."
version: 0.2.0
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [skillopt, self-improvement, skills, research]
    category: research
---

# SkillOpt-Sleep

Use the opt-in plugin to mine recurring work from the active Hermes profile's
sessions for one project. It uses SkillOpt 0.2.0 and keeps its state separate
for each profile, project, and target skill.

## Setup

Install this standalone plugin in the active profile's `plugins/` directory,
install `skillopt==0.2.0` in the active Hermes Python environment, then run
`hermes plugins enable skillopt-sleep`. The dependency is optional; no core
Hermes module needs modification. Restart the conversation to load new commands.

## Commands

```text
hermes skillopt-sleep status --project /path/to/project --json
hermes skillopt-sleep harvest --project /path/to/project --max-sessions 120 --json
hermes skillopt-sleep dry-run --project /path/to/project --target-skill-path .agents/skills/example/SKILL.md --json
hermes skillopt-sleep run --project /path/to/project --target-skill-path .agents/skills/example/SKILL.md --json
hermes skillopt-sleep adopt --project /path/to/project --target-skill-path .agents/skills/example/SKILL.md --json
```

Use the same project and target for status and adoption as for the run.
Without an explicit target, the plugin selects the active profile's
`skills/skillopt-sleep-learned/SKILL.md`. The slash command accepts the same
arguments. Session, task, lookback-hour, and edit limits accept integers 1–1000.

The adapter reads SQLite in read-only mode. It filters by project before the
session limit, exports strictly redacted user/assistant text and tool names,
and omits tool arguments, tool outputs, and raw transcript rows. Redaction
failures abort harvesting. Conversational feedback is a mining hint, not a
verified answer or a successful CI receipt.

## Evidence and adoption

`dry-run` mines and replays tasks without saving state or staging changes.
Both `dry-run` and `run` currently use the mock backend for local integration diagnostics. Mock scores are not evidence
of improvement and mock proposals cannot be adopted. External CLI backends
are unavailable until connected to Hermes's governed egress path; do not
work around this by invoking them directly on harvested sessions.

Inspect the staged report and candidate before explicit adoption. Adoption
requires an accepted, improved non-mock result, matching candidate hash, and
an unchanged target. It writes only the bound skill, preserves a backup, and
never modifies `CLAUDE.md`. Changed skills take effect next session; do not
invalidate a running conversation's prompt cache. Automatic adoption and the
upstream scheduler are not exposed by this integration.

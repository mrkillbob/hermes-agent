#!/bin/sh
# Bounded producer for the existing Hermes dependency audit job.
set -eu
hermes_bin=$(command -v hermes) || exit 127
body_file=$(mktemp) || exit 1
trap 'rm -f "$body_file"' EXIT
trap 'exit 1' HUP INT TERM
week_key=$(date +%G-W%V)
body=$(cat <<'HERMES_DEPENDENCY_AUDIT_BODY'
Audit Python deps (pyproject.toml upper bounds), Node deps (package.json), GitHub Actions SHA pins, and local tooling (serena, tirith, etc.) for staleness or known CVEs. File Kanban tasks for updates under board 'default'. Read-only.


delivery: When you find something actionable, create exactly one Kanban task: `hermes kanban create "<title>" --assignee task-intake-router --body "<details>" --priority <low|medium|high>`. Always assign to task-intake-router — it will route to the right profile. Do NOT assign to any other profile directly. If nothing actionable is found, report OK and create no card.\n\nBOARD POLICY: This job owns the 'default' Kanban board. Pin every Kanban command/tool call to that board explicitly; never use the current-board default and never create or target another board.
HERMES_DEPENDENCY_AUDIT_BODY
)
printf '%s' "$body" > "$body_file"
"$hermes_bin" kanban enqueue-audit \
  --board default --project p_2a482075 \
  --hermes "$hermes_bin" \
  --assignee hermes-maintenance-steward \
  --creator cron-hermes-dependency-health \
  --title "[Audit] Hermes dependency and tooling health ${week_key}" \
  --idempotency-key "hermes-dependency-health-${week_key}" \
  --max-runtime 15m --body-file "$body_file"

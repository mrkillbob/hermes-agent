#!/bin/sh
# Compatibility entrypoint for recurring audit wrappers; project is mandatory.
set -eu
if [ "$#" -ne 7 ] || [ -z "${7:-}" ]; then
    echo 'usage: enqueue-kanban-audit.sh ASSIGNEE CREATOR TITLE KEY MAX_RUNTIME BODY PROJECT' >&2
    exit 64
fi
hermes_bin=$(command -v hermes) || exit 127
body_file=$(mktemp) || exit 1
trap 'rm -f "$body_file"' EXIT
trap 'exit 1' HUP INT TERM
printf '%s' "$6" > "$body_file"
"$hermes_bin" --run-module hermes_cli.kanban_audit_enqueue \
    --hermes "$hermes_bin" --project "$7" --assignee "$1" --creator "$2" \
    --title "$3" --idempotency-key "$4" --max-runtime "$5" --body-file "$body_file"

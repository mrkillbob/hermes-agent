"""Queue a bounded LunaBot issue intake without a cron LLM or GitHub write."""
from __future__ import annotations

import argparse
import json
import subprocess
import time

BOARD = "tradingbot-burndown"
PROFILE = "lunabot-issue-reporter"
TITLE = "Verified LunaBot failure issue intake"
BODY = """Run one bounded, read-only LunaBot failure intake from this governed project worktree.
The human operator authorizes automatic creation of at most one verified, deduplicated issue in private mrkillbob/luna-bot. GitHub writes must be made by governed Hermes as mrkillbobbot.
Within ten minutes inspect at most two existing pure unit-test failures or bounded structural failure receipts, the latest twenty relevant cards, and existing issues/PRs. Do not run main.py, trading runtime, brokers, integration/smoke/slow tests, broad test suites, install packages, edit source, commit, push, approve, or merge. Do not repeat a failed tool or guessed private path.
Only admit a current failure reproducible by one selected pure unit pytest nodeid at the exact freshly verified origin/stable HEAD. Exclude failures already tracked by an issue, open PR, or owned Kanban repair; quota, missing optional evidence, infrastructure outage, or an expected diagnostic failure alone is not a source bug.
Use .github/ISSUE_TEMPLATE/hermes-worker-test-failure.yml and the lunabot-issue-reporter contract. If no new qualifying failure is established, complete ISSUE_REPORTER_IDLE with the bounded evidence gap and create no children or issue.
For a qualifying unit failure, write only the local structural packet artifacts/kanban/issue-packet.json with schema_version=1, repository=mrkillbob/luna-bot, expected_stable_head, test_nodeid, source_owner, expected_exit_code=1, task_id, and profile=lunabot-issue-reporter. Never put raw logs, credentials, private paths, or model-authored GitHub body text into remote context.
Run the governed github-pr-feedback doctor and require mrkillbobbot. Publish only through the dispatcher interpreter's github-pr-feedback publish-issue --packet artifacts/kanban/issue-packet.json --repository mrkillbob/luna-bot --expected-stable-head <full literal HEAD receipt> under the shared control HERMES_HOME. That command independently validates the source, reruns the constrained reproduction, checks identity, revalidates the stable head, and deduplicates before writing. Never use raw gh issue create or load/forward a token. If it rejects, record the exact bounded reason without bypassing it.
After a created or reused verified issue, follow the existing bounded repair -> independent review -> publish Kanban contract with stable idempotency and exact issue/head/command identities. Do not merge or activate anything. Reserve the last two minutes for a factual terminal receipt.
"""

def active_intake(tasks: list[dict]) -> dict | None:
    return next((task for task in tasks
                 if task.get("assignee") == PROFILE
                 and task.get("title") == TITLE
                 and task.get("status") not in {"done", "archived"}), None)

def create_argv(hermes: str, project: str, now: float) -> list[str]:
    if not project or any(char.isspace() for char in project):
        raise ValueError("an unambiguous existing project id is required")
    return [hermes, "kanban", "--board", BOARD, "create", TITLE,
            "--body", BODY, "--assignee", PROFILE, "--project", project,
            "--idempotency-key", f"lunabot-failure-scan:{int(now) // 14400}",
            "--max-runtime", "12m", "--max-retries", "2",
            "--model", "meituan/longcat-2.5-preview:free", "--provider", "nous",
            "--reasoning", "none", "--created-by", "lunabot-failure-scan", "--json"]

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes", default="hermes")
    parser.add_argument("--project", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    command = create_argv(args.hermes, args.project, time.time())
    if args.dry_run:
        print(json.dumps({"status": "validated", "board": BOARD, "profile": PROFILE,
                          "project": args.project, "no_agent": True, "github_write": False}))
        return 0
    result = subprocess.run([args.hermes, "kanban", "--board", BOARD, "list", "--json"],
                            capture_output=True, text=True, timeout=60, check=True)
    tasks = json.loads(result.stdout)
    if isinstance(tasks, dict):
        tasks = tasks["tasks"]
    if not isinstance(tasks, list):
        raise ValueError("invalid board census")
    existing = active_intake(tasks)
    if existing:
        print(json.dumps({"status": "reused", "task_id": existing["id"],
                          "task_status": existing["status"], "board": BOARD}))
        return 0
    result = subprocess.run(command, capture_output=True, text=True, timeout=90, check=True)
    created = json.loads(result.stdout)
    task_id = created.get("task_id") or created.get("id")
    if not task_id and isinstance(created.get("task"), dict):
        task_id = created["task"].get("id")
    if not task_id:
        raise ValueError("task creation returned no identity")
    print(json.dumps({"status": "queued", "task_id": task_id, "board": BOARD, "profile": PROFILE}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

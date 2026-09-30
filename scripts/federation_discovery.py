"""Bounded departmental discovery using existing Hermes profiles and Kanban claims."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import subprocess
import tempfile
import sqlite3
import sys
from collections import defaultdict


def plan_discovery(spec, tasks, day, links=()):
    children = defaultdict(set)
    for parent, child in links:
        children[parent].add(child)
    owned = defaultdict(set)
    for task in tasks:
        creator = str(task.get('created_by', ''))
        if not creator.startswith('federation-discovery-') or not task.get('id'):
            continue
        pending = [task['id']]
        while pending:
            task_id = pending.pop()
            if task_id in owned[creator]:
                continue
            owned[creator].add(task_id)
            pending.extend(children[task_id])
    federation_ids = set().union(*owned.values()) if owned else set()
    active = [task for task in tasks if task.get('status') not in {'done', 'archived'}]
    federation_active = [task for task in active if task.get('id') in federation_ids or str(task.get('created_by', '')).startswith('federation-discovery-')]
    capacity = max(0, spec['max_active'] - len(federation_active))
    planned = []
    for department in spec['departments']:
        creator = 'federation-discovery-' + department['id']
        key = creator + '-' + day
        title = '[Federation] ' + department['title'] + ' ' + day
        already_created = any(task.get('created_by') == creator and task.get('title') == title for task in tasks)
        active_children = [task['id'] for task in active if task.get('id') in owned[creator] and task.get('created_by') != creator]
        if len(active_children) >= 2 or already_created or any(task.get('created_by') == creator or task.get('assignee') == department['assignee'] for task in active):
            continue
        if len(planned) >= min(capacity, spec['max_dispatches']):
            break
        board = spec.get('project_boards', {}).get(department.get('project'), spec.get('board'))
        planned.append(dict(department, board=board, creator=creator, key=key, task_title=title, active_children=active_children))
    return planned


def run(hermes, *args):
    result = subprocess.run(
        [hermes, *args], check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    return json.loads(result.stdout)



def _safe_identifier(value):
    return (
        value
        if isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value)
        else None
    )


def seed_discovery_evidence(
    source: Path, workspace: Path, department: dict, tasks: list
) -> None:
    """Seed bounded structural facts; private source/config payloads stay local."""
    packet = {
        "schema": "discovery_evidence_v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "department": _safe_identifier(department.get("id")),
        "board": _safe_identifier(department.get("board")),
        "assigned_role": {
            "name": (
                _safe_identifier(department.get("assignee")) or "unverified"
            ).replace("-", " ")
        },
        "external_evidence_verified": False,
    }
    try:
        result = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        head = result.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            raise ValueError("invalid revision shape")
        packet["source_revision"] = head
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        packet["source_revision_unverified"] = "revision unavailable"
    try:
        registry = source / "configs/federation/roles.json"
        with registry.open("rb") as stream:
            raw = stream.read(131_073)
        if len(raw) > 131_072:
            raise ValueError("role registry exceeds metadata budget")
        data = json.loads(raw)

        def find_role(value):
            if isinstance(value, dict):
                if value.get("id") == department.get("assignee") or department.get(
                    "assignee"
                ) in value.get("profile_aliases", []):
                    return value
                for child in value.values():
                    match = find_role(child)
                    if match is not None:
                        return match
            elif isinstance(value, list):
                for child in value:
                    match = find_role(child)
                    if match is not None:
                        return match
            return None

        role = find_role(data)
        if role is None:
            raise ValueError("assigned role is not registered")
        for key in ("authority", "schedule"):
            value = _safe_identifier(role.get(key))
            if value:
                packet["assigned_role"][key] = value
        packet["assigned_role"]["handoffs"] = [
            value.replace("-", " ")
            for value in (
                _safe_identifier(item) for item in role.get("handoffs", [])[:8]
            )
            if value
        ]
    except (OSError, ValueError, TypeError) as error:
        packet["assigned_role"]["unverified"] = "registry unavailable"
    relevant = [
        task
        for task in tasks
        if task.get("_discovery_board", department.get("board"))
        == department.get("board")
    ]
    relevant.sort(
        key=lambda task: (
            task.get("created_at", 0)
            if isinstance(task.get("created_at", 0), (int, float))
            else 0
        ),
        reverse=True,
    )
    census = []
    seen = set()
    for task in relevant:
        task_id = task.get("id")
        if (
            not isinstance(task_id, str)
            or not re.fullmatch(r"t_[0-9a-f]{8}", task_id)
            or task_id in seen
        ):
            continue
        seen.add(task_id)
        row = {"id": task_id}
        for key in ("status", "assignee"):
            value = _safe_identifier(task.get(key))
            if value:
                row[key] = value.replace("-", " ")
        census.append(row)
        if len(census) == 20:
            break
    packet["task_census"] = census
    packet["census_complete"] = len(seen) == len(relevant) and len(relevant) <= 20
    text = json.dumps(packet, indent=2)
    if len(text.encode("utf-8")) > 10_240:
        raise ValueError("discovery evidence exceeds metadata budget")
    (workspace / "discovery-evidence.json").write_text(text, encoding="utf-8")


def discovery_body(spec: dict, department: dict) -> str:
    """Relative evidence takes precedence over stale source-location instructions."""
    instructions = (
        spec["instructions"]
        .replace(
            "Read the project's current AGENTS.md, role registry configs/federation/roles.json in the canonical Hermes source, and your role profile before work.",
            "Use discovery-evidence.json and your assigned profile instructions.",
        )
        .replace(
            "then run exact file existence or symbol queries for its unresolved evidence",
            "then inspect only explicitly admitted relative artifacts for its unresolved evidence",
        )
        .replace(
            "Inspect existing Kanban cards across all assignees, open/draft PRs, owned worktrees and current project evidence.",
            "Use the bounded local task census; external source, PR, profile and worktree payloads are unadmitted unless explicitly handed off.",
        )
    )
    return (
        "Read discovery-evidence.json in this assigned directory first. The producer seeded "
        "only source revision, bounded role authority/handoffs and the latest twenty task identities/statuses. "
        "This directory need not be a Git repository; do not run Git, pwd/env probes, search other "
        "checkouts, or guess private paths. Your assigned profile supplies the role instructions. "
        "No external repository, PR, runtime or raw log evidence was admitted here. Only inspect "
        "explicitly admitted relative artifacts. If none establishes a current finding, complete IDLE "
        "with the exact evidence gap and no children. Preserve all existing budgets, child ownership, "
        "publication, broker and approval gates. Reserve the final two minutes for a terminal result.\n\n"
        + instructions
        + "\nExisting active children: "
        + ", ".join(department["active_children"])
    )
def seed_revenue_evidence(source: Path, workspace: Path) -> None:
    """Keep bounded readiness facts in the scratch workspace, not private payloads."""
    packet = {"observed_at": datetime.now(timezone.utc).isoformat(), "configs": {}}
    for name in ("model_routing_policy", "cron_fleet"):
        config = source / "config" / (name + ".json")
        try:
            payload = json.loads(config.read_text(encoding="utf-8-sig"))
            if name == "model_routing_policy":
                facts = {tier: {"status": value.get("status")}
                         for tier, value in payload["tiers"].items()}
            else:
                facts = {key: {"enabled": value.get("enabled"), "tier": value.get("tier")}
                         for key, value in payload["jobs"].items()}
            packet["configs"][name] = {"facts": facts}
        except (OSError, ValueError, KeyError, TypeError) as error:
            packet["configs"][name] = {"unverified": type(error).__name__}
    packet["guarded_receipts_verified"] = False
    packet["instructions"] = (
        "Read this bounded readiness snapshot first. Unavailable tiers are intentional gates. "
        "It is not benchmark promotion, demand evidence, or permission to execute workloads. "
        "Without a current guarded receipt and a reproduced defect, finish IDLE, name the "
        "evidence gap, and create no child. Do not search unrelated worktrees or raw logs. "
        "Reserve the final two minutes for a terminal Kanban result."
    )
    (workspace / "readiness-evidence.json").write_text(json.dumps(packet, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    spec = json.loads((root / 'configs/federation/discovery.json').read_text(encoding="utf-8-sig"))
    tasks = []
    links = []
    boards = dict.fromkeys([spec['board'], *spec.get('project_boards', {}).values()])
    for board in boards:
        tasks.extend({**task, '_discovery_board': board} for task in run(args.hermes, 'kanban', '--board', board, 'list', '--json'))
    day = datetime.now(timezone.utc).date().isoformat()
    sys.path.insert(0, str(root))
    from hermes_cli.kanban_db import kanban_db_path
    for board in boards:
        connection = sqlite3.connect(kanban_db_path(board).as_uri() + '?mode=ro', uri=True)
        try:
            links.extend(connection.execute('SELECT parent_id,child_id FROM task_links').fetchall())
        finally:
            connection.close()
    plan = plan_discovery(spec, tasks, day, links)
    if not args.apply:
        print(json.dumps({'status': 'planned', 'departments': plan}))
        return
    receipts = []
    for department in plan:
        body = discovery_body(spec, department)
        workspace = tempfile.mkdtemp(prefix='hermes-discovery-' + department['id'] + '-')
        seed_discovery_evidence(root, Path(workspace), department, tasks)
        if department['id'] == 'revenue_lab' and department.get('evidence_root'):
            seed_revenue_evidence(Path(department['evidence_root']), Path(workspace))
            body = (
                'Run a read-only revenue readiness audit from readiness-evidence.json in this '
                'workspace. The producer has collected only tier and job availability facts. '
                'Do not load raw profile logs, private opportunity payloads, or unrelated project '
                'trees. Unavailable tiers are intentional; do not bypass benchmark, governor, '
                'compliance or approval gates. Admit a child only for a reproduced defect with '
                'current guarded evidence, and reuse an existing owner. If that evidence is '
                'absent, complete IDLE with the exact gap and no children. Spend at most five '
                'minutes on evidence and reserve two minutes for kanban_complete. No workloads, '
                'publication, spending, or outside contact are authorized by this discovery task.'
            )
        result = run(args.hermes, 'kanban', '--board', department['board'], 'create',
                     department['task_title'],
                     '--body', body, '--assignee', department['assignee'],
                     '--project', department['project'], '--workspace', 'dir:' + workspace,
                     '--idempotency-key', department['key'], '--max-runtime', '15m',
                     '--max-retries', '2', '--created-by', department['creator'], '--json')
        receipts.append({'department': department['id'], 'task': result})
    if receipts:
        print(json.dumps({'status': 'dispatched', 'receipts': receipts}))


if __name__ == '__main__':
    main()

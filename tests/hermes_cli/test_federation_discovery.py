from scripts.federation_discovery import plan_discovery


def test_discovery_respects_existing_ownership_capacity_and_daily_identity():
    departments = [dict(id=str(i), assignee='role-' + str(i), title='Discovery ' + str(i)) for i in range(5)]
    spec = dict(departments=departments, max_active=3, max_dispatches=2)
    tasks = [dict(status='blocked', assignee='role-0', created_by='federation-discovery-0'),
             dict(status='done', created_by='federation-discovery-1', title='[Federation] Discovery 1 2026-09-05')]
    plan = plan_discovery(spec, tasks, '2026-09-05')
    assert [item['id'] for item in plan] == ['2', '3']
    tasks += [dict(status='ready', assignee=item['assignee'], created_by=item['creator'],
                   idempotency_key=item['key']) for item in plan]
    assert plan_discovery(spec, tasks, '2026-09-05') == []


def test_completed_department_can_seek_new_work_on_next_day():
    spec = dict(departments=[dict(id='arts', assignee='arts-director', title='Art discovery')], max_active=2, max_dispatches=1)
    task = dict(status='done', assignee='arts-director', created_by='federation-discovery-arts',
                idempotency_key='federation-discovery-arts-2026-09-04')
    assert len(plan_discovery(spec, [task], '2026-09-05')) == 1


def test_active_descendants_count_even_when_worker_authors_differ_from_root():
    spec = dict(departments=[dict(id='arts', assignee='arts-director', title='Art discovery')], max_active=2, max_dispatches=1)
    tasks = [dict(id='parent', status='done', created_by='federation-discovery-arts'),
             dict(id='child', status='done', created_by='arts-director'),
             dict(id='grandchild', status='blocked', created_by='image-generator'),
             dict(id='other', status='ready', created_by='concept-artist')]
    links = [('parent', 'child'), ('child', 'grandchild'), ('parent', 'other')]
    assert plan_discovery(spec, tasks, '2026-09-06', links) == []
    tasks[-1]['status'] = 'done'
    plan = plan_discovery(spec, tasks, '2026-09-06', links)
    assert plan[0]['active_children'] == ['grandchild']


def test_department_routes_to_its_project_board_and_keeps_cross_board_ownership():
    spec = dict(board="legacy", project_boards={"city": "hermes", "trading": "lunabot"},
                departments=[dict(id="arts", assignee="artist", title="Art", project="city"),
                             dict(id="data", assignee="engineer", title="Data", project="trading")],
                max_active=4, max_dispatches=2)
    planned = plan_discovery(spec, [], "2026-09-23")
    assert {item["project"]: item["board"] for item in planned} == spec["project_boards"]
    existing = [dict(id="old", status="blocked", created_by="federation-discovery-arts", assignee="artist")]
    assert [item["project"] for item in plan_discovery(spec, existing, "2026-09-23")] == ["trading"]


def test_revenue_snapshot_excludes_payloads_and_preserves_unavailable_tiers(tmp_path):
    import json
    from scripts.federation_discovery import seed_revenue_evidence

    source = tmp_path / "source"
    config = source / "config"
    config.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (config / "model_routing_policy.json").write_text(json.dumps({
        "tiers": {"coding": {"status": "unavailable", "token": "PRIVATE_PAYLOAD"}}
    }))
    (config / "cron_fleet.json").write_text(json.dumps({
        "jobs": {"coding": {"enabled": False, "tier": "coding", "prompt": "PRIVATE_PAYLOAD"}}
    }))
    seed_revenue_evidence(source, workspace)
    text = (workspace / "readiness-evidence.json").read_text()
    packet = json.loads(text)
    assert packet["configs"]["model_routing_policy"]["facts"]["coding"]["status"] == "unavailable"
    assert packet["configs"]["cron_fleet"]["facts"]["coding"]["enabled"] is False
    assert packet["guarded_receipts_verified"] is False
    assert "PRIVATE_PAYLOAD" not in text
    assert str(source) not in text



def test_discovery_dispatch_seeds_relative_metadata_without_private_source_probes(
    tmp_path, monkeypatch
):
    import json
    import sqlite3
    from pathlib import Path
    import scripts.federation_discovery as discovery
    from hermes_cli import kanban_db
    from agent.llm_egress_firewall import content_free_violation_locations

    root = tmp_path / "source"
    config = root / "configs/federation"
    config.mkdir(parents=True)
    spec = {
        "board": "default",
        "project_boards": {"hermes-agent": "default"},
        "max_active": 2,
        "max_dispatches": 1,
        "instructions": "Use a 10-minute discovery budget within the 15-minute worker limit. Keep at most two active children; count blocked children. Do not merge, deploy, spend, publish, or install dependencies.",
        "departments": [
            {
                "id": "engineering",
                "assignee": "architecture-steward",
                "project": "hermes-agent",
                "title": "Engineering",
                "brief": "Read /Users/private/source/AGENTS.md and inspect private payloads.",
            }
        ],
    }
    (config / "discovery.json").write_text(json.dumps(spec))
    (config / "roles.json").write_text(
        json.dumps({
            "departments": [
                {
                    "roles": [
                        {
                            "id": "architecture-steward",
                            "authority": "advisory",
                            "schedule": "on_demand",
                            "handoffs": ["coding-expert"],
                        }
                    ]
                }
            ]
        })
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = tmp_path / "board.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    connection.close()
    tasks = [
        {
            "id": f"t_{i:08x}",
            "status": "done",
            "assignee": "coding-expert",
            "created_at": i,
            "body": "PRIVATE_PAYLOAD " + "c2VjcmV0" * 20,
            "workspace_path": "/Users/private/checkout",
        }
        for i in range(35)
    ]
    created = []

    def command(_hermes, *argv):
        if "list" in argv:
            return tasks
        created.append(argv)
        return {"id": "t_ffffffff"}

    monkeypatch.setattr(
        discovery, "__file__", str(root / "scripts/federation_discovery.py")
    )
    monkeypatch.setattr(discovery, "run", command)
    monkeypatch.setattr(discovery.tempfile, "mkdtemp", lambda **_: str(workspace))
    monkeypatch.setattr(kanban_db, "kanban_db_path", lambda _: db)
    monkeypatch.setattr(
        discovery.sys,
        "argv",
        ["federation_discovery.py", "--hermes", "fake", "--apply"],
    )
    discovery.main()
    argv = created[0]
    body = argv[argv.index("--body") + 1]
    assert "/Users/" not in body and str(root) not in body
    assert "discovery-evidence.json" in body and "IDLE" in body
    assert (
        "10-minute" in body
        and "two active children" in body
        and "blocked children" in body
    )
    assert argv[argv.index("--workspace") + 1] == "dir:" + str(workspace)
    assert argv[argv.index("--max-runtime") + 1] == "15m"
    packet_text = (workspace / "discovery-evidence.json").read_text()
    packet = json.loads(packet_text)
    assert len(packet["task_census"]) == 20
    assert packet["assigned_role"]["name"] == "architecture steward"
    assert packet["assigned_role"]["authority"] == "advisory"
    assert packet["external_evidence_verified"] is False
    assert "PRIVATE_PAYLOAD" not in packet_text and "/Users/" not in packet_text
    assert not list(content_free_violation_locations(packet))


def test_missing_discovery_registry_is_an_explicit_idle_evidence_gap(tmp_path):
    import json
    from scripts.federation_discovery import discovery_body, seed_discovery_evidence

    source = tmp_path / "missing-source"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    seed_discovery_evidence(
        source,
        workspace,
        {"id": "engineering", "assignee": "architecture-steward", "board": "default"},
        [],
    )
    packet = json.loads((workspace / "discovery-evidence.json").read_text())
    assert packet["assigned_role"]["unverified"] == "registry unavailable"
    assert packet["external_evidence_verified"] is False
    assert "IDLE" in discovery_body(
        {"instructions": "Preserve budgets."}, {"active_children": []}
    )
    assert str(source) not in json.dumps(packet)

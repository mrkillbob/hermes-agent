from scripts.lunabot_issue_intake import BOARD, PROFILE, TITLE, active_intake, create_argv

def test_intake_requires_luna_board_project_and_verified_worker_contract():
    argv = create_argv("hermes", "p_luna", 28800)
    assert argv[argv.index("--board") + 1] == BOARD
    assert argv[argv.index("--project") + 1] == "p_luna"
    assert argv[argv.index("--assignee") + 1] == PROFILE
    assert "publish-issue --packet" in argv[argv.index("--body") + 1]
    assert "Never use raw gh issue create" in argv[argv.index("--body") + 1]

def test_active_intake_is_reused_across_schedule_windows():
    task = {"id": "t_current", "title": TITLE, "assignee": PROFILE, "status": "blocked"}
    assert active_intake([task]) is task
    assert active_intake([dict(task, status="done")]) is None
    assert active_intake([dict(task, title="Other work")]) is None

def test_intake_window_is_idempotent_and_project_is_required():
    import pytest
    first = create_argv("hermes", "p_luna", 28801)
    second = create_argv("hermes", "p_luna", 28899)
    assert first[first.index("--idempotency-key") + 1] == second[second.index("--idempotency-key") + 1]
    with pytest.raises(ValueError):
        create_argv("hermes", "", 0)

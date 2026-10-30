from pathlib import Path

import pytest


@pytest.mark.parametrize("operation", ["create", "reconcile"])
def test_pr_feedback_workspace_binding_keeps_private_path_out_of_body(operation) -> None:
    from github_pr_feedback.cli import _kanban_create_argv, _kanban_reconcile_argv
    from github_pr_feedback.controller import KanbanTask

    task = KanbanTask(
        title="GitHub PR feedback: acme/widgets#17",
        instructions="Run the bounded repair.",
        board="tradingbot-burndown",
        assignee="hermes-maintenance-steward",
        repository_path=Path("/Users/example/.hermes/github-pr-feedback/worktrees/receipt"),
        head_sha="a" * 40,
        branch="hermes/github-pr-feedback/receipt",
        idempotency_key="feedback:test",
        evidence={"repository": "acme/widgets", "pr_number": 17},
    )

    argv = (_kanban_create_argv(task) if operation == "create"
            else _kanban_reconcile_argv("task-1", task))
    body = argv[argv.index("--body") + 1]

    workspace_flag = "--workspace" if operation == "create" else "--workspace-path"
    expected_path = f"dir:{task.repository_path}" if operation == "create" else str(task.repository_path)
    assert argv[argv.index(workspace_flag) + 1] == expected_path
    assert task.instructions in body
    assert str(task.repository_path) not in body

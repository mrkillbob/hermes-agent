"""Governed exact-head repair push command."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

from hermes_cli.github_identity import GitHubAutomationIdentity, GitHubIdentityError

from .cli_audit_task import owns_current_audit_task
from .git_stack import GitStackError, GitStackRunner
from .github_client import GitHubClientError
from .ledger import FeedbackLedger


def push_head(ctx, args) -> int:
    """Push one exact local PR head through a bound audit task."""

    # These imports are intentionally late: the CLI facade owns policy loading
    # and the authenticated client factory, while this sibling owns the push
    # operation and its privileged boundary.
    from .cli import _FULL_SHA, _github_client, _load_policy_from_context

    try:
        policy = _load_policy_from_context(ctx)
        if not policy.enabled or args.repository not in policy.targets:
            raise ValueError("repository is not a configured target")
        if not isinstance(args.head_sha, str) or not _FULL_SHA.fullmatch(args.head_sha):
            raise ValueError("head_sha must be a full hexadecimal SHA")
        settings = policy.github_identity
        if settings is None:
            raise GitHubClientError(
                "Hermes GitHub automation identity is not configured",
                code="automation_identity_not_configured",
            )
        task_id = os.environ.get("HERMES_KANBAN_TASK", "").strip()
        if not task_id:
            raise ValueError("push-head requires a bound audit task")
        github = _github_client(policy)
        pull_request = github.get_pull_request(args.repository, args.pr_number)
        admission = policy.admit_pull_request(pull_request)
        if not admission.admitted or admission.target is None:
            raise ValueError(
                f"pull request is not admitted: {admission.reason or 'unknown reason'}"
            )
        audit_identity = SimpleNamespace(
            repository=args.repository,
            pr_number=args.pr_number,
            head_sha=pull_request.head_sha,
        )
        ledger = FeedbackLedger.for_current_profile()
        try:
            if not owns_current_audit_task(ledger, audit_identity):
                raise ValueError("push-head is not bound to the current audit task")
        finally:
            ledger.close()
        expected_head_sha = args.head_sha.casefold()
        runner = GitStackRunner(args.worktree)
        if pull_request.head_sha != expected_head_sha:
            local_head_sha = runner.head_sha()
            if pull_request.head_sha == local_head_sha.casefold():
                observed = pull_request
            else:
                print(json.dumps({
                    "status": "stale_head",
                    "repository": args.repository,
                    "pr_number": args.pr_number,
                    "expected_head_sha": expected_head_sha,
                    "observed_head_sha": pull_request.head_sha,
                }, sort_keys=True))
                return 1
        else:
            if str(pull_request.state or "").strip().upper() != "OPEN":
                raise ValueError("pull request is not open")
            viewer_login = getattr(github, "viewer_login", None)
            if callable(viewer_login) and viewer_login().casefold() != settings.expected_login.casefold():
                raise GitHubClientError(
                    "Hermes GitHub automation identity does not match policy",
                    code="automation_identity_mismatch",
                )
            git_environment = GitHubAutomationIdentity(
                settings.expected_login, settings.token_env
            ).git_command_environment()
            runner = GitStackRunner(args.worktree, environment=git_environment)
            pushed_head_sha = runner.head_sha()
            runner.push_verified_head(
                pull_request.head_repository,
                pull_request.head_ref_name,
                args.head_sha,
            )
            try:
                observed = github.get_pull_request(args.repository, args.pr_number)
            except (GitHubClientError, TypeError, ValueError) as error:
                print(json.dumps({
                    "status": "reconciliation_pending",
                    "repository": args.repository,
                    "pr_number": args.pr_number,
                    "head_sha": pushed_head_sha,
                    "head_repository": pull_request.head_repository,
                    "head_ref_name": pull_request.head_ref_name,
                    "reason": str(error),
                }, sort_keys=True))
                return 1
            if observed.head_sha != pushed_head_sha.casefold():
                print(json.dumps({
                    "status": "reconciliation_pending",
                    "repository": args.repository,
                    "pr_number": args.pr_number,
                    "head_sha": pushed_head_sha,
                    "head_repository": pull_request.head_repository,
                    "head_ref_name": pull_request.head_ref_name,
                    "reason": "pushed head was not confirmed on the pull request",
                }, sort_keys=True))
                return 1
        if str(observed.state or "").strip().upper() != "OPEN":
            raise ValueError("pull request is not open")
    except (GitHubClientError, GitHubIdentityError, GitStackError, TypeError, ValueError) as error:
        print(json.dumps({"status": "push_unavailable", "reason": str(error)}, sort_keys=True))
        return 1
    print(json.dumps({
        "status": "pushed",
        "repository": args.repository,
        "pr_number": args.pr_number,
        "head_sha": observed.head_sha,
        "head_repository": observed.head_repository,
        "head_ref_name": observed.head_ref_name,
    }, sort_keys=True))
    return 0

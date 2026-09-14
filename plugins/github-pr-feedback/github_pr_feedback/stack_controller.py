"""Explicit, serial orchestration for pull-request stacks."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from hermes_cli.github_identity import (
    GitHubAutomationIdentity,
    GitHubIdentityError,
    run_as_github_automation,
)

from .git_stack import GitStackRunner
from .github_client import GitHubClient, GitHubClientError, _automation_gh_config_dir
from .policy import PluginPolicy, codex_review_trigger_comment
from .stack import StackEntry, StackManifest, StackStore

try:
    from hermes_constants import get_default_hermes_root
except ImportError:  # pragma: no cover
    def get_default_hermes_root() -> Path:
        return Path.home() / ".hermes"


def _ordered(entries: tuple[StackEntry, ...], base: str) -> tuple[StackEntry, ...]:
    remaining = list(entries)
    result: list[StackEntry] = []
    parent = base
    while remaining:
        ready = [entry for entry in remaining if entry.base_branch == parent]
        if not ready:
            raise ValueError("stack has no reachable next entry")
        entry = ready[0]
        result.append(entry)
        remaining.remove(entry)
        parent = entry.branch
    return tuple(result)


class StackController:
    def __init__(self, policy: PluginPolicy, *, github: GitHubClient | None = None) -> None:
        self.policy = policy
        if github is None:
            raise ValueError("StackController requires an identity-bound GitHub client")
        self.github = github
        self.store = StackStore(get_default_hermes_root() / "github-pr-feedback" / "stacks")

    def refresh_native(
        self,
        repository: str,
        pr_number: int,
        *,
        repository_path: Path,
    ):
        """Rebase and push a GitHub-native stack through the bot identity.

        GitHub-native stacks are not represented by Hermes' explicit local
        manifests. The official gh-stack extension is the supported headless
        interface for discovering and updating them from a selected PR.
        """

        merge_policy = self.policy.merge_policy_for(repository)
        if merge_policy is None:
            raise ValueError("repository is not configured for merge maintenance")
        if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number < 1:
            raise ValueError("pr_number must be positive")
        identity = self.policy.github_identity
        if identity is None:
            raise ValueError("GitHub automation identity is not configured")
        try:
            github_environment = GitHubAutomationIdentity(
                identity.expected_login, identity.token_env
            ).git_command_environment()
            github_environment["GH_CONFIG_DIR"] = str(_automation_gh_config_dir())
            verified = run_as_github_automation(
                ["gh", "api", "user"],
                identity=GitHubAutomationIdentity(
                    identity.expected_login, identity.token_env
                ),
            )
        except GitHubIdentityError as error:
            raise ValueError("GitHub automation credential is unavailable") from error
        if verified.returncode != 0:
            raise ValueError("GitHub automation identity verification failed")
        runner = GitStackRunner(repository_path, environment=github_environment)
        stack_number = self.github.get_pull_request_stack_number(repository, pr_number)
        if stack_number is None:
            raise GitHubClientError("PR is not part of a GitHub-native stack")
        selector = str(stack_number)
        runner.native_stack_checkout(selector)
        runner.native_stack_rebase()
        runner.native_stack_push()
        current = self.github.get_pull_request(repository, pr_number)
        if current.base_branch != merge_policy.base_branch:
            raise GitHubClientError("native stack PR base branch changed")
        return current

    def create(
        self,
        repository: str,
        stack_id: str,
        base_branch: str,
        entries: tuple[StackEntry, ...],
    ) -> StackManifest:
        merge_policy = self.policy.merge_policy_for(repository)
        if merge_policy is None:
            raise ValueError("repository is not configured for merge maintenance")
        if base_branch != merge_policy.base_branch:
            raise ValueError("stack base must equal the configured merge base")
        manifest = StackManifest(repository, stack_id, base_branch, entries, datetime.now(UTC))
        existing = self.github.list_all_open_pull_requests(repository)
        by_branch = {
            (pull.head_ref_name, pull.base_branch): pull
            for pull in existing
            if pull.head_repository == repository
        }
        created: list[StackEntry] = []
        for entry in _ordered(entries, base_branch):
            pull = by_branch.get((entry.branch, entry.base_branch))
            if pull is None:
                pull = self.github.create_pull_request(
                    repository,
                    head=entry.branch,
                    base=entry.base_branch,
                    title=entry.title,
                    body=entry.body,
                )
            created.append(replace(entry, pr_number=pull.number, head_sha=pull.head_sha))
        saved = replace(manifest, entries=tuple(created), updated_at=datetime.now(UTC))
        self.store.save(saved)
        return saved

    def refresh(
        self,
        repository: str,
        stack_id: str,
        *,
        repository_path: Path,
        accept_rebased_heads: bool = False,
    ) -> StackManifest:
        manifest = self.store.load(repository, stack_id)
        identity = self.policy.github_identity
        if identity is None:
            raise ValueError("GitHub automation identity is not configured")
        try:
            git_environment = GitHubAutomationIdentity(
                identity.expected_login, identity.token_env
            ).git_command_environment()
        except GitHubIdentityError as error:
            raise ValueError("GitHub automation credential is unavailable") from error
        runner = GitStackRunner(repository_path, environment=git_environment)
        entries: list[StackEntry] = []
        for entry in _ordered(manifest.entries, manifest.base_branch):
            head = runner.branch_head(entry.branch)
            if entry.head_sha and head != entry.head_sha:
                if not accept_rebased_heads:
                    raise GitHubClientError(f"remote head changed for {entry.branch}")
                current = self.github.get_pull_request(repository, entry.pr_number or 0)
                if (
                    current.head_ref_name != entry.branch
                    or current.base_branch != entry.base_branch
                    or current.head_sha != head
                ):
                    raise GitHubClientError(f"rebased head identity changed for {entry.branch}")
            if entry.base_branch != manifest.base_branch:
                parent = next(item for item in manifest.entries if item.branch == entry.base_branch)
                parent_state = self.github.get_merge_state(repository, parent.pr_number or 0)
                if parent_state.merged:
                    runner.merge_base_into_branch(entry.branch, manifest.base_branch)
                    runner.push_branch(entry.branch)
                    head = runner.branch_head(entry.branch)
                    self.github.post_issue_comment(
                        repository,
                        entry.pr_number or 0,
                        codex_review_trigger_comment(head),
                    )
                    self.github.update_pull_request_base(
                        repository,
                        entry.pr_number or 0,
                        base=manifest.base_branch,
                        expected_head_sha=head,
                    )
            entries.append(replace(entry, head_sha=head))
        refreshed = replace(manifest, entries=tuple(entries), updated_at=datetime.now(UTC))
        self.store.save(refreshed)
        return refreshed

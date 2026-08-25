# github-pr-feedback

`github-pr-feedback` is a disabled-by-default standalone Hermes plugin. It
reads canonical GitHub pull-request feedback, applies a strict local policy,
and creates exact-head Kanban tasks for admitted feedback. An independent,
explicitly configured lane can also schedule read-only local CI audits for PR
heads when repository GitHub Actions are disabled. A separately opt-in,
deterministic maintainer can merge an exact tested head after all configured
safety gates pass. Models never own merge authority, construct merge argv, or
create passing receipts. The plugin never pushes source branches, approves,
changes GitHub settings, deletes branches, or handles credentials.

## Install and configure

Copy this directory to the profile-owned flat plugin location. The shell
default below is intentional: an unset `HERMES_HOME` resolves to the normal
per-user Hermes profile instead of `/plugins`.

```sh
HERMES_PROFILE_HOME="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HERMES_PROFILE_HOME/plugins"
cp -R /path/to/github-pr-feedback "$HERMES_PROFILE_HOME/plugins/github-pr-feedback"
```

Then opt in explicitly in `$HERMES_PROFILE_HOME/config.yaml`. Values below are
placeholders; use real absolute paths to existing local Git worktrees and
replace every placeholder before setting `enabled: true`.

```yaml
plugins:
  enabled:
    - github-pr-feedback
  entries:
    github-pr-feedback:
      settings:
        enabled: false
        repositories:
          - base_repository: example-owner/example-repository
            head_repository: example-owner/example-repository
            local_path: /absolute/path/to/local/repository
            owner_login: example-owner
            branch_prefixes:
              - codex/
        reviewer_logins:
          - trusted-reviewer
        reviewer_associations: []
        include_self_feedback: false
        include_bot_feedback: false
        auto_dispatch: false
        # Optional exact-head audit lane. It runs only when the canonical
        # repository Actions permission is `enabled: false` and reruns once
        # for each new PR head SHA.
        local_ci_audit:
          enabled: false
          assignee: pr-local-ci-auditor
          post_results: false
          repositories:
            - example-owner/example-repository
        # Optional exact-head repair owner for confirmed conflicts, requested
        # changes, and non-green repository checks. It may repair and push but
        # never merge. Start in report-only mode.
        repair_steward:
          enabled: false
          assignee: pr-repair-steward
          repositories:
            - example-owner/example-repository
          report_only: true
        # Optional deterministic merge owner. Keep report-only enabled until
        # exact-head CI receipts and blocker decisions have been observed.
        merge_maintainer:
          enabled: false
          assignee: pr-merge-maintainer
          repository: example-owner/example-repository
          author_login: example-owner
          base_branch: stable
          merge_methods: [squash, rebase, merge]
          receipt_max_age_seconds: 21600
          report_only: true
          post_merge:
            enabled: false
        not_before: "2026-01-01T00:00:00Z"
        # Fallback when no rule wins uniquely, including ambiguous ties.
        assignee: task-orchestrator
        # Optional, ordered and bounded deterministic routing. Each rule scores
        # one point per distinct term found in the bounded feedback body.
        assignee_rules:
          - assignee: performance-patch-steward
            match_any: [latency, performance, throughput, profiling]
          - assignee: data-authority-patch-steward
            match_any: [market data, option chain, quote, hydration, freshness]
          - assignee: structural-ratchet-steward
            match_any: [structural ratchet, extraction, file size, monolith]
        board: repairs
```

The plugin receives these values only through Hermes's namespaced plugin
context (`plugins.entries.github-pr-feedback.settings`); it does not parse
global YAML itself. GitHub authentication remains the existing local `gh`
authentication. Do not put tokens, private keys, or GitHub secrets in this
configuration.

Run the readiness check before enabling or scanning:

```sh
hermes github-pr-feedback doctor
hermes github-pr-feedback status
hermes github-pr-feedback scan
hermes github-pr-feedback merge-status
```

`scan` is safe to repeat. It records durable receipt state and creates one
Kanban card only for feedback that passes all admission checks. By default the
card starts `blocked`. With the explicit `auto_dispatch: true` opt-in, it starts
`ready` on the deterministically selected specialist profile. Before creating
the card, the plugin synchronously creates or verifies a deterministic linked
Git worktree at the admitted receipt SHA and passes that concrete directory as
`dir:/absolute/path`. If that exact commit is absent locally, or the prepared
worktree `HEAD` differs, the scan fails degraded and never substitutes a local
branch tip. Its body keeps the bounded GitHub text in an explicitly untrusted
JSON evidence envelope.

With `local_ci_audit.enabled: true`, the scan also reads the canonical
`repos/{owner}/{repository}/actions/permissions` endpoint. It creates a ready,
read-only audit card only when `enabled` is exactly `false`; an unavailable or
malformed permission response is degraded and fails closed. The immutable
audit identity includes the PR head SHA, so repeated scans deduplicate the same
head and a later head automatically receives a fresh audit. The worker must
re-read the canonical PR head, use the exact receipt worktree, keep tracked
files unchanged, and run repository-owned governance, hygiene, static,
required test-lane, and changed-frontend checks through the deterministic
`audit-pr` command embedded in the card. It may post one factual result comment
when `post_results: true`, but cannot edit, push, approve, or merge. Only the
typed SQLite receipt produced by `audit-pr` can satisfy a merge gate; task prose
and GitHub comments are informational.

Feedback dispatch and feedback completion are different states. Creating a
Kanban card never clears a merge blocker. After an opted-in repair worker has
verified the exact head, pushed its bounded fix, and posted the factual reply,
the card supplies a fixed `complete-feedback` acknowledgement command. That
command rereads the canonical resolved head and records the action separately;
it cannot create CI or merge receipts.

When `repair_steward.enabled: true`, reconciliation independently rereads each
configured PR's exact head and creates a deduplicated repair card only for a
canonical merge conflict, change request, or non-green repository check. In
report-only mode the card is blocked and cannot write. In active mode it may
normal-merge the configured base into the verified head branch, make the
smallest confirmed repair, run focused tests, push normally, and post factual
evidence. It cannot merge or approve the PR, delete branches, change settings,
force-push, rewrite published history, or weaken tests and safety gates.

When `merge_maintainer.enabled: true`, each reconciliation also evaluates open
PRs from the configured author and same repository. It requires a private
repository, exact base and head identities, an admitted branch prefix, a fresh
passing local-CI receipt for the current lane-manifest digest, clean explicit
mergeability, green GitHub checks when Actions is enabled, no change request,
no unresolved review thread, and no unprocessed admitted feedback. Missing or
unknown evidence blocks. The controller selects the first configured method
that the repository currently enables, binds the command with
`--match-head-commit`, and accepts success only from canonical merged readback.

The `pr-merge-maintainer` Kanban profile is an observability worker. It may
explain deterministic blocker codes, but it cannot edit, push, reply, approve,
merge, change policy, waive a gate, or create receipts. Roll out in stages:
collect CI receipts, use `report_only: true`, enable automatic merging, and only
then separately configure and enable a post-merge hook.

An enabled post-merge hook uses a dedicated clean deployment worktree. It
proves the configured protected runtime is absent, fast-forwards the configured
base branch, runs a fixed package argv, verifies the bundle identity, relaunches
only that bundle, and repeats the runtime census. Merge and deployment receipts
are separate, so a rebuild failure never rewrites merge truth.

In `auto_dispatch` mode, the worker must independently validate the finding,
re-read the canonical PR immediately before any GitHub write, and require that
the head still equals the receipt SHA. A confirmed bounded repair may be
committed, pushed to the verified PR head branch, and followed by a factual PR
reply containing the commit and test evidence. Merge always remains
operator-gated. Without `auto_dispatch`, starting repair work and every GitHub
write remain operator decisions.

Claimed receipts carry a durable lease owner, UTC claim time, and monotonic
lease version. A later scan may reclaim a stale claim only after rereading and
readmitting the canonical PR and feedback. The immutable receipt identity and
idempotency key are reused, so recovery asks Kanban to create-or-get the same
card after a crash or lost response.

`doctor` is read-only. For an enabled configuration it checks the `gh`
executable and authentication, the Hermes executable, configured board and
every fallback/routed/audit assignee, ledger access, and each repository's
linked-worktree capability.
`scan` and `retry` return nonzero with `"status": "degraded"` when canonical
coverage or dispatch is incomplete.

To retry one dispatch failure, supply all five immutable receipt fields; retry
always asks the controller to reread and re-admit canonical GitHub state first.

```sh
hermes github-pr-feedback retry \
  --repository example-owner/example-repository \
  --pr-number 17 \
  --feedback-kind review_comment \
  --feedback-id 123456 \
  --head-sha 0123456789abcdef0123456789abcdef01234567
```

## Cron reconciliation

Cron is reconciliation only; it must not invoke an agent or pass arbitrary CLI
arguments. Resolve and record the Hermes executable while an interactive PATH
is available, then copy the supplied non-agent wrapper under the active profile:

```sh
HERMES_PROFILE_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_EXECUTABLE="$(command -v hermes)"
test -n "$HERMES_EXECUTABLE" && test "${HERMES_EXECUTABLE#/}" != "$HERMES_EXECUTABLE"
mkdir -p "$HERMES_PROFILE_HOME/scripts" "$HERMES_PROFILE_HOME/logs"
cp scripts/github-pr-feedback-scan.py "$HERMES_PROFILE_HOME/scripts/github-pr-feedback-scan.py"
chmod 755 "$HERMES_PROFILE_HOME/scripts/github-pr-feedback-scan.py"
```

For example, set the profile explicitly in the crontab and invoke that fixed
wrapper every five minutes:

```cron
HERMES_HOME=/absolute/path/to/hermes-profile
HERMES_EXECUTABLE=/absolute/path/to/hermes
*/5 * * * * /usr/bin/python3 "$HERMES_HOME/scripts/github-pr-feedback-scan.py" >> "$HERMES_HOME/logs/github-pr-feedback-scan.log" 2>&1
```

The wrapper refuses an unset, relative, missing, or non-executable
`HERMES_EXECUTABLE`, then runs exactly that absolute executable with
`github-pr-feedback scan`. It does not accept arguments, start a model, or
create webhooks. GitHub remains read-only unless the strict merge maintainer is
explicitly enabled and not in report-only mode.

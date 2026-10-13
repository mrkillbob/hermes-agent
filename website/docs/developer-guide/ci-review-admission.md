---
title: CI review admission and resumption
---

This candidate evaluates the canonical **public Code Review and Security Review
producer** and implements bounded resumption. Universal early admission is not
wired yet. Normal PR CI triggers remain intact until the trusted evaluator is
available on the default branch and its hosted metadata access is qualified.

## Exact-head public evidence

The evaluator accepts activity only from immutable GitHub user ID `199175422`
and App ID `1144995`, with the exact `codex-pull-request-review-summary` marker,
known table format, and the matching repository/PR comment identity. Both Code
Review and Security Review must have completed rows for the live head. A Code
Review-only summary remains pending, even if it has a thumbs-up.

Security also requires the observed `codex-security-review:v1` JSON marker with
the full head SHA, repository, PR number and completed status. Abbreviated table
commits are resolved through GitHub's commit API and compared with the full live
head; a prefix match alone is never admission. Missing, ambiguous, superseded,
running, failed or unknown evidence holds. The JSON's merge-gate setting and
severity threshold do not waive this fork's substantive finding guards.

Completion alone does not prove clean. The canonical PR-level `+1` reaction must
have been created strictly after both completion times, the summary update and
the latest current-head review submissions. Any canonical `eyes` reaction holds.
An old retained thumbs-up, another user's reaction, or ambiguous same-second
ordering cannot clear admission. Both activity rows and the security JSON remain
mandatory when no formal review submission exists on a clean pass.

The ordinary review guards remain: code and security submissions retain their
independent latest states, ordered by timestamp and immutable review ID. Neither
pass can revive a dismissed or changes-requested sibling or complete the other's
later explicit request. Unknown completion formats hold. Unresolved canonical
P0/P1/P2 threads block even when outdated; a new push or later clean result does
not resolve them. Standalone actionable canonical review or issue-comment
findings also block, including active findings from earlier heads. They have no
thread-resolution signal and require explicit public resolution evidence before
a future integration can clear them; this candidate conservatively keeps them
held. Unknown finding formats, incomplete pagination and API failures hold. The producer's
descriptive footer is excluded from explicit-request detection.

Observed source fixtures include [PR #225's completed public producer](https://github.com/mrkillbob/hermes-agent/pull/225#issuecomment-6104171691)
with both rows, full-SHA security JSON and a fresh canonical thumbs-up, plus
[PR #236's running Code Review](https://github.com/mrkillbob/hermes-agent/pull/236#issuecomment-6140772688).
The same completed security v1 shape was observed on PRs #217, #220 and #207.
Private reviews outside this public producer cannot be inferred from these records
or used as substitutes for either required public pass.

## Trusted resumption

`Review CI wake-up` preserves unprivileged native review/review-comment events.
The trusted `Review CI resumption` controller consumes `workflow_run`, canonical
PR `issue_comment` creation/edits and a 15-minute scheduled reconciliation. These
controller events load workflow/source from the default branch. It checks out no
PR code, sparsely checks out only trusted `scripts/ci` at depth one, executes
no PR artifacts, and uses existing metadata reads plus
`actions: write` for bounded retry requests. It needs no new secrets or settings.

The scheduled path covers thread resolution, empty native PR associations and
the brief delay between a completion-summary edit and its clean reaction. GitHub
Actions has no review-thread-resolution or reaction trigger. The controller
re-reads producer state, reactions, reviews, findings, live PR head and target run
immediately before each write. New running work or a moved head stops the retry.

Runs must match the exact head, head repository, workflow path and PR association.
A newer run or a healthy active run is left alone. A failed `Review admission`
job requires a full rerun so held downstream lanes can start. Writes serialize
only per PR; each original run receives at most two automatic retries. Metadata
reads are bounded to ten 100-item pages and reconciliation to 200 open PRs.
Exceeding limits fails visibly. Mergeability, aggregate `BLOCKED`, CI failure and
absent CI never determine whether substantive review is clear.

The existing maintainer `Label rerun` workflow and any surviving scheduled/review
writer call the same `label_resumption.py` implementation. This compensates for
GitHub replacing a pending same-group label event, even when substantive review
remains held during bootstrap. There is one label retry implementation and no
second competing retry algorithm. `ci-reviewed` stays independent human
approval and is never added automatically. Legacy label retries remain available
during bootstrap, but do not establish substantive review completion. Native
label additions and completed CI/Docker/Nix events use that trusted helper.
`ci-reviewed` requests failed-only CI retries when the actual label gate failed;
`run-e2e` requests full retries for CI/Docker/Nix. Both modes deduplicate against
the exact latest attempt, irrespective of the waking event kind. A run left active for longer than five minutes is retried in
full after completion only if its latest attempt started before the live label
addition. The attempt API prevents retained original-run timestamps from causing
duplicate retries. Removed labels, moved heads, newer healthy replacements,
missing PR associations and the retry cap suppress writes. Both label and review
writers share the same per-PR concurrency key and never cancel healthy runs.
The unsafe mutable-branch
dispatch fallback was removed: it raced pushes and skipped PR-only supply-chain,
OSV, review-label, history and lockfile guards. No-run/zero-job cases need a
separate qualified PR-context creation path. No controller action dispatches,
cancels, resolves findings, merges or changes settings.

## Rollout and remaining limits

Bootstrap in two stages: first make this trusted producer evaluator/controller
available on the default branch, including the shared label retry helper, and
verify the existing read token's hosted metadata access; then add a required early `Review admission` job before every
PR-controlled lane. Include it in the aggregate gate so a hold cannot become a
green all-skipped run. Preserve classification and every PR source/host/security
guard, and apply substantive admission to the shared label retry helper only
after the producer is qualified. Until that second stage, label compatibility
remains independent and cannot establish substantive completion.
Do not make a candidate PR depend on a script that is not yet on the default branch.

Code-only or stale/missing Security Review records remain blocked under the
both-required policy. Resolving findings cannot manufacture a clean witness: a
fresh qualified clean result may still be required when the previous completed
pass reported findings. Reconciliation can resume on final thread resolution
when both current-head completion records and the fresh witness already exist.
No private task, reaction, label or successful CI substitutes for missing evidence.

Run the inert API fixtures with the canonical explicit interpreter and one worker:
`HERMES_PYTHON="$PWD/.venv/bin/python" scripts/run_tests.sh -j 1 tests/ci/test_review_resumption.py`.
The unchanged PTY-helper regression now lives beside its helper at
`tests/e2e/core/terminal/test_pty_harness.py`; it opens no PTY and launches no Hermes.

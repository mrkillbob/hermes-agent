---
title: CI review admission and resumption
---

The bootstrap candidate provides the canonical public Code/Security evaluator
and bounded resumption. The stacked early-admission candidate additionally wires
every substantive `mrkillbob/hermes-agent` PR entry before PR source, classifiers,
local actions or reusable test lanes execute. Its gates intentionally hold while
trusted default-branch helpers or qualified public Security evidence are missing.
Other repositories and non-PR events retain their existing behavior.

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

## Early admission coverage

Every entry uses a read-only inline job named exactly `Review admission`, so the
resumption controller sees that unprefixed job name. It checks out only the
trusted default branch's `scripts/ci` at depth one, without persisted credentials,
then evaluates the original PR number and full head SHA. It has only
`contents: read`, `pull-requests: read` and `issues: read`; the last scope covers
PR-level reaction reads. No PR code, local action, artifact, environment secret,
inherited secret or App credential participates in admission.

| PR entry | Substantive consumers |
| --- | --- |
| `ci.yaml` | Classifier, all reusable unit/native/E2E/lint/security lanes, human review-label gate and timing report |
| `docker-pr.yml` | Original Docker classifier/build/test jobs via `docker.yml` |
| `nix-pr.yml` | Original Nix classifier and flake checks via `nix.yml` |
| `install-e2e-pr.yml` | Original install/update planners, native matrices and report via `install-e2e.yml` |
| `pm-bundle-pr.yml` | Original native payload matrix via `pm-bundle.yml` |
| `termux-verify-pr.yml` | Original contracts and TUI checks via `termux-verify.yml` |
| `plugin-catalog-ci.yml` | Structural and pinned-source validation |
| `sandbox-image.yml` | Existing source/host-qualified image jobs |
| `windows-bundle-sdk.yml` | Existing native SDK matrix |

The five PR wrappers retain the original filters and default PR inputs; their
children no longer have independent PR triggers. Their called jobs keep the
original read ceilings and inherit no release secrets. Original push, scheduled,
manual and stable-release bodies stay intact. The secret-free stable CI caller
adds only `issues: read` so its inline admission job does not exceed the reusable
caller ceiling; deployment, signing and R2 grants are unchanged.

CI's required aggregate retains its existing check name, waits for admission and
runs a static admission precheck before fetching PR source. Failure, cancellation,
skip, pending, missing and unknown admission all fail that precheck. The shared
aggregate evaluator also receives explicit qualified-PR context and rejects a
missing/skipped admission record; historical main/release contexts remain valid.
Every `always()`/`!cancelled()` source consumer has its own admission dependency
and success condition. Non-policy admission steps are no-ops, so original implicit
success dependencies and upstream behavior are preserved.

GitHub documents both [dependency skip propagation](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idneeds)
and the [implicit success check](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions#status-check-functions).
Reusable workflows retain the [caller's GitHub context and permission ceiling](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations),
which is why release workflows are protected through separate PR wrappers.

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
The live review comment also observes the Docker PR wrapper. Old entries and
their new PR wrappers share one logical lane: the newest qualified
run across both aliases wins, so an active wrapper cannot revive an older entry.
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
second competing retry algorithm. Bootstrap runs without a `Review admission`
job retain their original label behavior. When that job is present, label retries
recheck the same substantive review verdict before every write, holding without
consuming the retry cap until both passes clear. The admission evaluator never
requires the human label, so the two authorities cannot create a cycle.
`ci-reviewed` stays independent human
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

Deliver in two phases: land the independently reviewed bootstrap evaluator and
controllers first (PR #237), with their explicit hosted metadata read permissions;
then land this stacked gate wiring after current-head Code and Security producer
behavior and hosted token reads are qualified. The wiring candidate does depend
on the trusted default-branch helper and intentionally holds while it is absent.
It contains no inline evaluator, exemption, private-review substitute or fail-open
bootstrap switch.

The authenticated settings audit found automatic Security Review disabled. Its
exact human approval to enable it is pending outside this source patch. No setting,
App/OAuth permission, secret or installation is changed here. A Code-only activity
summary or a manually requested Security pass recorded only as Code remains held.
Qualified public Security completion must actually appear before admission clears.

This is a stacked candidate on `18ec23e8a360f511650bbe67b3c107ce039ed88e`. Publishing
it as a draft does not activate default-branch controllers or qualify the producer.
Missing helper/security evidence will keep its PR checks visibly failed/held until
the bootstrap and producer dependencies are met. Existing PR-context runs can then
be retried in full by the trusted controller; no mutable branch dispatch is used.

Code-only or stale/missing Security Review records remain blocked under the
both-required policy. Resolving findings cannot manufacture a clean witness: a
fresh qualified clean result may still be required when the previous completed
pass reported findings. Reconciliation can resume on final thread resolution
when both current-head completion records and the fresh witness already exist.
No private task, reaction, label or successful CI substitutes for missing evidence.

Run the inert API fixtures with the canonical explicit interpreter and one worker:
`HERMES_PYTHON="$PWD/.venv/bin/python" scripts/run_tests.sh -j 1 tests/ci/test_review_resumption.py tests/ci/test_review_admission_graph.py tests/ci/test_required_results.py tests/ci/test_workflow_job_graph.py`.
The unchanged PTY-helper regression now lives beside its helper at
`tests/e2e/core/terminal/test_pty_harness.py`; it opens no PTY and launches no Hermes.

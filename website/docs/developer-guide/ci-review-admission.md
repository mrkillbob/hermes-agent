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
runs a static admission precheck. Failure, cancellation, skip, pending, missing
and unknown admission all fail that precheck. On fork PRs it then fetches only
trusted default-branch `scripts/ci`, without persisted credentials, and checks
live exact-head admission before result evaluation and immediately before
success. It never fetches or executes PR source. Its timing-report dependent
likewise fetches trusted default-branch code, so a metadata-only retry cannot
execute PR code after review becomes held. Both jobs use explicit read scopes.
Other events/repositories continue to evaluate their original source revision.

The shared aggregate evaluator retains explicit qualified-PR support for callers
that need it. This workflow uses its bootstrap-compatible CLI arguments: the
static first step requires actual admission success even if the trusted main
helper predates `--pull-request`. Historical main/release contexts remain valid.
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
A newer run is left alone. A failed `Review admission` job requires a full rerun
so held downstream lanes can start. Writes serialize only per PR; source
resumption remains capped at three source-bearing attempts (the original plus
at most two automatic source retries). Metadata
reads are bounded to ten 100-item pages and reconciliation to 200 open PRs.
Exceeding limits fails visibly. Mergeability, aggregate `BLOCKED`, CI failure and
absent CI never determine whether substantive review is clear.

### Same-head changes after a successful aggregate

`review_aggregate.py` reconciles CI's existing required aggregate separately from
full resumption. If current-head review becomes pending, blocked or unknown
after that aggregate succeeded, it requests a retry of only the completed
`All required checks pass` job. The trusted live check then reports the hold.
Once review clears, it retries only an aggregate whose failed steps were the
live review checks. Source/lint/label failures never authorize this restoration.
Trusted result evaluation runs even after a held first live read, provided its
trusted checkout succeeded, and must actually pass before metadata restoration.
This prevents a metadata retry from consuming a pending maintainer-label retry's
attempt timestamp while its source/label lane never ran again.
Eligible maintainer-label retries retain priority over metadata restoration,
including an unread `run-e2e` addition. A label metadata read failure is reported
visibly while same-head aggregate invalidation can still proceed independently.
After any accepted label retry, that controller invocation stops before another
retry path, even if GitHub still reports the older completed run temporarily.
Persistent held metadata does not retry an already failed aggregate, and queued
or active aggregates are left alone. A completed aggregate may be selected even
while unrelated advisory jobs keep the parent workflow active.

This uses GitHub's documented [specific-job rerun endpoint](https://docs.github.com/en/rest/actions/workflow-runs#re-run-a-job-from-a-workflow-run)
with the existing `actions: write` grant. GitHub reruns that job and its dependents;
the only dependent here is the trusted timing report. The
[attempt-specific jobs endpoint](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt)
pins the current aggregate ID. Bounded job history proves the latest admission
execution actually succeeded, including when subset retries retain that earlier
job. GET/list responses may differ in optional fields; core identity, execution
and step states must match. The PR head, qualified latest run/attempt, job ID,
workflow definition and live review verdict are re-read before each write.

Matching job names alone cannot prove a metadata-only runtime. The controller
requires consistent local `referenced_workflows` records for the original PR
merge revision, verifies that Git commit's exact original base/head parents,
and compares the entire runtime `ci.yaml` blob with the trusted default-branch
blob. This also qualifies the aggregate's dependent definitions and workflow
defaults. Missing/ambiguous runtime records or a different definition hold.
It uses the recorded immutable merge SHA, never today's mutable PR merge ref.

Metadata rechecks are independent of the three-source-attempt resumption cap, so
that resource cap cannot preserve an otherwise eligible stale green result.
GitHub nevertheless [limits all reruns, including subsets](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs)
to 50 retries within 30 days. The controller stops at that platform attempt
limit; API denial is visible and never falls back to full healthy CI.

Both existing source retry writers use `source_attempts.py`. Its bounded reverse
history reads stop after three source attempts or at the 51-attempt platform
limit. An attempt is exempt only when its executed jobs are exactly the aggregate
and optional timing report, their immutable run/head/attempt identities and
live-step contract qualify, and the original whole runtime workflow matches
trusted main. Full runs, failed-only source/label executions, empty or ambiguous
job sets count as source attempts. Missing runtime evidence preserves bootstrap's
original total-attempt policy; malformed attempt identity holds. The most recent
nonexempt attempt's start time anchors unread labels, so a metadata refresh cannot
consume `run-e2e`/`ci-reviewed` or exhaust their source retry budget. Retained
admission history continues to require clean substantive review when a subset
attempt did not rerun the original admission job.

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
the exact latest source-bearing attempt, irrespective of the waking event kind. A run left active for longer than five minutes is retried in
full after completion only if its latest source-bearing attempt started before the live label
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

Before this wiring lands, bootstrap PR #237's older registry lacks the five new
PR wrapper aliases. A wrapper's first held run may therefore need one bounded
manual retry after both review passes and human approval are ready. The stacked
candidate and future PRs editing `ci.yaml` intentionally cannot qualify themselves
for metadata-only post-green retries while their runtime definition differs from
trusted main. Initial and final live admission still apply, but later automatic
invalidation for those definitions is held until the reviewed definition lands
on default branch. Older workflow definitions without the live-check steps
cannot safely refresh a stale result by rerunning themselves.

The documented specific-job endpoint has no parent-run-completed precondition,
but acceptance while advisory jobs remain active still requires hosted
qualification; inert fixtures prove selection behavior, not GitHub server
acceptance. A denial, platform age/cap, missing runtime binding or unavailable
target metadata can leave an earlier green check unchanged. Native events,
15-minute polling, queueing and final metadata reads also leave asynchronous
windows. GitHub provides no atomic review-read-to-job-rerun precondition here.
This source patch is not atomic merge arbitration: a maintainer must still read
current-head review metadata immediately before a normal merge. An independent
required-check authority would need a separately reviewed permission/rollout
decision if those finite API and timing limits must be removed.

Code-only or stale/missing Security Review records remain blocked under the
both-required policy. Resolving findings cannot manufacture a clean witness: a
fresh qualified clean result may still be required when the previous completed
pass reported findings. Reconciliation can resume on final thread resolution
when both current-head completion records and the fresh witness already exist.
No private task, reaction, label or successful CI substitutes for missing evidence.

Run the inert API fixtures with the canonical explicit interpreter and one worker:
`HERMES_PYTHON="$PWD/.venv/bin/python" scripts/run_tests.sh -j 1 tests/ci/test_review_resumption.py tests/ci/test_review_aggregate.py tests/ci/test_review_admission_cli.py tests/ci/test_required_results.py`.
The admission CLI suite executes the production evaluator and controller with an
inert GitHub transport. It does not parse workflow source or emulate GitHub's
expression engine. Dependency, trigger, permission, checkout and concurrency
wiring require source review and actual hosted qualification; these local CLI
contracts do not establish that hosted wiring is correct.
The unchanged PTY-helper regression now lives beside its helper at
`tests/e2e/core/terminal/test_pty_harness.py`; it opens no PTY and launches no Hermes.

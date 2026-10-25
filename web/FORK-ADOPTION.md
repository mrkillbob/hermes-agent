# Dashboard polling source provenance

Adapted selectively from [affaan-m/hermes-agent commit
3825f581674202dee5624ed815a6361f412e8c0c](https://github.com/affaan-m/hermes-agent/commit/3825f581674202dee5624ed815a6361f412e8c0c),
“fix(dashboard): scope gateway and recent-session status to selected profile”, authored by
Affaan Mustafa (`124439313+affaan-m@users.noreply.github.com`) on 2026-09-15.

The selected slice consists of `useSidebarStatus`, the status polling helper and their
behavior tests, and explicit `api.getStatus(profile)` scoping. The donor's backend, session
count model, translated labels, styles and unrelated changes are excluded. Local adaptation
uses the existing gateway-state field in tests and adds an A→B→A freshness regression and
correction, independently reviewed before acceptance.

Donor license at that commit is MIT, copyright (c) 2025 Nous Research. Its complete notice is
identical to and retained in this repository's root [LICENSE](../LICENSE); that notice applies
to the adapted source as well. No third-party dependencies were added.

Maintained baseline: `mrkillbob/hermes-agent` main,
`11229c795c1911f7e4835c46fe8df41565fe8d2a`. This patch is source-only and does not establish
installed/runtime adoption, deployment, or automation readiness. Full comparison matrix and
local verification receipts are retained in the task-10 handoff outside the source tree.

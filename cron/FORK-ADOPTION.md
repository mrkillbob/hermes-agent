# One-shot ledger safety provenance

Selectively adapted from [ANG-Ventures/hermes-agent
commit da48a9e338e944c17647159596418d609666e8cf](https://github.com/ANG-Ventures/hermes-agent/commit/da48a9e338e944c17647159596418d609666e8cf),
authored by `ang-fleet-workers[bot]`
(`333956806+ang-fleet-workers[bot]@users.noreply.github.com`), 2026-10-24.
The donor is MIT, copyright (c) 2025 Nous Research. Its complete notice is identical
to and retained in this repository's root [LICENSE](../LICENSE).

The adapted behavior distinguishes unreadable completion evidence from an empty ledger.
Scheduled one-shots wait without dispatch, claims, missed diagnostics/metrics, or retirement
while evidence is unreadable. Both the due-scan and fire-claim gates use the existing
occurrences sibling. Maintained source's grace, poison-completion and clock-skew checks remain.

Local compatibility corrections preserve `completed_occurrence`'s False-on-error contract
for cached pre-update schedulers and probe ledger readability for legacy naive one-shot
instants without fabricating an exact completion identity. Explicit manual bypass and the
existing recurring read-error behavior remain. Rewound-watermark frameworks, recurring policy
changes, terminal-settlement machinery and warning-throttle registries are excluded.

Baseline: maintained `mrkillbob/hermes-agent` main at
`20614a29e6826c9b22c6cdb69bd102377250deb8`, including PR402 and PR403. No runtime
or automation activation accompanies this source patch. Offline tests use temporary
JSON/SQLite stores; no new dependencies or services are required.

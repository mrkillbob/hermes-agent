# HRL-5 Revenue Ledger Design

## Scope

The Revenue Ledger is the local accounting authority for experiments. SQLite stores one current
snapshot, immutable change events, raw promotion evidence, and archived kill findings. Money and
rates use exact decimal text; unknown measurements remain SQL `NULL` and Python `None`.

## Accounting

The current snapshot contains every HRL-5 field. Updates use an expected revision so two writers
cannot silently overwrite each other. Every accepted create, update, evidence insertion, promotion,
or archive action appends an immutable audit event in the same transaction.

Derived metrics are computed, not hand-entered. A metric is unknown unless every required input is
known. Zero denominators are undefined rather than coerced to zero. Net revenue subtracts refunds
and transaction/platform fees. Contribution profit additionally subtracts advertising, API, other,
and electricity cost. Profit rate metrics require a positive observed duration.

## Promotion and kill boundaries

The promotion ladder is deterministic and sequential. E1 requires evidence of a real market test;
E2 requires a positive payment by a customer explicitly evidenced as a stranger. E3 and E4 require
cumulative revenue thresholds, and E4 also requires positive contribution profit. E5-E7 require
explicit monthly revenue, labor, recurring revenue, and stable-unit-economics evidence. AI opinion
is not an accepted evidence kind.

Kill decisions are explicit operator actions. Archiving requires bounded reason codes and findings,
preserves all prior accounting/evidence, and records an immutable archive event so failed ideas are
not silently rediscovered.

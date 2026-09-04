# HRL-9 Niche Intelligence/Data Product

## Selected recurring problem

The single experiment is model-year recall-change intelligence for independent used-car dealers.
Recall records change over time, while repeatedly checking a changing mix of years, makes, and
models is manual work. This selection is a product hypothesis, not evidence that dealers will pay.

The output is not VIN-specific, cannot prove that a particular vehicle is affected, and cannot
replace the official NHTSA VIN lookup or a dealer safety process. Every digest remains
`private_draft`; this patch adds no email, alert delivery, customer contact, account creation,
subscription, API service, or publication transport.

## Source permission and collection boundary

Collection uses only NHTSA’s documented HTTPS model-year/make/model endpoint:

```text
https://api.nhtsa.gov/recalls/recallsByVehicle
```

No VIN endpoint is called. Requests use a named user agent, an at-most-30-second timeout, a 5 MB
response bound, an exact `api.nhtsa.gov` redirect boundary, HTTP-success requirement, and JSON
content-type requirement. The catalog marks the recall dataset public and licenses it as a
[U.S. Government Work](https://catalog.data.gov/dataset/recalls-data); the collector also binds the
[NHTSA API use policy](https://api.nhtsa.gov/). The normal production cadence is intended to be a
small daily watchlist, not bulk VIN lookup or rate-limit evasion.

## Deterministic pipeline

```text
collect
→ normalize
→ source
→ dedupe
→ history
→ score
→ package
→ update
```

- Normalize requires the exact documented result schema and exact requested vehicle scope.
- Every packaged record retains source ID, URL, raw response SHA-256, and collection time.
- Exact duplicate rows collapse. Two different records with the same campaign/scope identity fail
  closed before history changes.
- A root-contained mode-`0600` SQLite database retains distinct semantic versions and the current
  version. Re-observing identical content does not fabricate a new version.
- Urgency is a deterministic sorting aid based on explicit NHTSA fields and consequence words. It
  is not a risk determination and never overrides NHTSA.
- The package records added, changed, and unchanged counts and is emitted only as a private draft.
- `publish_update_run` sends the digest, source receipt, query, and update log through HRL-15’s
  checksum-sealed run boundary.

## Monetization hypotheses

The retained modes are one-time CSV, paid report, alert subscription, business subscription, and
eventual API access. Every mode remains explicitly `hypothesis`; no price, demand, revenue, or
customer acceptance is asserted.

## Verification

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_data_experiment.py
PYTHONPATH=src python3 -m pytest -q
```

The focused corpus covers the selected problem, hypothesis status, non-VIN endpoint, bounded
collector, strict schema/scope checks, exact and conflicting duplicates, version history, root and
symlink safety, deterministic scoring, per-record provenance, private packaging, and HRL-15
publication.

A bounded live diagnostic against the official `2012 Acura RDX` model-year endpoint returned two
records and produced a two-item private draft. This proves current endpoint compatibility only. It
does not prove demand, customer value, revenue, VIN applicability, or publication readiness.

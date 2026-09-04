# HRL-11 Product and Report QA

## Required chain

No customer-facing deliverable can move directly from generation to publication:

```text
generator
→ deterministic validation
→ distinct-context model reviewer
→ exact-platform compliance
→ authenticated exact-scope approval
→ publish eligibility
```

`evaluate_publish_eligibility` is the sole HRL-11 decision boundary. The deliverable, deterministic
receipt, and review receipt are bound to the same artifact ID and SHA-256. Compliance is bound to
the deliverable’s exact target platform and the `publish_ai_content` action. Approval is bound to
`publish_first_product_in_category`, the exact artifact target, a canonical request hash, and a
non-null authenticated approval ID.

## Required QA dimensions

Both validation and review contain every dimension exactly once:

- factual claims;
- links;
- calculations;
- duplicated material;
- copyright concerns;
- source attribution;
- hallucinations;
- formatting;
- customer usefulness.

Each result is `pass`, `fail`, or `unknown`. Anything except `pass` blocks publication. Missing or
duplicate dimensions make the receipt invalid rather than merely ineligible.

## Reviewer independence and tiers

The reviewer must use a different context ID from the generator. For low-value work, the current
fast tier can satisfy the structural reviewer role in a fresh context when no independent model is
available; same-context self-review is always blocked. Exact reviewer tier, provider, model, and
digest are retained.

High-value work requires the unscheduled `escalation` tier. That tier has no selected model today,
so a real high-value deliverable necessarily remains blocked. A synthetic unit fixture proves the
gate shape only; it is not evidence that an escalation model exists or passed review.

## Current operational verdict

No actual product/report review was run and no model was loaded for this patch. The repository’s
current Etsy `publish_ai_content` policy is `BLOCK_AND_REVIEW`, and no authenticated first-category
publication approval is installed. Therefore no current deliverable is publish-eligible and no
marketplace action is authorized.

## Verification

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_deliverable_qa.py
PYTHONPATH=src python3 -m pytest -q
```

The focused tests cover a complete low-value chain, missing/failed stages, context independence,
high-value escalation, compliance and approval blocking, platform binding, artifact hash binding,
and exact dimension coverage.

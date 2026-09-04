# HRL-10 Selective Digital Products

## Boundary

This patch treats a marketplace only as a possible future distribution experiment. It does not
create an Etsy or other marketplace account, accept terms, generate listings, publish products,
spend money, or automate marketplace behavior.

Before any product can be selected, `NicheResearch` requires 36–500 unique candidates from the
HRL-7 Digital Product Scout. Every candidate must already pass the deterministic scout gate:
functional narrow utility plus demonstrable demand, with generic AI art rejected.

## Initial portfolio

`build_initial_portfolio` accepts exactly 3–5 products. Each selected candidate must additionally
retain:

- a permitted functional asset type;
- demonstrable-demand evidence;
- observed buyer language;
- an existing paid-alternative observation;
- at least three distinct evidence sources.

Supported functional types include calculators, spreadsheets, business templates, planning tools,
niche references, specialized utilities, professional checklists, and inventory tools. Product
specifications must declare two to 20 deterministic functional requirements. The resulting
portfolio is always `private_prototype` and its marketplace is `None`.

This patch intentionally does not generate the assets. HRL-11 must provide deterministic
validation, independent review, policy/compliance checks, and publish eligibility before any
customer-facing deliverable can exist.

## Demand and scale gate

`ListingMetrics` retains impressions, clicks, favorites, sales, price, total fees, refunds, and
observation time. Unknown values stay `None`; they are not converted to zero. Funnel invariants and
refund bounds are structural. Conversion is exact `sales / impressions`, and net revenue is exact
`price * (sales - refunds) - fees` when all inputs are known.

SKU expansion remains blocked unless every one of the initial 3–5 products has:

- complete funnel and economic observations;
- at least one non-refunded real sale;
- positive conversion;
- positive net revenue.

This is only a scale-eligibility prerequisite. It does not authorize publishing, advertising,
spending, price changes, or additional products; those remain subject to HRL-12 compliance and
HRL-13 approval.

## Evidence status

The unit corpus uses synthetic candidate and metric fixtures to prove the fail-closed contracts.
No real 36-candidate research corpus, product prototype, marketplace impression, click, favorite,
sale, fee, refund, conversion, or willingness-to-pay evidence has been collected. Therefore the
business experiment is implemented but unexercised and cannot advance to a marketplace.

## Verification

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_digital_product_experiment.py
PYTHONPATH=src python3 -m pytest -q
```

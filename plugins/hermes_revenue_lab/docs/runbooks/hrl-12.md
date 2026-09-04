# HRL-12 Compliance Registry Runbook

## Current classification

HRL-12 is **acceptance-valid infrastructure**. It does not authorize any external platform yet.

`config/compliance_registry.json` is the machine-readable policy authority. The initial registry
contains the complete global prohibition set and deliberately contains no external platform
entries. Therefore Etsy, marketplaces, websites, APIs, outreach destinations, and any other
unregistered platform resolve to `BLOCK_AND_REVIEW`.

The evaluator checks, in order:

1. global prohibited behavior;
2. platform registration;
3. platform-specific prohibition;
4. explicit automation permission;
5. policy source and verification date;
6. action-specific policy;
7. declared rate-limit evidence for API or scraping actions.

An allow receipt records the registry checksum, official policy source, verification date,
requirements, and declared rate limits. Missing or unclear evidence never becomes permission.

## Operator check

From `/Users/mikedemott/HermesRevenueLab`:

```bash
PYTHONPATH=src:. .venv/bin/python scripts/check_compliance.py \
  --platform etsy \
  --action publish_ai_content
```

Expected initial result: JSON status `BLOCK_AND_REVIEW`, reason `platform_not_registered`, and exit
code `3`.

Exit codes:

- `0`: explicitly allowed by complete registry evidence;
- `2`: explicitly blocked;
- `3`: blocked pending review because policy is missing or unclear;
- `4`: registry integrity/read failure, also blocked pending review.

## Adding a platform

Do not infer policy from blog posts, model output, or remembered terms. Before adding a platform:

- verify the current official policy source;
- record explicit automation, AI-content, scraping/API, and outreach decisions;
- record an official source URL and `last_verified` date;
- declare numeric rate limits before permitting API or scraping work;
- retain platform-specific prohibited behaviors;
- run the compliance test suite.

Registration is policy evidence, not approval to create an account, accept terms, publish, spend,
or contact customers. Those mutations remain under HRL-13.

## Verification

```bash
PYTHONPATH=src:. PYTEST_ADDOPTS='-p no:cacheprovider' \
  python3 -m pytest -q tests/test_compliance_registry.py
```

No network request, account action, publication, outreach, model call, or spend is performed by
this patch.

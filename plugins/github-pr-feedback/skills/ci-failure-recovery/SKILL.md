---
name: ci-failure-recovery
description: Recover governed exact-head local-CI failures without weakening merge gates.
---

# Governed CI failure recovery

Use this skill only for the configured Hermes `github-pr-feedback` repository.
The typed `ci_audit_receipts` table is authoritative; aggregate feedback counts
are not CI counts.

1. Run `github-pr-feedback doctor` with host filesystem access and continue only
   when it returns `status: ready`.
2. Run `github-pr-feedback status --repository OWNER/REPOSITORY` and classify
   typed CI receipts by `evidence_json.failure_reason` and failed command.
3. Run `github-pr-feedback ci-repair-backlog --repository OWNER/REPOSITORY`.
   This starts from typed failed receipts, verifies live exact heads, and is
   bounded to one receipt by default. Use `audit-backlog` afterward for missing
   receipts; never start a second coordinator while one is in progress.
4. Treat repeated `run_static_lane.py` or `run_hygiene_lane.py` failures as a
   shared lane-repair backlog. Route each current, admitted exact head to the
   configured typed fixer profile. Do not blindly retry a logic-regression
   receipt, manufacture a pass, or mark a Kanban task complete from prose.
5. A fixer must reproduce the literal failed command with the receipt's exact
   base SHA, make the smallest source change, rerun the required lane, and
   leave a new exact-head passing receipt. Base/head drift invalidates the old
   receipt and requires a fresh audit.
6. Only after the passing receipt is durable, run the governed `merge-scan`.
   Never use `gh pr merge`, bypass protected checks, or alter CI configuration
   merely to obtain a passing receipt.

Common classifications:

- `logic-regression`: route to the lane-specific fixer (`ci-static-fixer`,
  `ci-hygiene-fixer`, or `ci-test-fixer`), then require exact-head revalidation.
- `structural-ratchet`: route to `structural-ratchet-steward`.
- `worktree dirty`, bootstrap, identity, provider, or mergeability errors:
  classify as environment/control-plane recovery and retry only after the
  underlying condition is verified resolved.
- `action_required`, permission denial, or connector authorization: escalate
  to the human/operator; do not represent it as a code fix.

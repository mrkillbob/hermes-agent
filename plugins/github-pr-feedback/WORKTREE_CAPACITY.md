# Shared feedback worktree capacity admission

Production feedback scan, repair, release maintenance and review allocators share `CONTROL_HOME/state/worktree-capacity.sqlite3`; per-profile feedback ledgers do not define separate budgets. Pool overflow inherits the same admission object. Reconciliation-only pool instances do not create workspaces.

The new default policy holds 8 GiB per physical workspace and preserves 2 GiB free space. These conservative code-configured estimates are future-work headroom, not measured unique APFS bytes. All reservation phases remain charged. Failed/partial operations remain uncertain, and unknown legacy trees remain protected. No cleanup, expiry or automatic adoption occurs. Existing unknown slots therefore require positive owner reconciliation before reuse; changing a ledger lease alone does not establish storage ownership.

Reservation checks run before Git fetch, branch or worktree creation. The SQLite write transaction ends before Git/provisioning begins; admitted independent paths can run concurrently. Exact-path operations and conservative same-device Unicode/case aliases are fenced. On case-sensitive filesystems an alias can be rejected conservatively. Capacity rejection is distinct from pool exhaustion and cannot invoke unbounded overflow or main-clone review fallback.

Low-level repository injection is an adapter/test seam. All managed production constructors explicitly inject canonical admission. Uncontrolled and old runtime writers are not fenced by this source change; coordinated rollout and verified source loading are required before claiming containment. Native Windows reparse and alias behavior needs actual Windows test evidence.

## Fresh-first selection of protected pools

Before claiming a pooled slot, the allocator checks only canonical filesystem metadata and the shared registry. An existing directory with no registry row is preserved and skipped, without Git inspection, lease claim or adoption. Later slots in the same configured source namespace remain reachable. No namespace/key remapping is performed.

This read-only hint does not authorize allocation: the selected candidate still requires atomic `reserve()` before Git or provisioning. Concurrent creation, capacity pressure, registered uncertain/allocating state, aliases, unavailable metadata and unknown registry schemas remain hard rejections. All protected/leased slots may reach the existing bounded overflow path, whose reservation uses the same canonical budget. Existing legacy work is never reconciled implicitly.

This source repair requires coordinated source loading and writer containment for live acceptance; it does not fence older or uncontrolled writers.

# Cooperative workspace retirement: source API only

This plugin owns reusable feedback workspaces. They are not native ConversationWorktree
records. `pool_doctor` remains read-only and does not authorize an action.

The unregistered, trusted in-process API is in `github_pr_feedback.worktree_retirement`:
`observe_identity`, `prepare_retirement`, and `retire_to_quarantine`. There is no new CLI,
RPC, tool, grant, broad-mode capability or automatic retirement. A real operator call
requires separate approval for the exact selected target and current producer-adoption
and recovery evidence. Importing this module or preparing a receipt is not that approval.

## Identity and evidence

Observe the exact canonical control capacity store, GitHub repository, source worktree,
common Git directory, registered linked worktree admin directory, workspace device/inode
and current physical HEAD. Source/workspace aliases, reparse points, foreign registrations,
submodules and uncertain Git/config discovery refuse. The physical HEAD is separate from
a historical pool row's head and task IDs.

Preparation accepts an exact expected identity, a separate complete expected pool row
(or `None` for path-only work), and pre-existing private recovery and selected-owner
adoption references. Each reference is `(path, sha256)` to a regular, unaliased UTF-8 JSON
receipt whose `identity` equals the observed identity. The owner reference also names its
actual selected `owner` and 40-hex `producer_generation`. These are operator-supplied
references, not automated proof that old code adopted a fence or that all writers stopped.
The operator must verify that evidence independently before any live invocation.

Preparation validates references and the current free/nonactive state, then publishes a
private no-overwrite plan receipt. Plan/result paths must be canonical and outside
the moving workspace; execution also refuses overlap with the selected retained destination
before creating retirement intent. It does not release or retire a slot. A modified plan,
reference, row, Git registration, inode or HEAD refuses execution. Keep prior recovery
copies unchanged. The provided digest pins the reference; it does not replace a restoration
proof or copy a private backup into the model's context.

Legacy physical-path preparation with no pool association uses `ledger=None, slot_id=None`.
It neither invents a pool row nor asserts that an unknown row owns those bytes. All unknown
legacy rows/history remain unchanged. An explicitly selected pool row must already have
an exact durable ledger/slot/path/generation association; integer IDs, path hashes or a
free status cannot create one during retirement. Unknown associations refuse. The API
does not qualify any inventory batch or assert that nineteen live roots are covered.

## Mandatory producer fence

`workspace_lifecycle` lives in the existing canonical control `worktree-capacity.sqlite3`.
It charges no new budget and introduces no queue or capacity limit. Physical path ownership
(repository/source/path) is distinct from the exact active operation and pool execution
history. Optional injected budget admission never selects another lifecycle authority.

Both `LocalGitRepository` and `PooledLocalGitRepository` reserve the exact path before
claiming a pool slot or ensuring/fetching objects, creating a branch, materializing a
worktree, or linking its environment. This applies with `capacity=None`, through overflow,
maintenance, local-CI and direct class calls. Direct public branch preparation takes the
same fence; only its already-owning nested operation is reused. A retirement refusal is
not `WorktreePoolExhausted` and cannot silently trigger overflow. Proven same-owner
materialization contention is a separate transient result: a pool selector may try another
candidate, preserving its existing concurrent selection behavior. Uncertain, foreign and
retiring/retired candidates remain hard refusals.

Direct pool claims require the current exact durable operation. Binding/release validates
the associated lifecycle identity and the issued lease version/PID/head. Unknown legacy
identity, retiring/retired paths and conflicting generations refuse without rewriting
history. Fresh normal producer claims establish their own association; an unassociated
pre-existing row is never auto-adopted by a guessed integer or source hash.

Lock order is existing caller reservation when present, exact-path operation, then short
pool/capacity SQL writer transactions. No SQL writer survives Git, network, copying or
subprocess work. Pool and lifecycle databases are not one atomic transaction: intent is
journaled first, and partial failures preserve a protected state. Unrelated paths remain
available; there is no machine-wide producer pause.

## Retained move and failure states

The first physical operation is non-force `git worktree move` to an absent same-device
private destination with an existing parent. Before Git, the original path has durable
retiring intent and the destination is also excluded. Git updates the real worktree admin
registration while retaining tracked/index/conflict state and all ignored/untracked bytes.
No delete, prune, reset, forced checkout/unlock, pool reuse or implicit reinitialization
is performed. The original exact generation remains tombstoned.

An open descriptor can continue writing the moved inode. The result explicitly reports
`retained_mutable=true` and `disk_reclaimed=0`. It is recovery retention, not immutable
preservation, arbitrary-writer quiescence or permission for immediate deletion.

States do not expire: successful materialization becomes ready; unknown materialization
failure becomes uncertain; retiring/retired remains excluded. A producer may release its
own fence for a definite failure before workspace mutation; an existing budget's uncertain
charge remains independent. Failure after retirement intent or an ambiguous Git result
retains intent, the selected destination and any occupied private receipts. No automatic
replay removes those artifacts. Operator recovery must inspect the durable state and
retained paths. A moved result receipt with pending retirement state is an uncertain final
publication, not an instruction to repeat the move.

Previously loaded or installed producers do not consult the new boundary. Before live
retirement, the operator must establish adoption/exclusion for the actual relevant selected
owner through its supported lifecycle. A new source commit, free row, missing PID or held
caller lock is insufficient. This limitation is per selected owner, not a global pause or
a demand to prove every possible unrelated writer quiescent. Later destructive reclamation
requires separate approval and refreshed exact-owner/preservation evidence.

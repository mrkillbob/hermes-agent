# Caller-bound feedback commands

Ordinary registered commands operate on one repository: the canonical GitHub
`origin` of the trusted invocation workspace. The fetch and push identities must
agree. An explicit repository selector confirms that identity; it cannot switch
repositories. Existing configured admission is still required. This change does
not add repositories, writer identities, tool grants, or enablement.

The host supplies standalone CLI dispatch cwd through a task-local context. For
managed calls the plugin requires `ctx.session_cwd(require_session=True)`: a
recorded session workspace, never the shared gateway directory or an unbound
profile terminal default. Older hosts that do not implement the strict keyword
refuse. The generic host change must ship with the plugin; isolated plugin hosts
also need to carry that trusted context before they can qualify. This is an
accident-prevention contract, not OS containment against arbitrary in-process
plugin code.

Both the caller and configured checkout must share a Git common directory and
canonical remote identity. Nested directories and linked worktrees are admitted;
another clone with the same branch and commit is not. Ambient Git routing/config
overrides and a conflicting origin push URL refuse. The host's exact null-device
global/system config isolation and `GIT_CONFIG_NOSYSTEM=1` remain supported;
arbitrary config files/injections, including bare `GIT_CONFIG`, still refuse.
Only GitHub HTTPS/SSH origins
are currently supported; ambiguous/missing origins and other hosts refuse.

All nested maintenance/review policies are narrowed before parsing/client access.
Wrong workspace arguments and post-merge deployment paths refuse before effects.
Materializers retain the immutable binding for thread hops, check before mutable
lease/capacity admission, and task creation verifies the selected workspace again.
Ordinary workers receive the same repository's worktree; the calling session's
cwd/environment is never rewritten. Caller arguments are copied before canonical
case normalization.

Configured repository spelling is preserved for existing case-sensitive admission
and intake contracts; identity checks and lock keys compare case-insensitively.
Local-CI and label policies whose empty repository list means all configured targets
remain enabled, narrowed to the caller. Empty lists do not grant repair or Actions
identity scope. The configured base must match the checkout's actual origin before
any lock or handler is entered, including administrative scans. An explicitly
configured head fork remains an exact PR admission rule; it cannot select another
workspace or change the base repository's lock identity.

Receipt history stays in its existing shared ledger. Summary/history queries are
repository-filtered; caller scans ignore ambient multi-board reconciliation and
do not enumerate legacy pool leases whose slot namespace cannot prove repository
ownership. This may retain capacity longer; existing timeout handling remains.
Per-common-directory/repository scan locks isolate concurrent calls. Administrative
scans acquire those same locks in sorted order before the legacy control-home lock.
The existing `HERMES_KANBAN_BOARD` worker pin remains authoritative for successor
task creation and promotion; it does not restore fleet-wide board enumeration.

Fleet-wide behavior is separate: `scan-admin --all-configured` requires an explicit
`administrative_batch: true` plugin setting and refuses protected worker/delegation
contexts, including empty identity markers. The setting is absent/false by default;
no migration sets it. It preserves the existing broad scan implementation for a
later deliberate operator migration, rather than letting an ordinary call scan
global targets implicitly. This candidate does not authorize that migration.

## Minimal migration/deprecation plan (not performed)

1. Qualify the host and plugin patch together in isolated, offline A/B fixtures.
   Keep the installed deny, cron state and ESTOP intact throughout qualification.
   The general cron wrapper requires `--workspace /absolute/repository`; it passes
   that directory only to its child and leaves the scheduler's cwd unchanged.
   Existing repository selectors confirm this identity. No target is inferred from
   shared config, and the wrapper never opts into administrative scanning.
2. Retain the general skill as the canonical instructions. Replace the LunaBot
   same-name skill with a differently named native-workflow reference or repository
   policy pointer, preserving all writer-identity and GitHub-write requirements.
   The Claude global path is an alias; do not remove it as a duplicate implementation.
3. Convert hardcoded Luna scanners and repo-specific schedule wrappers to thin
   adapters that invoke the same bound command from the intended workspace and
   confirm its expected repository. Keep old files with a deprecation notice first;
   remove only after two subsequent minor releases and an explicit migration review.
4. Preserve historical ledgers, pending acknowledgments and worktrees. Do not copy,
   reset or delete them to obtain green readiness. Decide explicitly whether upstream
   fleet scans or separate-clone deployment workflows belong in administrative scope;
   ordinary caller mode deliberately refuses that relocation.
5. A later installed change requires separate authorization and acceptance evidence.
   A source patch, static review, or synthetic pass is not live scheduling readiness.

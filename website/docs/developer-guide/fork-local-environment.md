# Fork local development

## Local project environment

This checkout is the separate Hermes-agent project and targets Python 3.14+. Use
`./.venv/bin/python` for the primary Hermes-agent checkout. The canonical environment
is managed outside the checkout and is shared only by Hermes-agent worktrees. New
Hermes-agent Git worktrees created by the task, conversation,
subagent, web-Git, CLI, or PR-maintenance paths receive a `.venv` link to the verified
repository runtime before an agent is released; inside those worktrees use
`./.venv/bin/python` (which must resolve to Python `>=3.14` and `<3.15`, as supported by the project lock). Do not install Hermes dependencies into the LunaBot environment at
`/Users/mikedemott/LunaBot-default/.venv`; that environment belongs to LunaBot/TradingBotV18.


## VS Code Studio Access

- Shared launcher: `/Users/mikedemott/.local/bin/vscode-studio`.
- Open this canonical Hermes workspace with `vscode-studio hermes`.
- Open only curated workspaces or specific files; do not open `/Users/mikedemott/Codex` or network drives from Hermes agents.
- VS Code is for inspection/editing ergonomics only; tests and runtime evidence still come from explicit commands.

## Catalog admission during upstream syncs

This fork keeps catalog additions and entry edits out of mixed source syncs. Those changes require
separate data-only admission work under the existing Plugin Catalog CI guard; neither a successful
structural check nor an upstream SHA bump establishes that admission. This sync retains the fork's
previous entry files, including their exact pins, while adopting the catalog tooling and policy updates.

Online catalog browsing and bare-name installs use the published or cached catalog when available;
otherwise they fall back to this checkout's catalog. Deferring a local entry does not block an
already installed plugin or guarantee that the published catalog has the entry. The fork's existing
delisted entries remain absent from its local catalog, and `removed.yaml` retains its separate blocklist role.

Mem0 and OpenViking moved out of core in this sync, but their new local catalog entries are deferred.
Existing installed plugins, configuration and stored memories remain in place. A home without the
plugin can migrate only when the published or cached catalog supplies a usable entry and installation
is permitted; if that catalog is unavailable, the checkout fallback cannot supply either provider.
Offline recovery hints based on the local catalog are also unavailable until separate admission adds
the entries. The generic upstream migration instructions assume such a catalog entry is available.

## Codex Cloud snapshot restores

The cloud checkout uses PM activation (`source ./activate`), not the Mac's
`.venv` link. In the tested cloud image it lives at `/workspace/hermes-agent`;
pinned tools and download caches live under `/workspace/hermes-dev-data`.
`/home/agent` and `/var/tmp` are read-only in development tasks.

PM lifetime records bind mutable dependency state to filesystem identities.
Copying an installed `HERMES_HOME` into another executor can make activation fail
with `pm.lifetime.LifetimeUnavailable: managed environment identity changed`.
`hermes pm repair` also starts the protected PM runtime, so it cannot repair this
ownership mismatch. Do not delete or rewrite `.lifetimes` records, remove a live
generation, or disable the checks.

Keep the image's tools, caches and built JavaScript assets reusable, but create
<!-- no-tmp: ok — this guide describes the provider-owned temporary mount, separate from published workspace state -->
the development dependency state at task startup. In this environment `/tmp` is
a separate writable tmpfs (8.8 GiB when tested). Use one task-scoped home there
instead of creating a new home for every shell or storing task homes in the
published `/workspace` tree. Run this block in each **clean Bash shell**:

```bash
set -eo pipefail
cd /workspace/hermes-agent
test -n "${CODEX_SESSION_ID:-}"

hermes_task_key="$(
  /workspace/hermes-dev-data/tools/python-3.14.9+20261021-linux-x64/bin/python3 -I -B - <<'PY'
import hashlib
import os
task = os.environ['CODEX_SESSION_ID']
print(hashlib.sha256(f'{os.getuid()}:{task}'.encode()).hexdigest()[:32])
PY
)"
# no-tmp: ok — PM state belongs on the provider's disposable tmpfs, outside the published workspace
export HERMES_HOME="/tmp/hermes-cloud-task-$hermes_task_key"
unset hermes_task_key
export HERMES_RUNTIME_DIR=/workspace/hermes-dev-data/tools
export UV_CACHE_DIR=/workspace/hermes-dev-data/uv-cache
export UV_PYTHON_INSTALL_DIR=/workspace/hermes-dev-data/python-bootstrap
export XDG_CACHE_HOME="$HERMES_HOME/cache"
export NPM_CONFIG_CACHE=/workspace/hermes-dev-data/npm-cache
export HERMES_TEST_SCRATCH_ROOT=/workspace/hermes-test-scratch
unset VIRTUAL_ENV PYTHONHOME PYTHONPATH
source ./activate
```

These paths and the bootstrap Python version describe the current fork image;
if its layout or pins change, update them from the image's verified tool store.
Do not substitute a downloaded interpreter or another project's environment.
For subsequent reactivation in the same shell, use `source ./activate`.
All shells with the same `CODEX_SESSION_ID` reuse the same PM state. A different
task ID selects a separate home without touching the old one. This home is for
development only: do not put personal credentials or persistent user data there.

Check readiness after activation:

```bash
python -c 'import sys,httpx,pm; assert sys.version_info[:2] == (3,14); print(sys.executable); print(pm.__file__)'
node --version
npm --version
hermes --version
python -m pm.cli doctor
bash scripts/run_tests.sh tests/test_hermes_bootstrap.py -q
```

During image installation, use PM's existing setup/activation workflow with
writable data/cache paths, followed by `npm ci` and the `ui-tui` and `web` workspace
builds. A successful image install is insufficient: also run the startup block
and readiness checks in a fresh restored task. Preserve inherited proxies, CA
trust, TLS verification and network policy. Do not use alternate routes after a
denial, add credentials, or start gateways, scheduled jobs or live model calls.

### Lifecycle and storage limits

This recipe relies on the provider discarding the temporary filesystem when the
executor is disposed and excluding it from the reusable image. A tmpfs mount by
itself does not prove the provider's snapshot policy. Verify that lifecycle with
a fresh restored task before treating this as a working published environment.
Do not publish a running development task's mutable homes back into the image.
If temporary storage is retained across tasks, this recipe does not bound total
storage: use provider-managed disposable storage or request a supported PM
retirement workflow instead of adding age-based deletion.

Within one executor the recipe creates one home per task ID, not per shell.
PM can collect unused generations through its normal GC, but its orphan-state
collector preserves installs whose checkout still exists. It does not delete
whole task homes. Do not add an EXIT trap or a timer to remove them: another
shell or managed child may still use them. Executor disposal supplies cleanup;
the startup block never deletes or recertifies existing state. If activation
still reports an identity mismatch, stop and report the exact failing command.
Do not restore the bundled providers or removed dependency extras to bypass this deferral.

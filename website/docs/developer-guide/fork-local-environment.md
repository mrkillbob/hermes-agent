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
Do not restore the bundled providers or removed dependency extras to bypass this deferral.

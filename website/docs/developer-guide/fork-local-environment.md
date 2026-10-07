# Fork local development

## Local project environment

This checkout is the separate Hermes-agent project and targets Python 3.14+. Use
`./.venv/bin/python` for the primary Hermes-agent checkout. The canonical environment
is managed outside the checkout and is shared only by Hermes-agent worktrees. New
Hermes-agent Git worktrees created by the task, conversation,
subagent, web-Git, CLI, or PR-maintenance paths receive a `.venv` link to the verified
repository runtime before an agent is released; inside those worktrees use
`./.venv/bin/python` (which must resolve to Python >=3.14 and <3.15, as supported by the project lock). Do not install Hermes dependencies into the LunaBot environment at
`/Users/mikedemott/LunaBot-default/.venv`; that environment belongs to LunaBot/TradingBotV18.


## VS Code Studio Access

- Shared launcher: `/Users/mikedemott/.local/bin/vscode-studio`.
- Open this canonical Hermes workspace with `vscode-studio hermes`.
- Open only curated workspaces or specific files; do not open `/Users/mikedemott/Codex` or network drives from Hermes agents.
- VS Code is for inspection/editing ergonomics only; tests and runtime evidence still come from explicit commands.

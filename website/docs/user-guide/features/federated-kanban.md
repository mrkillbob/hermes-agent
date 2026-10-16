---
sidebar_position: 8
title: Federated Kanban runners
---

# Federated Kanban runners

Federated Kanban is an opt-in runner pool for using multiple computers as one
queue. The Mac and Windows Hermes installations remain separate: each keeps its
own profiles, projects, credentials, workspaces, and Git checkout. Only task
metadata, runner capabilities, leases, and results cross the coordinator
boundary.

Windows does not become a fork. The Windows runner is a standalone compatibility
process that invokes the stock upstream Hermes executable already installed on
that computer. The standalone zipapp requires a Hermes version with the internal
`fleet child-env` helper. It asks that runtime to construct the target profile
environment through the same isolation and managed-value rules as local runners;
the zipapp itself needs only the standard library. An older runtime or invalid
helper response stops execution without falling back to the launch environment.

## Availability model

Each runner advertises capacity only while its local Hermes/Desktop liveness
marker is present. Closing Windows removes only Windows capacity; the Mac can
continue claiming work. Closing the Mac removes only Mac capacity. If both are
closed, no runner claims new federated work. A disappeared runner's lease is
reclaimed after its expiry.

## Enable the lane

Configure the coordinator URL and explicitly enable admission:

```text
hermes fleet init --url https://mac.example/fleet --enable
```

Keep the bearer key in the Hermes secret environment:

```text
HERMES_FLEET_TOKEN=<machine-scoped-key>
```

Create a federated card with no profile assignee:

```text
hermes kanban create "Build LunaBot" --federated --project lunabot --workspace worktree
```

The normal local dispatcher ignores fleet-owned cards. The coordinator matches
the card's model, tool, project, and workspace requirements against live runner
capabilities.

## Run a stock-Windows compatibility runner

The runner requires a liveness marker maintained by the local Hermes/Desktop
launcher. It never copies `.env` files or Git history:

```text
hermes fleet runner \
  --node-id windows \
  --hermes-executable 'C:\\Users\\idrat\\AppData\\Local\\hermes\\bin\\hermes.exe' \
  --liveness-file 'C:\\Users\\idrat\\AppData\\Local\\hermes\\fleet-live' \
  --profile coding-expert \
  --profile task-orchestrator \
  --project LunaBot \
  --project Hermes-Agent \
  --model gpt-5
```

Use `hermes fleet status` to inspect reachability, task leases, and whether the
secret is configured. Status output never prints the bearer key.

The CLI runner retains claims and transport captures under the active Hermes home's
`fleet/runners/<node hash>/` directory. Restart it with the same home and node ID to
recover that state. A restart reads the original gateway admission without submitting
or cancelling it. Captures are removed after the coordinator acknowledges settlement;
unknown outcomes retain their evidence and require inspection.

Each polling cycle retries transient registration failures at most twice, waiting one
and then two seconds. Exhaustion defers work until the next poll instead of exiting the
worker. A transient idle heartbeat failure also skips claiming until the next registered
poll. Authorization, certificate, protocol and explicit heartbeat refusals remain terminal.
Owner liveness and cancellation are checked again before claiming; active execution's
lease-maintenance failures still cancel execution. Claims and execution are never replayed
by this recovery loop.

The Windows Desktop supervisor stores each worker's complete stdout/stderr and a PID,
start/end-time and exit-code receipt in `fleet/runner-diagnostics/run-<unique id>/`.
It retains the latest eight completed runs, evicting whole older runs rather than
truncating their output. Active runs and records whose process identity cannot be checked
are protected. After a supervisor disappears, a dead worker's receipt records an
unobserved exit rather than inventing an exit code. This bounds completed history by
run count, not byte size; live output is never truncated. Receipts contain no command
arguments or credentials. The earlier fixed supervisor log files are left intact.

The coordinator checks expired leases in a single maintenance worker even when all runners are offline.
A busy expiry transaction does not block HTTP acceptance. Shutdown waits for an in-flight
sweep to finish committing and mirroring its result. Expiry
releases runner load and holds the task as `uncertain`; it never makes possibly
running work available for automatic retry. These leases and receipts do not provide
consensus-grade exactly-once execution.

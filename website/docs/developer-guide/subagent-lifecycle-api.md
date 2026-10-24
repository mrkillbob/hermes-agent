---
title: Public Subagent Lifecycle API
sidebar_label: Subagent lifecycle API
---

# Public Subagent Lifecycle API

Plugins can launch and supervise fresh Hermes child sessions without importing
`tools.delegate_tool`, gateway internals, TUI state, or `AIAgent` fields.
The service resolves its parent from the current agent turn, so it works in
CLI, gateway, non-interactive, and kanban-worker sessions. Launching outside an
active agent turn fails closed with `No active Hermes parent session`.
The facade also works when a plugin runs under `plugins.isolation: host`.

Requests accept `goal`, `context`, `role`, `model`, `provider`, `allowed_toolsets`,
`blocked_tools`, `working_directory`, `parent_session_id`, `correlation_id`,
`metadata`, and `timeout_seconds`, subject to the restrictions below.

```python
from agent.subagent_lifecycle import SubagentLaunchRequest

def launch_review(ctx):
    # Call from a plugin tool or hook while an agent turn is active.
    service = ctx.subagent_lifecycle
    handle = service.launch(SubagentLaunchRequest(
        goal="Review this change for regressions.",
        context="Only inspect the supplied repository.",
        role="leaf",
        correlation_id="review-42",
        allowed_toolsets=("file",),
    ))
    # Persist handle.to_dict() if desired.
    if service.wait(handle, timeout_seconds=2).timed_out:
        return handle.to_dict()
    return service.result(handle)
```

`SubagentHandle` is serializable and carries a versioned, opaque capability.
Requests and handles may also arrive as mappings, including plugin-host wire records.
Pass a handle back to `status`, `wait`, `cancel`, `steer`, `result`, or `reconnect`; malformed
or forged handles return `UNKNOWN`/`UNKNOWN_HANDLE` and cannot access a child.

`steer(handle, text)` queues text for the child's next iteration boundary and
returns `True` while the child accepts steering. It returns `False` for empty
text, unknown or forged handles, handles owned by another parent session, or a
child that has finished or stopped accepting steering.

The stable states are `PENDING`, `STARTING`, `RUNNING`, `SUCCEEDED`, `FAILED`,
`INTERRUPTED`, `CANCEL_REQUESTED`, `CANCELLED`, and `UNKNOWN`.

`cancel(handle, reason=...)` is cooperative: it asks the child agent to
interrupt at its next safe boundary and returns `CANCEL_REQUESTED`; it never
claims completion until `wait` or `result` observes a terminal state. Terminal
results are immutable, idempotent, bounded to 32k characters, omit transcripts
and hidden reasoning, and include a stable result hash.

This API is lifecycle-managed asynchronous execution. Child construction and
completion use the same host-owned path as `delegate_task`, including parent
tool-resolution restoration, memory notification, serialized `subagent_stop`
hooks, resource cleanup, and child-cost rollup. It does not change the
synchronous `delegate_task` tool, batch delegation, or its gateway/TUI display.
The initial implementation retains metadata and terminal results in-process for
one hour.
After a process restart, `reconnect` returns `RECONNECT_UNAVAILABLE` and never
starts a replacement child. Running Python threads also cannot survive process
exit; callers must treat those handles as interrupted by process exit.

Requests are fail-closed: goal/context/metadata sizes are capped, unknown or
parent-broadening toolsets are rejected (plugin-registered toolsets count as
known), and per-tool blocks, working-directory overrides, and per-launch
timeouts are explicitly rejected until Hermes can
support them without weakening isolation. Use `allowed_toolsets` to narrow a
child; Hermes's existing unsafe-tool block remains enforced.

`allowed_toolsets` narrows the names passed into child resolution; it does not
guarantee that the final child schema contains only those named sets. By
default, `delegation.inherit_mcp_toolsets` appends every MCP toolset held by the
parent to a narrowed child request. Setting it to `false` disables only that
automatic append: a parent MCP toolset named explicitly in `allowed_toolsets`
is still available, and synchronous `delegate_task` children inherit the
parent's toolsets. The parent’s `disabled_toolsets` and the child role’s blocked
tools are applied after expansion. The public lifecycle API also rejects
unknown toolset names and, when the parent has an explicit enabled set, names
outside that set before constructing a child.

## Workforce governance

For specialist workers, `governance` carries the reusable controls inspired by
the fictional workforce patterns analyzed for Hermes. It is passive metadata
and validation; the host's tool and authority boundaries remain authoritative.

```python
from agent.subagent_lifecycle import SubagentLaunchRequest
from agent.worker_contract import (
    ContextProfile,
    ExecutionEnvelope,
    MemoryPolicy,
    PrivacyPolicy,
    RoleSeparation,
    WorkforceGovernance,
)

governance = WorkforceGovernance(
    memory=MemoryPolicy(purpose="task-local evidence", retention="bounded"),
    execution=ExecutionEnvelope(lane="simulation"),
    roles=RoleSeparation(planner_id="planner", critic_id="critic"),
    context=ContextProfile(
        profile="reviewer",
        assumptions=("the source revision is current",),
        assumption_warnings=("verify source freshness",),
    ),
    privacy=PrivacyPolicy(allowed_scopes=("task",)),
)
handle = ctx.subagent_lifecycle.launch(SubagentLaunchRequest(
    goal="Analyze the supplied evidence.",
    governance=governance,
))
```

The envelope provides bounded memory retention, dimensioned trust decay,
lineage and quarantine state, explicit simulation/canary/production lanes,
visible degraded capability states, planner/critic/executor separation,
missing-context warnings, privacy disclosure decisions, hostile-input
assessment, and probabilistic scenario ensembles. Production governance with
an executor requires a distinct critic that has accepted the work and an
explicit execution approval. Simulation and canary governance cannot permit
side effects. These checks do not turn model-facing text into authority: the
runtime still decides which tools and destinations are available.

## Routing

Children follow the `delegation` config (`provider`, `model`, `base_url`,
`api_key`, `api_mode`, `request_overrides`) exactly as `delegate_task` children do.
The request's `model` overrides the configured model. Its optional `provider`
names a configured provider for this launch, with precedence request >
delegation config > parent; the selected provider supplies its own credential
bundle. An unresolvable provider raises `SubagentLifecycleError` before launch.
Core does not expose `provider` to the model: the plugin chooses it, for example
from operator config.

The lifecycle API does not consult `delegation.backend`: the plugin that launches a child already chooses
its route. To change how `delegate_task` routes the model's subagents, write a
[delegation provider plugin](delegation-provider-plugin.md).

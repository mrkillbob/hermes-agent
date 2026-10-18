# A2A — Agent-to-Agent protocol for Hermes

Talk to other agents, and let other agents talk to you, over the open
[A2A protocol](https://a2a-protocol.org) **v1.0**. Works with any A2A-compliant
peer (another Hermes, LangChain, CrewAI, Google ADK, OpenClaw, …). Stdlib only —
no `a2a-sdk` dependency.

## Enable

```bash
hermes gateway setup      # pick A2A, or:
```

```yaml
# ~/.hermes/config.yaml
gateway:
  platforms:
    a2a:
      enabled: true
      extra:
        port: 9900

# peers you want to call (outbound):
a2a_agents:
  researcher:
    url: "http://localhost:9999"
    auth: { type: bearer, token: "sk-..." }
    timeout: 120
    capabilities: [web_search, research]
```

## Outbound — call other agents

The agent gets five tools:

- `a2a_discover(url)` — what can this agent do?
- `a2a_call(agent, message, context_id?)` — send it a task, get the reply.
- `a2a_list()` — configured peers, saved conversations, metrics.
- `a2a_history(context_id)` — recall a saved A2A conversation.
- `a2a_orchestrate(capability, message, mode?)` — fan-out a task to every
  peer advertising a capability (`all` / `first` / `best`).

## Inbound — be callable

When the `a2a` platform is enabled, Hermes serves a v1.0 Agent Card at
`http://<host>:<port>/.well-known/agent-card.json` (the legacy
`/.well-known/agent.json` path is also answered for pre-1.0 clients) and
accepts JSON-RPC
`message/send`, `message/stream` (SSE), `tasks/get|list|cancel|subscribe`,
and push notification configs (inline or via
`tasks/pushNotificationConfig/create`). Incoming tasks are injected into your
**live** agent session — the same agent that's talking to you, with full
memory — and the reply is returned over A2A. Completed tasks stay queryable
via `tasks/get`.

## Security

- **No token ⇒ localhost only.** The server binds `127.0.0.1` and refuses to
  widen unless you configure a token *and* set `A2A_HOST`.
- **Per-peer tokens**: `A2A_PEER_TOKENS="alice:tok1,bob:tok2"` gives each
  remote agent its own credential; that authenticated name (never anything
  in the request body) drives rate limiting, trust, and audit.
- Inbound text — including `/`-prefixed text — is run through
  prompt-injection filters and framed as untrusted peer input; remote peers
  cannot invoke operator slash commands.
- Outbound text is scrubbed of credential-shaped strings.
- Push callbacks are SSRF-guarded and HMAC-SHA256 signed (`X-A2A-Signature`).
- Every exchange is logged to `~/.hermes/a2a_audit.jsonl`.
- Conversations persist to `~/.hermes/a2a_conversations/` — they survive context
  compaction and restarts (`a2a_history` recalls them).

## Env vars

| Var | Default | Meaning |
|---|---|---|
| `A2A_PEER_TOKENS` | _(unset)_ | Per-peer credentials `name:token,…` (preferred). |
| `A2A_BEARER_TOKEN` | _(unset)_ | Shared token; identity falls back to caller IP. |
| `A2A_HOST` | `127.0.0.1` | Bind host. Only widens with a token set. |
| `A2A_PORT` | `9900` | Inbound port. |
| `A2A_AGENT_NAME` | hostname-derived | Name on the Agent Card. |
| `A2A_PUBLIC_URL` | _(unset)_ | Routable URL advertised on the card (reverse proxies). |
| `A2A_TRUSTED_PEERS` | _(unset)_ | Allow-list of authenticated identities. Required for a non-loopback bind (with a token set) unless `A2A_ALLOW_ALL_USERS=true`; otherwise dispatch is refused. |
| `A2A_ALLOW_ALL_USERS` | `false` | Allow any authed peer (dev only). |
| `A2A_RATE_LIMIT` | `60` | Requests/minute per identity. |
| `A2A_MAX_PINGPONG_TURNS` | `5` | Anti-loop turn cap per context (max 20). |
| `A2A_REPLY_TIMEOUT` | `300` | Seconds to wait for the agent's reply; the orphan sweep never fails a task before this window (floor 300s) or while a request still waits on it. |
| `A2A_PUSH_SECRET` | bearer token | HMAC secret for push signing. |
| `A2A_ADVERTISED_TOOLSETS` | all registered | Restrict skills on the Agent Card. |

See `DESIGN.md` for architecture and the requirement-tracing table.

## Opt-in durable event-return candidate

`gateway.platforms.a2a.extra.reliable_events` defaults to `false`. When explicitly
`true`, the existing A2A endpoint serves a narrower event-return contract through
the already-running canonical owner. It does not start an owner, expose a new
listener, or grant another profile access. Legacy mode remains unchanged.

The event mode accepts `SendStreamingMessage`, `SubscribeToTask`, exact `GetTask`
and typed delivery acknowledgments using `SendMessage`. It requires the
`hermes.a2a.assignment.v1` assignment/receipt contract documented in
[Kanban worker lanes](../../../website/docs/user-guide/features/kanban-worker-lanes.md).
The first SSE Task identifies admission. A committed result/question/failure Task
has `metadata["hermes.delivery.v1"] = {id, sha256, acknowledged}` and every Task advertises
`metadata["hermes.events.v1"].ack_schema = "hermes.a2a.delivery_ack.v1"`.
The hash covers the canonical Task without this transport metadata. The receiver
stores the receipt before sending one data Part containing
`{schema, task_id, event_id, sha256}` in `SendMessage`. This is an application
extension negotiated by these peers, not a standard A2A ACK method. A matching
receipt response marks delivery; an SSE write or bare HTTP success does not.
Explicit reads/subscriptions may replay the same saved result after ACK, marked
`acknowledged: true`; this is not a new delivery obligation and the client does
not send another ACK. ACK never deletes the result or changes execution outcome.

Outer task IDs, request fingerprints and exact owner admissions are retained in
the owning profile's existing `state_meta` store. Results and delivery IDs are
saved once, before emission. Duplicate messages from the same authenticated peer,
agent, tenant and local producer resolve to the same task/admission; changed
payloads under that key are refused. The store retains up to 500 projections per
scope, each at most 64 KiB, and never silently evicts live/unacknowledged work.
Capacity exhaustion refuses admission. Retention cleanup remains an operator
source follow-up; do not treat the store as unbounded history.

A lost initial ACK can be resolved with one read-only `GetTask` application lookup:
`metadata["hermes.a2a.lookup.v1"] = {message_id, request_sha256}`. The fingerprint is
SHA256 of the canonical compact JSON `[request, correlation]`, where `request`
contains the original extracted `context_id` and assignment `text`. Scope and
fingerprint must match an existing projection; this never submits a task.

The active stream waits for canonical owner events. Disconnect detaches delivery,
not execution. A caller may make one bounded read/reattachment per explicit recovery ID,
with at most three distinct recovery attempts retained per task. A repeated ID
is inert, and transient failure requires a fresh explicit operator decision;
there is no watcher, periodic receipt read, automatic reconnect or retry timer.
Saved terminal receipts can be returned after owner restart without a model call.
An unknown owner admission remains uncertain and is never re-executed.

This candidate does not implement push in event mode (its Agent Card advertises
push disabled), remote cancellation/listing, or automatic question continuation.
Questions are delivered and acknowledged on the same bound task; answering or
settling them remains an explicit owner action. Streaming is bounded to 120 seconds
per foreground connection and existing owner/event-queue limits; cooperative OS
I/O can still stall. A CLI that exits cannot claim unattended return. No native
Dot/Codex callback, Claude endpoint/account or live peer is qualified by this source.

Reliable-mode JSON-RPC IDs must be null, a finite number or text, with at most
1,024 bytes in their ASCII-escaped JSON representation (including string quotes).
An invalid ID is refused before owner access, with a null response ID. Terminal
receipts reserve that entire ID budget and fit both the 49,152-byte SSE frame and
GetTask/find JSON response, including outer Unicode identifiers. Oversized result
text carries an explicit truncation marker; it does not change outcome or binding.
An already saved receipt that exceeds a response bound is retained and explicitly
refused, rather than rewritten under a new hash. The full truncated text is not
retrievable over this A2A interface.

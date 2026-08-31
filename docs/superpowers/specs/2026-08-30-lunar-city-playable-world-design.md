# Lunar City Playable Hermes World

Date: 2026-08-30
Status: approved design

## Purpose

Turn the approved Lunar City visualizer into a low-overhead, playable 2D
isometric world for observing and safely interacting with Hermes profiles,
sessions, subagents, Kanban tasks, and Kanban workers.

Lunar City is a hybrid control surface. It makes real work spatial and legible,
lets the operator hold persistent text or voice conversations with profile
leaders, and exposes bounded commands without creating a parallel authority
system.

## Approved source and provenance

The visual source of truth is:

`apps/desktop/public/lunar-city/moon-settlement-approved.jpg`

The approved restoration is recorded by these commits:

- `8dcdaae576` removes obsolete substitute board assets.
- `aee1ad9ca8` restores the approved settlement artwork and interaction layer.
- `55ffea25ba` adds `docs/lunar-city-design-handoff.md`.

The approved image is an art bible and extraction source. It is not the runtime
background. A flattened background would leave duplicated, stagnant leaders,
workers, vehicles, and activity behind the live game layer.

Implementation must begin in a branch or worktree that contains the exact
approved commit chain. If the implementation checkout does not contain that
chain, the implementation plan must reconcile the three exact commits before
editing Lunar City. It must not silently recreate or substitute the artwork.

## Goals

- Rebuild the approved settlement from individually controllable assets while
  preserving its composition, palette, buildings, leaders, robot-child design,
  and warmth.
- Use Phaser 3 for a genuine game scene with cameras, navigation, animation,
  depth, object selection, and deterministic simulation.
- Represent all three live populations:
  - profiles as district leaders and conversational identities;
  - sessions and subagents as moving inhabitants;
  - Kanban tasks, runs, and workers as jobs, destinations, queues, and visible
    infrastructure state.
- Give every repository or project a recognizable mission compound inside one
  expanding shared city.
- Let the operator talk to each leader through one persistent profile-owned
  conversation using text and, when configured, push-to-talk voice.
- Reuse existing Hermes session, subagent, voice, profile, and Kanban authority
  paths for every read and command.
- Fail closed on stale, ambiguous, disconnected, or unauthorized state.
- Keep CPU, GPU, memory, and background activity low enough for Lunar City to
  remain open during real development work.

## Non-goals

- Replacing the approved city with a generic strategy map, hex board, blue
  science-fiction board, procedural settlement, or spreadsheet dashboard.
- Rendering the approved flattened image underneath live objects.
- Simulating worker progress, evidence, task completion, or command success.
- Giving Phaser direct access to gateway, profile, session, voice, filesystem,
  or Kanban mutation APIs.
- Adding a new model tool or granting profiles, workers, or the game authority
  they do not already possess.
- Building general-purpose combat, resource economies, construction systems,
  or autonomous NPC behavior unrelated to Hermes work.
- Replacing the desktop chat surface. Leader conversations are focused entry
  points into standard Hermes sessions.

## Product ontology

### City and projects

The desktop presents one unified Lunar City across its registered connections,
not a separate game per repository or gateway. Every repository or project
receives a mission compound. Project compounds may expand or contract with
observed active work, but their identity is stable and keyed by connection plus
canonical project/repository identity. Profiles with the same display name on
different connections remain separate leaders.

Shared specialist buildings retain the meanings established by the approved
art:

- Library: context retrieval, source comparison, and research.
- Research Lab: investigation, experiments, and evidence gathering.
- Resource Depot: dependencies, credentials, models, and other resources.
- Bus Stop: queued, ready, or waiting-to-dispatch work.
- Review Office: validation, review, and acceptance evidence.
- Triage: failures, typed blockers, and diagnostic work.
- Break Garden: idle, heartbeat, recovery, or explicitly paused work.
- Council and operations spaces: orchestration, routing, dependencies, and
  handoffs.

### Leaders

Each profile is represented by a leader assigned from curated profile metadata
and `SOUL.md`. The assignment is deterministic and preserves explicit user
choices. `SOUL.md` may inform labels, role descriptions, idle gestures,
conversation tone, and room affinity. It does not grant runtime or command
authority.

Leaders remain anchored to their approved district locations. Selecting a
leader focuses the camera and opens that leader's persistent conversation.

### Inhabitants and jobs

Sessions and subagents are inhabitants. Kanban tasks and runs are jobs.
Inhabitants travel between their project compound and shared buildings as their
authoritative state changes. Parent-child delegation appears as a temporary
travel group and handoff path.

Every entity retains a complete typed identity, including the fields that
exist for that entity:

- connection ID and source;
- profile and profile home identity;
- session and child-session IDs;
- subagent ID and parent ID;
- board, task, run, and worker IDs;
- canonical repository or project identity.

Display names are never used as mutation identities.

## Art decomposition and reconstruction

### Source treatment

The approved image is decomposed into individually controllable assets:

- terrain and clean ground plates;
- cliffs, platforms, railings, and walkway segments;
- building shells and open interiors;
- furniture, signs, consoles, workbenches, plants, and small props;
- bus and other vehicles;
- each leader;
- robot workers and carried objects;
- emissive lights and effect masks.

When an extracted object reveals previously obscured pixels, a matching clean
plate is reconstructed. No removed object may leave a clone, hole, blur, or
stagnant remnant in the world.

The initial reconstructed scene matches the approved composition before any
dynamic expansion is added. The original image remains in the repository as a
visual reference and regression target only.

### Animation-ready assets

Articulated objects are separated into the smallest useful parts. Required
initial animated props include the laboratory telescope, review portal, bus,
depot and room lights, triage station, doors, workbenches, and garden activity.

Leader animation supports idle, listening, talking, thinking, acknowledging,
and unavailable states. Leaders do not roam in the first playable release.

Robot-worker animation supports:

- idle and walk;
- talk and listen;
- work and tool use;
- carry and handoff;
- queue and wait;
- blocked and failed;
- review and triage;
- heartbeat, rest, and done.

All derived animation frames must preserve the approved robot-child and animal
leader designs. Generic replacement sprites are not acceptable.

### Asset manifest

A versioned scene manifest is the single source of spatial truth. Each asset
entry records:

- stable ID, source-art digest, and asset version;
- atlas and frame names;
- world scale and origin;
- foot, interaction, attachment, and dialogue anchors;
- depth band and occlusion behavior;
- collision or navigation polygon;
- animation names, frame timing, and reduced-motion frame;
- optional effect masks and tint policy.

The manifest also defines walkway navigation nodes, edges, entrances, room
anchors, project-compound slots, camera bounds, and semantic destinations.
Runtime code does not duplicate those coordinates in component constants.

## Runtime architecture

### Phaser world

`LunarCityWorld` owns Phaser scenes, cameras, navigation, animation, depth
sorting, selection hit areas, object pooling, and visual level of detail. It is
lazy-loaded only when the Lunar City route opens and is fully destroyed when
the route is closed.

Phaser receives immutable presentation snapshots and emits typed selection or
intent events. It cannot read credentials, call Hermes, or mutate state.

### Live adapter

`LunarCityAdapter` combines existing desktop and plugin data sources:

- the connection-scoped profile fleet roster;
- standard profile and session data;
- native `subagent.*` events and stored child-session identities;
- Kanban board, task, run, worker, comment, diagnostic, and live-event data;
- existing voice and capability state.

The adapter publishes immutable `LunarCitySnapshot` values plus ordered deltas.
Snapshots are keyed by typed canonical identity. They contain presentation-safe
state only and exclude secrets, raw credentials, and unbounded logs.

Gateway events update the city immediately. Bounded reconciliation rereads
authoritative state after connection recovery, sequence gaps, or explicit user
refresh. The adapter does not poll on animation frames.

### React and nanostore boundary

React and nanostores continue to own:

- leader conversation, transcript, text composer, and voice controls;
- worker, task, run, evidence, and diagnostic inspectors;
- keyboard and screen-reader equivalents for every selected entity;
- command staging, confirmation, progress, and receipts;
- connection, profile, session, and route ownership.

Phaser animation does not cause React rerenders. React snapshot changes do not
recreate the Phaser scene; a thin bridge applies entity deltas.

### Command broker

`LunarCityCommandBroker` maps typed UI intents onto existing Hermes operations.
It is a UI adapter, not an authority source.

Direct actions are:

- focus or open the owning session;
- inspect evidence, diagnostics, comments, and logs;
- send ordinary guidance to a leader or owned session.

Disruptive actions require staged confirmation:

- interrupt or terminate;
- retry or reclaim;
- reassign;
- dispatch;
- change task state or otherwise redirect execution.

The confirmation shows exact profile, session/task/run identity, connection,
repository, current state, requested operation, and expected consequence. The
broker routes the command to the entity's owning connection and profile. Missing
or ambiguous ownership fails closed.

The city changes authoritative status only after a canonical receipt or event.
A successful click or HTTP/RPC return without authoritative readback is not
completion evidence.

## Leader conversations

Each leader owns one persistent profile-scoped Hermes session. A desktop store
persists the mapping from connection plus canonical profile identity to a
standard durable Hermes session ID. Resolution verifies that the session row is
still owned by the expected connection and profile before resuming it. A
missing, deleted, or mismatched row creates a new standard session and replaces
the mapping; it never adopts a similarly named foreign session. Reopening a
leader resumes that real session and context rather than creating a blank chat.

The dialogue panel overlays the running city without replacing the primary
desktop chat surface. It supports:

- streamed text and normal message composition;
- push-to-talk through the existing Hermes voice pipeline when configured;
- visible listening, thinking, speaking, interrupted, unavailable, and error
  states;
- opening the full session in the standard desktop chat surface;
- a truthful profile-population summary derived from the live adapter.

If voice is unavailable or fails, text remains usable in the same persistent
conversation. An attempted voice message is not silently discarded or marked
sent without the existing voice/session receipt.

## Gameplay state mapping

Movement is a visualization of authoritative transitions, not a work scheduler.
When a destination changes, a worker follows the navigation graph from its
current resolved anchor to the semantic destination.

- `ready` or queued work waits at the Bus Stop or project dispatch anchor.
- `working` moves to the project compound or observed specialist destination.
- resource waits route to the Resource Depot.
- review routes to the Review Office.
- failure or typed triage routes to Triage.
- heartbeat, idle, recovery, or explicit pause routes to the Break Garden.
- orchestration and dependency work routes through Council or operations.
- completion returns to the project compound with a completion artifact or
  handoff animation.

Unknown state never maps to `working`. Unknown, stale, disconnected,
unavailable, and partially observed entities use distinct non-alarming visual
treatments with last-observed timestamps.

Selecting a worker focuses the camera but does not pause or modify the real
worker. Its inspector presents identity, current action, evidence, files, cost,
duration, task, blocker, and commands only when those fields are actually
available.

## Performance architecture and budgets

### Rendering policy

- Interactive camera or selection runs at a maximum of 30 FPS.
- Ambient visible mode runs at a maximum of 15 FPS.
- Hidden, minimized, and route-unmounted worlds stop the Phaser scheduler and
  render zero animation frames.
- There is no general physics simulation. Navigation paths are computed only
  when origin, destination, or walkability changes.
- Terrain and building layers are cached. Texture atlases permit batched draws.
- Off-camera entities and animation are culled.
- Distant populations use truthful aggregate activity at the project or room
  level rather than rendering hundreds of full animation rigs.
- Continuous dynamic lighting, full-screen post-processing, expensive blur,
  unbounded particles, and simulated real-time shadows are prohibited.
- Workers, labels, effects, and path markers use object pools.
- Reduced-motion mode replaces travel and looping activity with direct state
  placement and minimal status changes.

### Acceptance budgets

Performance receipts record hardware, OS, Electron version, power state,
window size, and display scale. After a 30-second warmup on the user's target
Mac, the packaged Electron renderer must meet all of these initial budgets:

- hidden or minimized Lunar City: no active Phaser animation frame loop and no
  more than 0.5 percentage points of additional process CPU over the same
  desktop shell without Lunar City mounted;
- visible idle city: no more than 3 percentage points of additional process
  CPU averaged over 60 seconds;
- 100 individually rendered active inhabitants: 30 FPS cap, p95 frame time at
  or below 33.3 ms, p95 Phaser update work at or below 6 ms, and no more than
  12 percentage points of additional process CPU;
- 250 observed inhabitants with level-of-detail aggregation: p95 frame time at
  or below 33.3 ms and no more than 18 percentage points of additional process
  CPU;
- incremental GPU memory attributable to Lunar City at or below 256 MiB;
- after a 30-minute 100-inhabitant run, renderer resident-memory drift at or
  below 75 MiB and no monotonically growing entity, texture, listener, or timer
  count.

Failure to meet a budget blocks acceptance. The response is to reduce work,
atlas size, update frequency, effects, or visible detail, not to weaken the
budget without explicit operator review.

## Failure handling

- A rejected, timed-out, or disconnected command remains unresolved and shows
  the real error. It never animates as completed.
- Event-sequence gaps and reconnects trigger bounded snapshot reconciliation.
- Last-known entities remain visible with source and timestamp instead of
  silently disappearing.
- A lost WebGL context restores the latest immutable snapshot. If restoration
  fails, Phaser stops and the accessible React inspectors and conversations
  remain available.
- An unavailable Kanban plugin, voice capability, remote gateway, or profile
  source closes only that source's buildings and actions. The rest of the city
  remains usable and does not fabricate substitute data.
- Asset or manifest digest mismatch fails the world load before controls are
  enabled.
- A missing animation uses the asset's declared reduced-motion or static frame
  and emits a diagnostic; it does not substitute an unrelated sprite.
- Voice failure leaves the persistent text conversation intact.
- Kanban task state, run state, typed blocker, diagnostics, comments, logs, and
  durable events remain separate evidence surfaces.

## Accessibility

Every selectable Phaser entity has a synchronized keyboard-accessible React
representation. Selection, focus, identity, status, and available actions are
available without pointer precision or animation.

The world honors reduced motion, platform zoom, high contrast, and screen-reader
navigation. Status never depends only on color. Disruptive confirmations are
ordinary accessible React dialogs and are not drawn inside canvas.

## Testing

### Asset and visual tests

- Validate source digest, transparent bounds, anchors, scale, depth, collision
  shapes, attachment points, effect masks, and required animation states.
- Render the reconstructed default camera deterministically and compare it with
  the approved source. The comparison masks only explicitly dynamic regions.
- Check extracted-object seams, clean plates, occlusion, and animation frames
  at native and scaled resolutions.
- Reject generic substitute assets and missing manifest provenance.

### Adapter and state tests

- Cover profile, session, subagent, Kanban task/run/worker, repository, and
  connection identity mapping.
- Cover duplicate display names, remote connections, partial capability,
  dropped and reordered events, stale snapshots, deletion, reconnect, and
  process restart.
- Prove every supported authoritative state maps to its declared semantic
  destination and animation.
- Prove unknown state never becomes progress or `working`.

### Command and conversation tests

- Verify owning-route selection and fail-closed ambiguity.
- Verify the direct-action allowlist and confirmation requirement for every
  disruptive action.
- Verify exact identity in request, receipt, and readback.
- Cover rejection, timeout, ambiguous write outcome, reconnect, and no blind
  retry.
- Cover persistent leader-session resolution, profile ownership, streamed text,
  push-to-talk, interruption, audio failure, and text fallback.

### Renderer and Electron tests

- Use deterministic clocks and seeded routes for Phaser unit and integration
  tests.
- Test React inspectors, dialogue, confirmations, keyboard access, and
  reduced-motion behavior independently of Phaser.
- Run packaged Electron end to end against a fake backend: enter the city,
  select leaders and workers, resume a leader conversation, use text and fake
  voice, inspect evidence, send safe guidance, confirm a disruptive action,
  reconcile a disconnect, and restore after simulated canvas-context loss.
- Run the performance budgets for idle, hidden, 25, 100, and 250 inhabitants,
  active camera motion, active conversation, and the 30-minute stability case.

Mock-backend, visual, performance, and live Hermes evidence remain separate.
Live acceptance requires real profile, session, voice, subagent, and Kanban
receipts and must not be inferred from deterministic fixtures.

## Rollout

1. Reconcile the exact approved commit chain into the implementation branch.
2. Establish asset digests, manifest schema, clean plates, and a reconstructed
   static scene that visually matches the approved source.
3. Add camera, navigation graph, depth, and deterministic robot movement using
   fixture snapshots.
4. Add the read-only live adapter for profiles, sessions, subagents, and Kanban;
   keep controls disabled.
5. Add persistent leader text conversations, then capability-gated voice.
6. Add direct safe actions and evidence inspectors.
7. Add staged disruptive commands with exact-identity confirmation and
   authoritative readback.
8. Add project compounds, population level of detail, and the complete state
   animation set.
9. Pass packaged Electron visual, accessibility, performance, and fake-backend
   acceptance.
10. Capture separate supervised live Hermes receipts before calling the city a
    live control surface.

Rollback is route-local: disable the Lunar City route or its live adapter while
retaining standard desktop sessions, profile controls, voice, and Kanban. The
game cannot be a required intermediary for controlling Hermes.

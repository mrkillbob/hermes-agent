# Lunar City Playable Hermes World Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the approved Lunar City artwork as a low-overhead Phaser world populated by real Hermes profiles, sessions, subagents, and Kanban work, with persistent leader text/voice conversations and identity-safe controls.

**Architecture:** Phaser 3.90.0 owns only rendering, navigation, animation, selection, and level of detail. Focused TypeScript adapters publish immutable, presentation-safe snapshots from existing desktop stores, gateway RPC, and the optional Kanban plugin; React and nanostores retain conversations, accessibility, confirmations, and every mutation.

**Tech Stack:** React 19, TypeScript 6, nanostores, Phaser 3.90.0, Electron 41, Vitest, Testing Library, Playwright, existing Hermes JSON-RPC/REST/plugin doors.

**Spec:** `docs/superpowers/specs/2026-08-30-lunar-city-playable-world-design.md`

## Global Constraints

- The approved source is `apps/desktop/public/lunar-city/moon-settlement-approved.jpg`, SHA-256 `248e8d40946b08b9f74f4b2ddd0ba17e4f17fd054260189972164c5d6ca70590`, 1280x910.
- The approved image is an extraction/reference source and must not render as the runtime background.
- Preserve the approved composition, palette, buildings, leaders, and robot-child design; do not add generic substitute art.
- Keep Phaser off the initial desktop bundle through a dynamic import and destroy the game when Lunar City unmounts.
- Phaser consumes snapshots and emits intents; it never calls Hermes or mutates authoritative state.
- Identity keys include connection/profile plus every available session, subagent, board, task, run, worker, and repository identifier; display names are never mutation keys.
- Unknown, stale, disconnected, or partially observed state must never become `working` or implied progress.
- Safe actions may execute directly; interrupt, terminate, retry, reclaim, reassign, dispatch, and task-state changes require exact-identity confirmation and authoritative readback.
- Interactive rendering is capped at 30 FPS, ambient rendering at 15 FPS, and hidden/minimized/unmounted rendering at zero frames.
- Do not add a general physics engine, continuous dynamic lighting, full-screen post-processing, or unbounded particles.
- Mock, visual, performance, and supervised live-Hermes evidence remain separate.

---

## File Map

### Approved baseline and assets

- `apps/desktop/public/lunar-city/moon-settlement-approved.jpg`: immutable art reference.
- `apps/desktop/public/lunar-city/v1/world-atlas.png`: extracted terrain, platforms, buildings, clean plates, props, and effect masks.
- `apps/desktop/public/lunar-city/v1/world-atlas.json`: Phaser atlas frame coordinates.
- `apps/desktop/public/lunar-city/v1/characters-atlas.png`: derived leaders and robot animation frames.
- `apps/desktop/public/lunar-city/v1/characters-atlas.json`: Phaser character atlas coordinates.
- `apps/desktop/public/lunar-city/v1/world-manifest.v1.json`: anchors, depth, navigation, destinations, animations, and source digest.
- `apps/desktop/scripts/lunar-city/validate-assets.mjs`: deterministic asset/manifest validator.

### Focused runtime units

- `apps/desktop/src/app/lunar-city/model.ts`: canonical identity, snapshot, state, and intent interfaces.
- `apps/desktop/src/app/lunar-city/identity.ts`: collision-safe key constructors.
- `apps/desktop/src/app/lunar-city/manifest.ts`: manifest loader and runtime validator.
- `apps/desktop/src/app/lunar-city/state-map.ts`: authoritative state to destination/animation mapping.
- `apps/desktop/src/app/lunar-city/store.ts`: immutable snapshot and selection nanostores.
- `apps/desktop/src/app/lunar-city/adapters/fleet.ts`: profile and connection roster normalization.
- `apps/desktop/src/app/lunar-city/adapters/sessions.ts`: session/subagent event normalization.
- `apps/desktop/src/app/lunar-city/adapters/kanban.ts`: optional Kanban REST/socket normalization.
- `apps/desktop/src/app/lunar-city/adapters/reconciler.ts`: ordered deltas, staleness, and bounded rereads.
- `apps/desktop/src/app/lunar-city/game/create-game.ts`: lazy Phaser construction and teardown.
- `apps/desktop/src/app/lunar-city/game/world-scene.ts`: static world and entity reconciliation.
- `apps/desktop/src/app/lunar-city/game/navigation.ts`: graph route resolution.
- `apps/desktop/src/app/lunar-city/game/scheduler.ts`: 30/15/0 FPS policy and visibility handling.
- `apps/desktop/src/app/lunar-city/game/entities.ts`: pooled leaders, workers, props, and LOD groups.
- `apps/desktop/src/app/lunar-city/components/leader-dialogue.tsx`: persistent text/voice conversation.
- `apps/desktop/src/app/lunar-city/components/entity-inspector.tsx`: evidence and safe actions.
- `apps/desktop/src/app/lunar-city/components/command-confirmation.tsx`: disruptive-action confirmation.
- `apps/desktop/src/app/lunar-city/leader-sessions.ts`: durable connection/profile to session mapping.
- `apps/desktop/src/app/lunar-city/command-broker.ts`: routed commands and receipt verification.
- `apps/desktop/src/app/lunar-city/index.tsx`: thin composition root only.

---

### Task 1: Reconcile the approved Lunar City baseline

**Files:**
- Restore: `apps/desktop/src/app/lunar-city/*`
- Restore: `apps/desktop/public/lunar-city/*`
- Restore: `apps/desktop/e2e/lunar-city.spec.ts`
- Modify through replay: `apps/desktop/src/app/starmap/index.tsx`
- Preserve: `docs/lunar-city-design-handoff.md`, `design-qa.md`

**Interfaces:**
- Consumes: design commit `0dcfc99bcc` and approved source chain ending at `55ffea25ba`.
- Produces: the approved fixture visualizer running on the current desktop shell before replacement work begins.

- [ ] **Step 1: Create an isolated feature worktree and record the starting state**

Run the `superpowers:using-git-worktrees` skill from commit `0dcfc99bcc16bbaacc4b8b2dfac4d240a52682c2`, then record:

```bash
git status --short --branch
git rev-parse HEAD
git merge-base 55ffea25bab4779cd3998744cae9975a054d2ec1 HEAD
```

Expected: clean feature worktree, exact design commit at HEAD, merge base `a35100ac50b7f0097dc26947e07b055c2137f5fb`.

- [ ] **Step 2: Replay the complete Lunar City history in order**

```bash
git cherry-pick fd01048446 9ba59f07de 6cc1cd4276 cb77643208 d2f915c76c 651ba8bba8 8dcdaae576 aee1ad9ca8 55ffea25ba
```

If the modern Starmap/overlay shell conflicts, preserve its current routing and panel ownership while applying only the Lunar City import, `LunarCity` render branch, and `onOpenMemoryGraph` callback from the replayed commit. Do not accept changes to unrelated session, gateway, or overlay behavior.

- [ ] **Step 3: Verify baseline behavior**

```bash
npm run test:ui --workspace apps/desktop -- src/app/lunar-city/index.test.tsx
npm run typecheck --workspace apps/desktop
git diff --check
```

Expected: six Lunar City tests pass; TypeScript and whitespace checks pass.

- [ ] **Step 4: Record the reconciled exact heads**

Append the feature HEAD and the nine replayed source commits to `docs/lunar-city-design-handoff.md`, then commit only that receipt if conflict resolution created an additional commit:

```bash
git add docs/lunar-city-design-handoff.md apps/desktop/src/app/starmap/index.tsx
git commit -m "chore(desktop): reconcile approved Lunar City baseline"
```

### Task 2: Add the validated modular asset pack

**Files:**
- Create: `apps/desktop/public/lunar-city/v1/world-atlas.png`
- Create: `apps/desktop/public/lunar-city/v1/world-atlas.json`
- Create: `apps/desktop/public/lunar-city/v1/characters-atlas.png`
- Create: `apps/desktop/public/lunar-city/v1/characters-atlas.json`
- Create: `apps/desktop/public/lunar-city/v1/world-manifest.v1.json`
- Create: `apps/desktop/scripts/lunar-city/validate-assets.mjs`
- Create: `apps/desktop/scripts/lunar-city/validate-assets.test.mjs`

**Interfaces:**
- Consumes: approved source image and its fixed digest.
- Produces: `validateManifest(root): ValidationResult` and two Phaser-compatible atlases with no flattened runtime backdrop.

- [ ] **Step 1: Write the failing validator test**

```js
test('rejects a flattened reference image as a runtime frame', () => {
  const result = validateManifest(fixture({ frames: [{ id: 'background', source: 'moon-settlement-approved.jpg' }] }))
  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /approved source cannot be a runtime frame/)
})
```

- [ ] **Step 2: Run the validator test and verify failure**

```bash
node --test apps/desktop/scripts/lunar-city/validate-assets.test.mjs
```

Expected: FAIL because `validateManifest` does not exist.

- [ ] **Step 3: Implement manifest validation**

The validator must require this top-level shape and verify every referenced atlas frame, unique ID, anchor, depth, navigation endpoint, animation, and source digest:

```js
export function validateManifest(root) {
  const errors = []
  if (root.version !== 1) errors.push('version must equal 1')
  if (root.source?.sha256 !== '248e8d40946b08b9f74f4b2ddd0ba17e4f17fd054260189972164c5d6ca70590') {
    errors.push('approved source digest mismatch')
  }
  if (root.frames?.some(frame => frame.source === 'moon-settlement-approved.jpg')) {
    errors.push('approved source cannot be a runtime frame')
  }
  return { ok: errors.length === 0, errors }
}
```

- [ ] **Step 4: Derive and review the complete asset set**

Use the approved image-editing workflow to extract and clean: terrain plates; cliffs; platform/walkway segments; Library, Research Lab, Resource Depot, Bus Stop, Review Office, Triage, Break Garden; bus; telescope; review portal; consoles; signs; plants; owl/fox/badger/owl leaders; and the white robot-child rig. Reconstruct every obscured clean plate. Create robot frames for `idle`, `walk`, `talk`, `listen`, `work`, `carry`, `queue`, `blocked`, `failed`, `review`, `triage`, `heartbeat`, `rest`, and `done`; create leader frames for `idle`, `listen`, `talk`, `think`, `ack`, and `unavailable`.

- [ ] **Step 5: Validate the assets and commit**

```bash
node --test apps/desktop/scripts/lunar-city/validate-assets.test.mjs
node apps/desktop/scripts/lunar-city/validate-assets.mjs apps/desktop/public/lunar-city/v1/world-manifest.v1.json
git add apps/desktop/public/lunar-city/v1 apps/desktop/scripts/lunar-city
git commit -m "feat(desktop): derive modular Lunar City asset pack"
```

Expected: all frames, anchors, navigation references, and digests validate; visual review finds no frozen duplicates, holes, seams, or generic replacements.

### Task 3: Mount Phaser lazily and reconstruct the static world

**Files:**
- Modify: `apps/desktop/package.json`, `package-lock.json`
- Create: `apps/desktop/src/app/lunar-city/model.ts`
- Create: `apps/desktop/src/app/lunar-city/manifest.ts`
- Create: `apps/desktop/src/app/lunar-city/manifest.test.ts`
- Create: `apps/desktop/src/app/lunar-city/game/create-game.ts`
- Create: `apps/desktop/src/app/lunar-city/game/world-scene.ts`
- Create: `apps/desktop/src/app/lunar-city/game/create-game.test.ts`
- Modify: `apps/desktop/src/app/lunar-city/index.tsx`

**Interfaces:**
- Consumes: manifest v1 and atlas URLs.
- Produces: `loadWorldManifest(url): Promise<WorldManifest>` and `createLunarCityGame(parent, manifest, emit): Promise<LunarCityGameHandle>` where the handle exposes `applySnapshot`, `focus`, and `destroy`.

- [ ] **Step 1: Install the approved engine version**

```bash
npm install phaser@3.90.0 --workspace apps/desktop
```

- [ ] **Step 2: Write the failing lifecycle test**

```ts
it('destroys the game and listeners when the host unmounts', async () => {
  const handle = await createLunarCityGame(parent, manifest, emit)
  handle.destroy()
  expect(fakeGame.destroy).toHaveBeenCalledWith(true)
  expect(listenerCount()).toBe(0)
})
```

- [ ] **Step 3: Implement the lazy game boundary**

```ts
export type EntityKey = string & { readonly __entityKey: unique symbol }
export type NavigationNodeId = string
export type AuthorityState = 'authoritative' | 'partial' | 'stale' | 'unknown'
export type DestinationId = 'project' | 'library' | 'lab' | 'depot' | 'bus' | 'review' | 'triage' | 'garden' | 'council' | 'unknown'
export type EntityIdentity =
  | { kind: 'profile'; connectionId: string; profile: string }
  | { kind: 'session'; connectionId: string; profile: string; sessionId: string }
  | { kind: 'subagent'; connectionId: string; profile: string; sessionId: string; subagentId: string }
  | { kind: 'kanban'; connectionId: string; board: string; taskId: string; runId?: string }
export interface SourceHealth { source: string; authority: AuthorityState; observedAt: number; error?: string }
export interface ObservedState { source: 'session' | 'subagent' | 'kanban'; status: string; fresh: boolean }
export interface SpatialState { animation: string; destination: DestinationId; authority: AuthorityState }
export interface LunarEntity { key: EntityKey; identity: EntityIdentity; observedAt: number; authority: AuthorityState; destination: DestinationId; animation: string }
export interface LunarCitySnapshot { revision: number; observedAt: number; entities: ReadonlyMap<EntityKey, LunarEntity>; sources: readonly SourceHealth[] }
export interface LunarDelta { revision: number; upserts: readonly LunarEntity[]; removals: readonly EntityKey[]; sources: readonly SourceHealth[] }
export interface AtlasRef { key: string; image: string; data: string }
export interface ManifestFrame { id: string; atlas: string; frame: string; x: number; y: number; depthBand: number }
export interface NavigationGraph { nodes: readonly { id: NavigationNodeId; x: number; y: number }[]; edges: readonly { from: NavigationNodeId; to: NavigationNodeId; cost: number }[] }
export interface WorldManifest { version: 1; source: { sha256: string }; atlases: readonly AtlasRef[]; frames: readonly ManifestFrame[]; navigation: NavigationGraph }
export type LunarCityIntent = { kind: 'select'; entityKey: EntityKey } | { kind: 'open-leader'; entityKey: EntityKey }
export type EmitIntent = (intent: LunarCityIntent) => void
export interface LunarCityWorldScene {
  applySnapshot(snapshot: LunarCitySnapshot): void
  focus(key: EntityKey): void
}
export interface LunarCityGameHandle {
  applySnapshot(snapshot: LunarCitySnapshot): void
  focus(key: EntityKey): void
  destroy(): void
}

export async function createLunarCityGame(parent: HTMLElement, manifest: WorldManifest, emit: EmitIntent) {
  const { default: Phaser } = await import('phaser')
  const scene = createWorldScene(Phaser, manifest, emit)
  const game = new Phaser.Game({ type: Phaser.AUTO, parent, physics: undefined, scene, transparent: true })
  return {
    applySnapshot: snapshot => scene.applySnapshot(snapshot),
    focus: key => scene.focus(key),
    destroy: () => game.destroy(true)
  }
}
```

Configure `Phaser.AUTO`, no physics, transparent canvas, resize scaling, pixel rounding, antialiasing, and the world scene. Load only the modular atlases; never load the approved JPG.

- [ ] **Step 4: Reconstruct the approved static composition**

Create terrain/building/prop objects from manifest anchors and depth bands. Add a deterministic camera preset matching the approved 1280x910 composition and transparent Phaser hit areas for leaders/buildings.

- [ ] **Step 5: Verify and commit**

```bash
npm run test:ui --workspace apps/desktop -- src/app/lunar-city/game/create-game.test.ts
npm run typecheck --workspace apps/desktop
npm run build --workspace apps/desktop
git add apps/desktop/package.json package-lock.json apps/desktop/src/app/lunar-city
git commit -m "feat(desktop): reconstruct Lunar City in Phaser"
```

### Task 4: Define collision-safe identities and truthful state mapping

**Files:**
- Modify: `apps/desktop/src/app/lunar-city/model.ts`
- Create: `apps/desktop/src/app/lunar-city/identity.ts`
- Create: `apps/desktop/src/app/lunar-city/state-map.ts`
- Create: `apps/desktop/src/app/lunar-city/model.test.ts`
- Create: `apps/desktop/src/app/lunar-city/state-map.test.ts`

**Interfaces:**
- Produces: `entityKey(identity): EntityKey`, `mapObservedState(input): SpatialState`, and `LunarCitySnapshot`.

- [ ] **Step 1: Write failing identity and unknown-state tests**

```ts
expect(entityKey({ kind: 'profile', connectionId: 'a', profile: 'worker' })).not.toBe(
  entityKey({ kind: 'profile', connectionId: 'b', profile: 'worker' })
)
expect(mapObservedState({ source: 'kanban', status: 'mystery', fresh: true })).toEqual({
  animation: 'unavailable', destination: 'unknown', authority: 'unknown'
})
```

- [ ] **Step 2: Implement the exact model**

```ts
const STATE_MAP: Readonly<Record<string, SpatialState>> = {
  ready: { animation: 'queue', destination: 'bus', authority: 'authoritative' },
  running: { animation: 'work', destination: 'project', authority: 'authoritative' },
  working: { animation: 'work', destination: 'project', authority: 'authoritative' },
  resource: { animation: 'queue', destination: 'depot', authority: 'authoritative' },
  review: { animation: 'review', destination: 'review', authority: 'authoritative' },
  blocked: { animation: 'blocked', destination: 'triage', authority: 'authoritative' },
  failed: { animation: 'failed', destination: 'triage', authority: 'authoritative' },
  heartbeat: { animation: 'heartbeat', destination: 'garden', authority: 'authoritative' },
  idle: { animation: 'rest', destination: 'garden', authority: 'authoritative' },
  paused: { animation: 'rest', destination: 'garden', authority: 'authoritative' },
  orchestration: { animation: 'work', destination: 'council', authority: 'authoritative' },
  dependency: { animation: 'queue', destination: 'council', authority: 'authoritative' },
  done: { animation: 'done', destination: 'project', authority: 'authoritative' }
}

export function entityKey(identity: EntityIdentity): EntityKey {
  const fields = identity.kind === 'profile' ? [identity.kind, identity.connectionId, identity.profile]
    : identity.kind === 'session' ? [identity.kind, identity.connectionId, identity.profile, identity.sessionId]
    : identity.kind === 'subagent' ? [identity.kind, identity.connectionId, identity.profile, identity.sessionId, identity.subagentId]
    : [identity.kind, identity.connectionId, identity.board, identity.taskId, identity.runId ?? '']
  return fields.map(value => encodeURIComponent(String(value))).join(':') as EntityKey
}

export function mapObservedState(input: ObservedState): SpatialState {
  if (!input.fresh) return { animation: 'unavailable', destination: 'unknown', authority: 'stale' }
  return STATE_MAP[input.status] ?? { animation: 'unavailable', destination: 'unknown', authority: 'unknown' }
}
```

Map `ready→bus`, resource waits to `depot`, review to `review`, failure/blocked diagnostics to `triage`, heartbeat/idle/pause to `garden`, orchestration/dependencies to `council`, completion to `project`, and unknown to `unknown`.

- [ ] **Step 3: Run, commit**

```bash
npm run test:ui --workspace apps/desktop -- src/app/lunar-city/model.test.ts src/app/lunar-city/state-map.test.ts
git add apps/desktop/src/app/lunar-city/model.ts apps/desktop/src/app/lunar-city/identity.ts apps/desktop/src/app/lunar-city/state-map.ts apps/desktop/src/app/lunar-city/*.test.ts
git commit -m "feat(desktop): model truthful Lunar City state"
```

### Task 5: Add navigation, animation, pooling, and frame scheduling

**Files:**
- Create: `apps/desktop/src/app/lunar-city/game/navigation.ts`
- Create: `apps/desktop/src/app/lunar-city/game/entities.ts`
- Create: `apps/desktop/src/app/lunar-city/game/scheduler.ts`
- Create: `apps/desktop/src/app/lunar-city/game/navigation.test.ts`
- Create: `apps/desktop/src/app/lunar-city/game/entities.test.ts`
- Create: `apps/desktop/src/app/lunar-city/game/scheduler.test.ts`
- Modify: `apps/desktop/src/app/lunar-city/game/world-scene.ts`

**Interfaces:**
- Produces: `findRoute(graph, from, to): readonly NavigationNodeId[]`, `EntityPool.reconcile(snapshot)`, and `FrameScheduler.setMode('interactive'|'ambient'|'suspended')`.

- [ ] **Step 1: Write failing deterministic-route and visibility tests**

```ts
expect(findRoute(graph, 'project:a', 'review')).toEqual(['project:a', 'junction:2', 'review'])
document.dispatchEvent(new Event('visibilitychange'))
expect(clock.targetFps).toBe(0)
```

- [ ] **Step 2: Implement graph routing and pooled entities**

Use Dijkstra over manifest edges only when a destination changes. Reconcile keyed entities in place; never recreate unchanged sprites. Set depth from foot-anchor Y plus manifest depth band. Distant populations collapse into a labeled aggregate whose count and state distribution come from the snapshot.

- [ ] **Step 3: Implement the 30/15/0 scheduler**

Interactive input selects 30 FPS for five seconds, then returns to 15 FPS. `document.hidden` (including Electron minimization) or unmount selects zero FPS and sleeps the scene. Reduced motion moves directly to destinations and uses static declared frames.

- [ ] **Step 4: Verify and commit**

```bash
npm run test:ui --workspace apps/desktop -- src/app/lunar-city/game
git add apps/desktop/src/app/lunar-city/game
git commit -m "feat(desktop): animate Lunar City efficiently"
```

### Task 6: Build the profile, session, and subagent live adapter

**Files:**
- Create: `apps/desktop/src/app/lunar-city/store.ts`
- Create: `apps/desktop/src/app/lunar-city/adapters/fleet.ts`
- Create: `apps/desktop/src/app/lunar-city/adapters/sessions.ts`
- Create: `apps/desktop/src/app/lunar-city/adapters/reconciler.ts`
- Create: `apps/desktop/src/app/lunar-city/adapters/fleet.test.ts`
- Create: `apps/desktop/src/app/lunar-city/adapters/sessions.test.ts`
- Create: `apps/desktop/src/app/lunar-city/adapters/reconciler.test.ts`
- Create: `apps/desktop/src/app/lunar-city/store.test.ts`
- Modify: `apps/desktop/src/app/lunar-city/index.tsx`

**Interfaces:**
- Consumes: `$fleetRoster`, session rows, `$subagentsBySession`, and native `subagent.*` events.
- Produces: `$lunarCitySnapshot`, `startLunarCityReconciler(): () => void`, and ordered `LunarDelta` values.

- [ ] **Step 1: Write failing duplicate-profile and terminal-subagent tests**

```ts
expect(normalizeRoster(roster).map(row => row.key)).toEqual(['profile:local:worker', 'profile:ssh-1:worker'])
expect(normalizeSubagent(completeEvent).authority).toBe('authoritative')
```

- [ ] **Step 2: Implement event-first reconciliation**

Refresh the fleet roster on mount/focus/registry change, never per frame. Fold session/subagent updates by revision. Mark a source stale after its declared freshness window; retain last-known identity and timestamp. A sequence gap or reconnect schedules one bounded reread and deduplicates concurrent rereads.

- [ ] **Step 3: Apply snapshots without React animation renders**

`index.tsx` subscribes once and calls `game.applySnapshot(snapshot)` imperatively. React state contains selection/dialog/confirmation only.

- [ ] **Step 4: Verify and commit**

```bash
npm run test:ui --workspace apps/desktop -- src/app/lunar-city/adapters src/app/lunar-city/store.test.ts
git add apps/desktop/src/app/lunar-city
git commit -m "feat(desktop): populate Lunar City from Hermes sessions"
```

### Task 7: Add optional Kanban and project compounds

**Files:**
- Create: `apps/desktop/src/app/lunar-city/adapters/kanban.ts`
- Create: `apps/desktop/src/app/lunar-city/adapters/kanban.test.ts`
- Modify: `apps/desktop/src/app/lunar-city/adapters/reconciler.ts`
- Modify: `apps/desktop/src/app/lunar-city/game/world-scene.ts`

**Interfaces:**
- Consumes: `pluginRest('kanban', ...)` and `pluginSocket('kanban', ...)`.
- Produces: `createKanbanCitySource(): KanbanCitySource`, board/task/run/worker entities, and stable connection/project compounds.

- [ ] **Step 1: Write failing unavailable-plugin and event-gap tests**

```ts
await expect(source.read()).resolves.toMatchObject({ health: 'unavailable', entities: [] })
expect(source.onFrame({ events: [{ id: 9 }] }).needsReconcile).toBe(true)
```

- [ ] **Step 2: Implement reads and socket invalidation**

Read `/boards`, `/board`, and `/workers/active` at initial reconciliation. Read `/tasks/{id}` only when that task is selected or an event invalidates its visible diagnostic state. Subscribe through `pluginSocket('kanban', '/events', ...)`; use the socket to invalidate and bounded REST reads as authority. Missing plugin/API closes Kanban buildings and actions without failing the rest of Lunar City.

- [ ] **Step 3: Build stable project compounds**

Key compounds by connection plus canonical project/repository ID. Allocate manifest compound slots deterministically; overflow uses paged camera regions, not overlapping sprites. Route tasks by the state map and keep task, run, blocker, diagnostic, comment, and log evidence separate.

- [ ] **Step 4: Verify and commit**

```bash
npm run test:ui --workspace apps/desktop -- src/app/lunar-city/adapters/kanban.test.ts
git add apps/desktop/src/app/lunar-city
git commit -m "feat(desktop): map Kanban work into Lunar City"
```

### Task 8: Add persistent leader text and voice conversations

**Files:**
- Create: `apps/desktop/src/app/lunar-city/leader-sessions.ts`
- Create: `apps/desktop/src/app/lunar-city/leader-sessions.test.ts`
- Create: `apps/desktop/src/app/lunar-city/components/leader-dialogue.tsx`
- Create: `apps/desktop/src/app/lunar-city/components/leader-dialogue.test.tsx`
- Modify: `apps/desktop/src/app/lunar-city/index.tsx`

**Interfaces:**
- Produces: `resolveLeaderSession(owner): Promise<LeaderSession>` and a dialogue component that uses standard `prompt.submit`, transcript events, and existing voice hooks.

- [ ] **Step 1: Write failing ownership and persistence tests**

```ts
export interface LeaderOwner { connectionId: string; profile: string }
export interface LeaderSession { storedId: string; runtimeId: string }
expect(await resolveLeaderSession({ connectionId: 'a', profile: 'owl' })).toEqual({ storedId: 's1', runtimeId: 'r1' })
expect(requestGatewayForAgent).not.toHaveBeenCalledWith('b', expect.anything(), expect.anything(), expect.anything())
```

- [ ] **Step 2: Implement durable mapping**

Persist `{version:1, leaders:{[connectionId+'::'+profile]: storedSessionId}}` in a dedicated desktop store. Verify the stored row still belongs to that exact owner; otherwise create a normal session through the existing profile-scoped session API and replace the mapping.

- [ ] **Step 3: Implement streamed text conversation**

Reuse the standard session state slice and `requestForSessionProfile`. The panel shows transcript, text composer, speaking/thinking/error state, and “Open full chat.” It never implements a second conversation protocol.

- [ ] **Step 4: Reuse existing voice behavior**

Mount the existing `useVoiceConversation` hook with the leader session's composer scope and routed submit/interrupt callbacks. Reuse the existing transcription ladder and voice playback helpers. Preserve wake-word pause/resume, microphone ownership, barge-in, stop phrases, streamed TTS, and text fallback without copying their implementations.

- [ ] **Step 5: Verify and commit**

```bash
npm run test:ui --workspace apps/desktop -- src/app/lunar-city/leader-sessions.test.ts src/app/lunar-city/components/leader-dialogue.test.tsx src/app/chat/composer/hooks/use-voice-conversation.test.tsx
git add apps/desktop/src/app/lunar-city
git commit -m "feat(desktop): talk with Lunar City leaders"
```

### Task 9: Add evidence inspectors and identity-safe command brokerage

**Files:**
- Create: `apps/desktop/src/app/lunar-city/command-broker.ts`
- Create: `apps/desktop/src/app/lunar-city/command-broker.test.ts`
- Create: `apps/desktop/src/app/lunar-city/components/entity-inspector.tsx`
- Create: `apps/desktop/src/app/lunar-city/components/command-confirmation.tsx`
- Create: `apps/desktop/src/app/lunar-city/components/entity-inspector.test.tsx`
- Create: `apps/desktop/src/app/lunar-city/components/command-confirmation.test.tsx`

**Interfaces:**
- Produces: `planCommand(intent, snapshot): CommandPlan` and `executeCommand(plan): Promise<CommandReceipt>`.

- [ ] **Step 1: Write failing safety tests**

```ts
export type CommandVerification = 'verified' | 'rejected' | 'timed_out' | 'verification_required'
export interface ReadbackPlan { kind: 'session' | 'subagent' | 'kanban-task' | 'kanban-run'; id: string }
export interface CommandPlan { entityKey: EntityKey; owner: LeaderOwner; method: string; params: Record<string, unknown>; consequence: string; confirmation: boolean; readback: ReadbackPlan }
export interface CommandReceipt { verification: CommandVerification; identity: EntityIdentity; response?: unknown; error?: string }
expect(planCommand({ kind: 'open-session', entityKey }, snapshot).confirmation).toBe(false)
expect(planCommand({ kind: 'terminate-run', entityKey }, snapshot).confirmation).toBe(true)
expect(() => planCommand(ambiguousIntent, ambiguousSnapshot)).toThrow(/owner is ambiguous/)
```

- [ ] **Step 2: Implement pure planning**

`CommandPlan` contains exact identity, owner route, method, typed parameters, consequence text, confirmation flag, and readback. Direct allowlist: open session, inspect evidence, send ordinary guidance. Every other supported mutation is confirmed.

- [ ] **Step 3: Implement existing routed operations only**

Use `prompt.submit`, `session.steer`, `session.interrupt`, `subagent.steer`, and `subagent.interrupt` through the owning gateway route. Use Kanban plugin endpoints for reclaim, reassign, dispatch, patch, and run termination. Never construct shell commands.

- [ ] **Step 4: Require authoritative readback**

After mutation, reread the session/subagent registry or Kanban task/run. Return `verified`, `rejected`, `timed_out`, or `verification_required`; only `verified` changes authoritative animation.

- [ ] **Step 5: Verify and commit**

```bash
npm run test:ui --workspace apps/desktop -- src/app/lunar-city/command-broker.test.ts src/app/lunar-city/components
git add apps/desktop/src/app/lunar-city
git commit -m "feat(desktop): control Lunar City workers safely"
```

### Task 10: Add accessibility, fallback, and visual regression

**Files:**
- Create: `apps/desktop/src/app/lunar-city/components/entity-list.tsx`
- Create: `apps/desktop/src/app/lunar-city/components/source-health.tsx`
- Create: `apps/desktop/e2e/fixtures/lunar-city-dynamic-mask.png`
- Modify: `apps/desktop/src/app/lunar-city/index.tsx`, `lunar-city.css`, `index.test.tsx`
- Modify: `apps/desktop/e2e/lunar-city.spec.ts`

**Interfaces:**
- Produces: a keyboard/screen-reader equivalent for all game selection and a React-only fallback when Phaser cannot render.

- [ ] **Step 1: Write failing keyboard and context-loss tests**

```ts
expect(screen.getByRole('button', { name: /Pip.*working.*Research Lab/i })).toBeVisible()
fireEvent(window, new Event('webglcontextlost'))
expect(screen.getByText(/world renderer unavailable/i)).toBeVisible()
expect(screen.getByRole('button', { name: /Talk to Fox Scientist/i })).toBeEnabled()
```

- [ ] **Step 2: Implement synchronized accessible entities**

Use native controls ordered by manifest/camera order. Pair every color/state with text. Reduced motion places entities at destinations and uses static frames. Renderer failure leaves leader dialogue, evidence, and authorized actions usable.

- [ ] **Step 3: Add deterministic reconstruction regression**

Capture the default camera with inhabitants and animated masks disabled. Exclude only pixels marked in `lunar-city-dynamic-mask.png`; fail when more than 2.5% of remaining pixels exceed a per-channel difference of 24. Additionally assert the approved JPG is absent from canvas loader requests.

- [ ] **Step 4: Verify and commit**

```bash
npm run test:ui --workspace apps/desktop -- src/app/lunar-city
npm run build --workspace apps/desktop
npm exec --workspace apps/desktop -- playwright test e2e/lunar-city.spec.ts
git add apps/desktop/src/app/lunar-city apps/desktop/e2e/lunar-city.spec.ts
git commit -m "test(desktop): verify playable Lunar City access"
```

### Task 11: Enforce performance and packaged-Electron acceptance

**Files:**
- Create: `apps/desktop/scripts/perf/lunar-city.mjs`
- Create: `apps/desktop/scripts/perf/lunar-city.test.mjs`
- Modify: `apps/desktop/package.json`
- Create: `apps/desktop/e2e/lunar-city-live-sources.spec.ts`
- Modify: `docs/lunar-city-design-handoff.md`, `design-qa.md`

**Interfaces:**
- Produces: a JSON performance receipt with hardware, OS, Electron, power state, scale, FPS/frame-time, CPU delta, GPU delta, memory drift, and listener/entity/timer counts.

- [ ] **Step 1: Write failing receipt-schema tests**

```js
assert.equal(validateReceipt({ scenario: 'hidden', phaserFrames: 1 }).ok, false)
assert.match(validateReceipt({ scenario: 'hidden', phaserFrames: 1 }).errors[0], /zero frames/)
```

- [ ] **Step 2: Implement measured scenarios**

Add packaged scenarios for hidden/minimized, visible idle, 25, 100, and 250 inhabitants, camera movement, active leader voice/text conversation, WebGL loss, and 30-minute stability. The script records raw samples and evaluates the exact budgets from the spec.

- [ ] **Step 3: Run complete desktop verification**

```bash
npm run typecheck --workspace apps/desktop
npm run lint --workspace apps/desktop
npm run test:ui --workspace apps/desktop -- src/app/lunar-city
npm run build --workspace apps/desktop
npm exec --workspace apps/desktop -- playwright test e2e/lunar-city.spec.ts e2e/lunar-city-live-sources.spec.ts
npm run perf:lunar-city --workspace apps/desktop
git diff --check
```

Expected: all deterministic tests pass; packaged Electron passes; every performance budget is green. Mock and performance receipts are labeled as such.

- [ ] **Step 4: Capture supervised live Hermes acceptance separately**

With explicit operator supervision, capture real receipts for profile enumeration, leader session persistence, text, voice, subagent progress, Kanban events, one safe guidance action, one confirmed disruptive action, disconnect/reconnect, and readback. Do not weaken identity, freshness, or authority gates to obtain a green receipt.

- [ ] **Step 5: Update handoff and commit**

Record exact HEAD, clean status, commands, counts, receipt paths, evidence classes, known constraints, and build stamp:

```bash
git add apps/desktop/scripts/perf apps/desktop/package.json apps/desktop/e2e/lunar-city-live-sources.spec.ts docs/lunar-city-design-handoff.md design-qa.md
git commit -m "test(desktop): certify playable Lunar City"
git status --short --branch
git rev-parse HEAD
```

Expected: clean worktree and an exact final commit. Push/PR/merge remain operator-controlled unless separately requested.

# Lunar City Bot Crossing Integration Design

Date: 2026-09-03

Status: design proposed for review

## Purpose

Bring the useful place-making behavior from
[`jarrenrocks/bot-crossing`](https://github.com/jarrenrocks/bot-crossing) into
Hermes Lunar City without replacing Lunar City's exact-owner data model,
Babylon renderer, approved asset contract, or command authority.

The result should feel like one persistent colony rather than a collection of
floating status markers: projects keep their ground, active work is visually
prioritized, the operator can understand a zone and its inhabitants from one
surface, and the lighting/world can be explored without changing Hermes state.

## Existing strengths to preserve

Lunar City already provides the parts Bot Crossing cannot supply safely for
Hermes:

- exact connection/profile/session/subagent/Kanban identity;
- multi-connection reconciliation with stale, partial, and unknown authority;
- manifest-bound Babylon models and navigation geometry;
- Recast route construction, camera focus/follow, occlusion handling, LOD,
  reduced-motion support, and a single frame scheduler;
- exact-owner leader dialogue and bounded command execution;
- accessible text-first controls and renderer-degraded operation.

No external scanner, Claude session reader, Three.js colony, or second command
authority will be introduced.

## Bot Crossing findings and model reuse

The reference project has four especially useful ideas:

1. status is resolved by explicit precedence rather than independent visual
   flags;
2. project zones retain their root cells and survive roster changes;
3. ground scatter is rebuilt when plot footprints change so props cannot end up
   underneath newly created decks;
4. camera, routing, arrival, spacing, labels, and world lighting are treated as
   one place-making system.

Its checked-in `spacebase.glb`, `crew.glb`, and `forest.glb` are built from
KayKit packs and are documented as CC0 1.0, while the repository code is MIT.
They are suitable as analysis material and potentially as source geometry, but
they are not automatically interchangeable with our current approved GLBs:

- they use a Three.js-oriented scene and node contract;
- their geometry/material/animation budgets have not yet been checked against
  our manifest budgets;
- `crew.glb` has a different animation vocabulary and rig contract;
- importing one as runtime art would require a new manifest entry, bounds,
  navigation/occlusion metadata, integrity hash, and provenance record.

The implementation will therefore have an asset-audit step first. The models
may be used in one of three explicit ways:

- as a measured visual reference for rebuilding compatible Lunar City assets;
- as a CC0 source mesh for a new, manifest-bound compound/prop pack after the
  audit passes;
- as runtime assets only when the resulting GLBs meet the existing loader,
  budget, naming, integrity, and license-credit gates.

The existing approved Lunar City models remain the default runtime assets until
that audit proves a replacement or additive pack is safe. The reference
repository's files will not be fetched by the application at runtime.

## Goals

- Make project compounds visibly persistent and bounded rather than invisible
  transform anchors.
- Add a canonical presentation status with badges and zone urgency, using only
  authoritative signals or explicitly stale/unknown states.
- Add a unified zone/thread interaction surface that complements the existing
  entity inspector and exact-owner commands.
- Add Luna, Mars, and Terra world presets plus a user-controlled day/night
  value, while keeping all of it presentation-only.
- Improve worker arrival and crowd spacing around existing Recast paths without
  allowing agents to cross declared obstacles or invent routes.
- Preserve low-power quality tiers, reduced motion, accessibility, and the
  single-scheduler frame invariant.
- Add model analysis and provenance receipts so external art can be reused
  responsibly and reproducibly.

## Non-goals

- Replacing the Hermes adapters with filesystem scanning or Claude-specific
  session records.
- Replacing Babylon with Three.js or embedding Bot Crossing as an iframe.
- Adding game economy, combat, resource simulation, autonomous work claims, or
  model-generated progress.
- Inferring unread, errors, merges, or work from a title, path, animation, or
  stale observation when the authoritative source did not provide that signal.
- Giving the browser or Babylon runtime direct access to gateway mutation APIs.
- Automatically replacing approved assets merely because an external model is
  attractive.

## Architecture

### 1. Canonical city status

Add a small pure status module that consumes a `LunarEntity` plus an explicit
`now` value and returns:

- one status from `blocked`, `working`, `celebrating`, `waiting`, `sleeping`,
  or `idle`;
- a human label;
- an optional badge (`!`, `⚒`, `✓`, `?`);
- whether the project zone is urgent or active.

Precedence will be deterministic and documented. A signal is eligible only when
it comes from an authoritative entity field or a source observation that is
still fresh. Missing signals stay absent. Partial, stale, unknown, or
disconnected entities remain visually unavailable rather than being upgraded
to working/celebrating.

The resolver will be reused by the accessible entity list, zone summary, and
world presentation so these surfaces cannot disagree.

### 2. Persistent project zones

Add a presentation-only layout store keyed by the exact compound identity
already used by Kanban: `(connectionId, projectId)`.

- Reuse the existing retained-slot allocation logic as the first rung.
- Extend the manifest's bounded project-slot pool so the number of supported
  simultaneous compounds is explicit rather than invented at runtime.
- Keep a project root fixed while its footprint grows or shrinks.
- Reuse the same slot after a reload when the identity is present again.
- Retain a bounded memory of inactive projects, with deterministic eviction.
- Persist only visual layout data under a versioned Lunar City local-storage
  key; never persist gateway truth, task state, credentials, or raw transcripts.
- If there is no valid slot, leave the project unplaced and expose that state in
  text; do not place it by hashing a title or silently overlap another zone.

Dynamic compound pads, kerbs, labels, and optional low-cost props will be
created by the Babylon world layer and tagged as non-authoritative presentation
nodes. Reconciliation will update them from the snapshot and remove them when
their source disappears. Scatter/prop invalidation will happen after the
footprint is known, matching the reference project's ordering guarantee.

### 3. Zone/thread interaction

Add a focused project-zone panel that follows the existing desktop interaction
rules:

- selecting a compound, label, or entity selects the same exact zone;
- the zone panel shows project identity, connection, task count, status counts,
  source health, and the bounded list of exact entities;
- selecting an entity opens the current inspector path;
- opening a session continues through the existing exact-owner callback;
- archive/new-conversation actions are enabled only where the existing
  capability and exact owner are available, otherwise visibly disabled;
- background refreshes never navigate, steal focus, or open a panel.

The panel will complement, not replace, the current accessible operations and
leader dialogue surfaces.

### 4. World presets and day/night

Add a data-driven presentation preset contract, initially:

- Luna: charcoal regolith, hard neutral key light, deep-space sky;
- Mars: rust ground, warmer haze, lower-contrast fill;
- Terra: muted soil/green scatter, softer sky and ambient fill.

Each preset owns colors, light ranges, fog, scatter tint, and whether the
environment/glow path is enabled at each quality tier. A normalized time-of-day
value drives the sun arc, clear color, ambient fill, emissive plot lights, and
optional sky/environment refresh. It must not create a continuous animation in
efficient mode unless the user is actively scrubbing or has enabled it.

The world handle will expose presentation-only methods such as
`setWorldPreset` and `setTimeOfDay`. These methods will be driven by a small
settings surface and will request a render through the existing scheduler.

### 5. Arrival, spacing, and obstacle behavior

Audit the existing navigation controller against the reference behavior before
changing it. Where gaps exist, add pure, bounded helpers for:

- string-pulling/reducing route waypoints;
- obstacle-safe movement and slide-along response;
- deterministic standing-spot selection outside building bounds;
- crowd separation using the declared worker radius;
- bounded blocked-time fallback to the last reachable ground point.

These helpers will be applied only to presentation positions. They will never
change `LunarEntity.position`, destination authority, task progress, or source
state. Recast remains the route authority; no straight-line fallback is added
when a valid route is unavailable.

## Asset audit and build contract

Add a deterministic audit script for the three external GLBs that records,
without embedding raw model contents in logs:

- SHA-256 and byte size;
- node, mesh, material, texture, skin, animation, and triangle counts;
- animation names and duration bounds;
- bounding boxes and likely navigation/prop candidates;
- license/provenance source and transformation tool version.

If an asset is adopted, the output becomes a reviewed manifest entry and the
credits file is copied into the Lunar City asset directory. The runtime uses
local checked-in assets only and verifies the manifest hash/URI contract already
enforced by `manifest.ts`.

## Files and seams

Likely additions or focused changes:

- `apps/desktop/src/app/lunar-city/city-status.ts` and tests;
- `apps/desktop/src/app/lunar-city/zone-layout.ts` and tests;
- `apps/desktop/src/app/lunar-city/world-presets.ts` and tests;
- project-zone React component plus Lunar City styles/tests;
- `model.ts`, `world-scene.ts`, `create-world.ts`, and `world/navigation.ts`
  only at the typed seams described above;
- manifest/project-slot data and asset provenance under
  `apps/desktop/public/lunar-city/v2/`;
- a bounded external-model audit script and its non-secret receipt fixture.

Existing adapters remain the only source of live data. Existing command broker
and executor code remains the only mutation path.

## Error handling and safety

- Malformed external model metadata fails the asset audit and is not loaded.
- Missing/stale/ambiguous source data produces an honest unavailable or
  unplaced state, never a guessed location or status.
- Local-storage corruption is ignored and replaced with an empty layout; the
  app remains usable and the current snapshot can re-seed placement.
- A failed authoritative command rolls back/clears optimistic UI state and is
  surfaced to the operator; it never falls through to another owner.
- Renderer failure leaves the existing text-first operations available.
- All timers, listeners, animation groups, temporary meshes, and route-local
  resources are disposed on route teardown.

## Testing and acceptance evidence

Focused unit tests will cover:

- status precedence and no-inference behavior;
- stable root-cell/slot retention, bounded overflow, persistence corruption,
  and exact compound-key collisions;
- world-preset/time mapping and efficient-tier scheduler behavior;
- waypoint reduction, obstacle clearance, spacing, and blocked fallback;
- manifest and external asset-audit contract validation.

Desktop/E2E coverage will verify:

- project zones and labels render from the real mock-backed snapshot;
- a refresh does not move an unchanged zone;
- stale/partial sources remain visibly unavailable;
- selecting a zone and selecting one of its exact entities converge on the same
  inspector/command path;
- planet/time controls update presentation without changing backend state;
- renderer degradation preserves accessible operations.

Acceptance will distinguish focused UI/static/asset-audit evidence from full
desktop E2E and packaged-app evidence. A successful typecheck or screenshot
alone is not acceptance for the live-source or command-bound paths.


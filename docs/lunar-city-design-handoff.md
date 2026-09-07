# Lunar City Visualizer Handoff

> **Current 3D handoff (2026-08-31):** This file's original image-backed notes below are historical and superseded by the current implementation record in this banner and the linked design QA addendum. The runtime is a genuine Babylon 3D world; the approved JPG is a visual reference only, never the runtime background. The final 3D implementation source tip is `b40e67825121cd0edb99b64e3a1bedcfbd19ec96`; this documentation update is committed at `3c16a086d745ca44d7a57af75dcfe15f301123d8`. Source/unit lanes are review-clean, but no clean packaged GPU/CPU, visual-regression, 30-minute, or supervised-live acceptance receipt exists.

## 2026-08-31/09-01 lighting + hero-building addenda

The 3D world (below) is still short of the approved reference art and the
user's inspiration video/PNGs, though less so after two follow-up passes:

- `.superpowers/sdd/2026-08-31-lunar-city-lighting-pass/report.md`: a
  lighting/material pass (key/rim/fill lights, a glow layer for the
  emissive accents, per-material PBR recipes) plus the original gap
  analysis, and `scripts/lunar-city/render-webgl-preview.mjs`, a real-WebGL
  screenshot tool for grading geometry/material changes without an
  Electron packaging cycle.
- `.superpowers/sdd/2026-09-01-lunar-city-hero-building-walls/report.md`:
  solid walls/windows/roof cap added to the Library and Research Lab (the
  two hero buildings) so they read as enclosed volumes instead of a thin
  pillar/beam armature, while keeping the approved open-front interior
  view. Read this one before touching any other building's geometry — it
  documents a real trap: `build-models.test.mjs`'s silhouette-uniqueness
  test will fail if a building's added wall mass makes its normalized
  silhouette converge on another building's, and explains why Research Lab
  only got window glow rather than the Library's full wall treatment.

**Continuing this work? Start at `docs/lunar-city-video-reference-roadmap.md`.**
It is the top-level, full-journey handoff from the current state to the
user's video-reference endpoint — it resolves what that video actually
means for this project (concept, not palette; read it before assuming
otherwise), inventories what's already built, defines what "done" looks
like, and sequences every remaining phase (geometry, cinematic camera,
focus/hero-shot treatment, floating status badges, remaining fidelity
items, packaging/acceptance). `docs/lunar-city-handoff-2026-09-01.md` is
its Phase 0 detail doc: the step-by-step per-building geometry loop and
budget table for the 9 remaining specialist buildings.

**Switching to a session with local Blender/desktop access?** Start at
`docs/lunar-city-handoff-2026-09-01-local-session.md` instead — it covers
everything above plus what changed after (baked AO, real shadows,
tonemapping, fog, the approved `charcoal-structure` palette fix) and the
two open problems a local session is positioned to actually fix: the
geometry is primitive-box modeling with no bevels/booleans/subdivision,
and the district layout is 12 hand-picked coordinates with no city-planning
logic behind them (confirmed by reading `terrain.mjs`'s `DISTRICTS`
constant directly — there is no generative plan there at all).

## Current implementation addendum

- `/lunar-city` is a dedicated left-sidebar destination immediately after Kanban. Arranged Sim-City/StarCraft-like 3D camera controls support orbit, pan, tilt, zoom, focus, follow, occlusion, interiors, visibility pausing, context recovery, and real world teardown.
- Runtime assets are `apps/desktop/public/lunar-city/v2/` (terrain, buildings, leaders, workers, textures, and manifest). The approved JPG at `apps/desktop/public/lunar-city/moon-settlement-approved.jpg` is not rendered as a background.
- Six leader families, 19 group kits, and 2,888 modular worker signatures use shared low-cost resources. A global 24 animated-visual cap, LOD/culling/aggregation, parked resting workers, bounded readers, and demand-driven scheduling protect CPU/GPU.
- Profiles, groups, sessions, subagents, Kanban tasks, runs, and workers merge across bounded registered connection/profile owners. Canonical identities preserve duplicate IDs across connections; stale/failed/empty/oversized/null rosters fail closed.
- Physical leader picks and collision-only accessibility selectors invoke the same production handler, show exact connection/profile identity, focus the selected node, and open the persistent exact-owner conversation. Text/voice and commands remain exact-owner and authority/readback gated.
- The old simulated timer, fabricated progress/rooms/quotes, hidden DOM actors, grid, and disconnected CSS animation layer were removed. Renderer `degraded` is reported as `DEGRADED`, not `STARTING`.

### Evidence and blockers

- Review-clean source/unit receipts include full Lunar suites through 538/538, focused authority/multi-source/bridge/asset lanes, typecheck after npm dependency restoration, and scoped lint/format/diff checks.
- Packaged acceptance remains pending: no clean electron-builder package from this final SHA, no GPU-enabled CPU/RSS/FPS/GPU allocation receipt, no 30-minute stability receipt, and no supervised-live receipt.
- Exact packaged 25/100/250 scenarios remain blocked until an owned authenticated three-gateway lifecycle emits real subagent evidence. Remote Hermes Desktop is unreachable, so the duplicate `test-contract-steward` profile has not been compared or deleted.
- The detailed historical sections below are retained for provenance only; do not use their old SHA, image-backed behavior, six-test receipt, or `dirty:false` stamp as current evidence.

## Purpose

This handoff records the current Lunar City visualizer implementation and the approved design direction so future work does not replace the agreed artwork with a different generated scene or a spreadsheet-like dashboard.

## Current outcome

The desktop app now uses the approved warm red/orange lunar settlement image as the canonical world surface. The image contains the visual identity that must be preserved:

- Open-front lunar buildings with readable room interiors.
- Animal sentients as group leaders: owl, fox, badger, stag, bird/owl, and other animal leaders.
- Small white robot children/helper workers distributed through the settlement.
- Library, research lab, review office, resource depot, bus stop, triage, and break garden.
- Raised walkways and a Sim City/StarCraft-like isometric settlement composition.

The application adds a low-GPU game layer over the image rather than repainting it. Buildings and workers have transparent clickable hotspots; task state, progress, camera controls, inspectors, and room panels remain functional without covering the approved characters or architecture.

## Canonical asset

The source image is committed at:

`apps/desktop/public/lunar-city/moon-settlement-approved.jpg`

Do not replace this with a newly generated board image unless the user explicitly approves a new design. The deleted substitutes were:

- `apps/desktop/public/lunar-city/moon-settlement-board.png`
- `apps/desktop/public/lunar-city/moon-settlement-clean.png`

The visual QA record is in `design-qa.md`.

## Implementation changes

### `apps/desktop/src/app/lunar-city/index.tsx`

- Uses `moon-settlement-approved.jpg` as the terrain image.
- Keeps eight deterministic districts and their task/work data.
- Keeps live task progress advancement on the existing timer.
- Keeps building selection, worker inspection, room entry, room activity, zoom, and reset-camera behavior.
- Repositioned district hotspots to match the approved image composition:
  - Library: upper-left.
  - Research Lab: upper-right.
  - Arts Studio: left/middle.
  - Engineering Workshop: lower-middle.
  - Council Hall: center-right.
  - Release Gatehouse: right/middle.
  - Operations Depot: lower-left.
  - Archive & Acquisition: lower-right.
- Removed procedural inline SVG leader and worker characters. The approved image owns all visible character designs.
- Preserved `design`, `soul`, and `data-worker-design` metadata in the interaction model so future SOUL-derived procedural behavior can be added without painting replacement sprites over the canonical artwork.

### `apps/desktop/src/app/lunar-city/lunar-city.css`

- Building buttons are transparent hotspots with focus treatment only.
- Leader and worker hotspots no longer render replacement figures.
- Hidden signal/light decorations no longer obscure the image.
- The terrain is only lightly adjusted for contrast/saturation.
- The old grid and route overlays are disabled.
- Camera controls were moved below the top HUD so the HUD cannot intercept their clicks.

### `apps/desktop/src/app/lunar-city/index.test.tsx`

- Updated the expected image source to the approved JPG.

### `design-qa.md`

- Records the approved settlement image as the source visual.
- States that the original leaders and robot children must remain visible.
- Records that the current state is deterministic fixture data and that a live Hermes adapter is future work.

## Commits

The restoration is saved in two local commits:

1. `8dcdaae576` — removes the two obsolete substitute board assets.
2. `aee1ad9ca8` — adds the approved JPG and updates the Lunar City source, styling, tests, and QA record.

Current `HEAD` is:

`aee1ad9ca8902d8e05948508680c6fa27b8c2909`

The worktree was clean after the final build. No remote push or PR publication was performed in this handoff.

## Verification receipts

Executed successfully:

- Prettier formatting check.
- Targeted ESLint for Lunar City source, test, and Electron E2E files. CSS was reported as ignored by the ESLint configuration; it produced no lint errors.
- Lunar City UI test suite: 6 tests passed.
- `npm run build` from `apps/desktop`.
- Mock-backed dev-Electron Playwright test (runs `electron .` against built
  `dist/`, not an `electron-builder` package):
  `npx playwright test e2e/lunar-city.spec.ts`
- Final mock/dev-Electron result: 1 test passed.
- `git diff --check` passed.
- `apps/desktop/build/install-stamp.json` reports:

```json
{
  "commit": "aee1ad9ca8902d8e05948508680c6fa27b8c2909",
  "dirty": false,
  "source": "local"
}
```

Final visual captures from the mock-backed dev-Electron run:

- `apps/desktop/test-results/lunar-city-renders-the-lun-a3de9-pens-a-building-detail-view/lunar-city-overview.png`
- `apps/desktop/test-results/lunar-city-renders-the-lun-a3de9-pens-a-building-detail-view/lunar-city-building.png`

## How to continue

Start from the current clean `HEAD`. Inspect the approved asset and the current scene before changing layout or character treatment. The next implementation work should add depth and behavior around the existing art, not replace the art:

1. Replace deterministic fixture data with the real Hermes profile/group/task adapter.
2. Make each profile's curated `SOUL.md` drive role metadata, leader identity, child-worker behavior, animation state, and room assignment.
3. Add low-cost movement along the existing settlement walkways using CSS transforms or a small canvas/scene layer, while keeping the source artwork as the visual base.
4. Represent task states through movement and activity: working, blocked, stalled, triage, review, heartbeat/break, waiting for resources, and done.
5. Add building-specific activity animations that remain subordinate to the artwork: telescope motion in the lab, book/resource interactions in the library/depot, review/triage transitions, bus-stop readiness, and break-garden pauses.
6. Keep counters and inspectors as secondary game HUD elements. Do not turn the screen into a table, legend, or traditional web dashboard.
7. Add tests for adapter mapping, SOUL-derived metadata, state-to-animation mapping, and hotspot interaction.
8. Re-run the mock-backed dev-Electron test after visual changes because Electron has previously crashed or behaved differently from browser-only runs. Run a separately packaged-binary lane before claiming packaged evidence.

## Guardrails

- Do not reintroduce `AnimalLeader`, `RobotChild`, inline SVG characters, or opaque sprite overlays in the Lunar City scene.
- Do not use a blue sci-fi board, blank terrain, grid map, or spreadsheet layout as a replacement for the approved settlement.
- Do not hard-code a new visual design when a design decision belongs to the user.
- Keep the image-backed scene low-GPU and interactive.
- Preserve the distinction between visible canonical artwork and data-driven interaction hotspots.
- Keep current deterministic fixture behavior until a real adapter is implemented and tested; do not claim live Hermes data prematurely.
- Preserve unrelated worktree changes if the branch is later rebased or merged.

## Key files

- `apps/desktop/src/app/lunar-city/index.tsx` — scene, districts, worker/task state, interactions.
- `apps/desktop/src/app/lunar-city/lunar-city.css` — scene composition and hotspot styling.
- `apps/desktop/src/app/lunar-city/index.test.tsx` — component coverage.
- `apps/desktop/e2e/lunar-city.spec.ts` — mock-backed dev-Electron visual/interaction coverage.
- `apps/desktop/public/lunar-city/moon-settlement-approved.jpg` — canonical approved artwork.
- `design-qa.md` — visual QA and source-of-truth record.

## 2026-08-30 reconciliation record

The approved Lunar City baseline was replayed into the linked implementation
worktree before the planned 3D replacement work. The worktree started at
`2d682fc76d6bcbf63d5d539e940a21a4fa886f91`; its merge base with the approved
source tip `55ffea25bab4779cd3998744cae9975a054d2ec1` was
`a35100ac50b7f0097dc26947e07b055c2137f5fb`.

Replayed source commits, in order:

1. `fd01048446`
2. `9ba59f07de`
3. `6cc1cd4276`
4. `cb77643208`
5. `d2f915c76c`
6. `651ba8bba8`
7. `8dcdaae576`
8. `aee1ad9ca8`
9. `55ffea25ba`

The replay completed without conflicts. Its resulting baseline HEAD is
`6e44f77fc1b531466aa76f04221e0e27fa912e6c`. The current Starmap shell retains
its existing routing, panel ownership, and memory-graph overlay behavior; the
replayed change adds only the Lunar City import, initial render branch, return
callback, approved public artwork, associated tests, and handoff/QA records.
No sessions, gateway, overlay implementation, or desktop boot behavior was
manually changed during reconciliation.

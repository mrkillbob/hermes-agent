# Design QA

> **2026-08-31/09-01 lighting + hero-building passes:** A lighting/material
> addendum (key/rim/fill lights, glow layer, tuned PBR recipes) landed on
> top of the 3D world below, followed by solid walls/windows for the
> Library and Research Lab so they read as enclosed volumes instead of
> thin pillar/beam armatures — the open-front interior view stayed intact.
> Neither closes the full gap to the approved reference art: most other
> buildings still have the thin-armature silhouette issue, and a
> silhouette-uniqueness test constrains how much wall mass any one building
> can gain (see the reports below for what that blocked on Research Lab).
> Full gap analysis, verification receipts, and prioritized follow-up plans
> are in `.superpowers/sdd/2026-08-31-lunar-city-lighting-pass/report.md`
> and `.superpowers/sdd/2026-09-01-lunar-city-hero-building-walls/report.md`,
> each with a real-WebGL screenshot (`webgl-preview.png`) for visual
> comparison against `moon-settlement-approved.jpg`.

> **Current status (2026-08-31):** The historical image-backed QA below is superseded. The runtime is now a genuine Babylon 3D scene using `apps/desktop/public/lunar-city/v2/`; `moon-settlement-approved.jpg` is a reference asset only. The final 3D implementation source tip is `b40e67825121cd0edb99b64e3a1bedcfbd19ec96`; this documentation update is committed at `3c16a086d745ca44d7a57af75dcfe15f301123d8`. Source/unit implementation is review-clean, but no clean packaged visual, GPU/CPU, 30-minute, or supervised-live acceptance has been performed.

## Current 3D QA disposition

- Source contracts cover arranged camera orbit/pan/tilt/zoom/focus/follow, 3D picking, exact connection/profile collision selection, persistent leader dialogue, live source health, real teardown, six leader families, 19 group kits, shared low-power resources, LOD/aggregation, and multi-connection sessions/subagents/Kanban.
- Automated source/unit receipts are current; they are not a substitute for packaged visual approval. The strongest interaction E2E is source-level/GPU-enabled test coverage, while this checkout cannot currently launch a fresh Electron runtime because its packaged Electron installation is incomplete.
- Required next visual run: build a clean exact-SHA `Hermes.app`, capture overview/turntable/focus/interior/occlusion/leader-dialogue states, compare against the approved reference direction, and retain screenshots as packaged evidence. Do not mark this file “passed” until that run completes.

final result: passed

## Scope

- Source visual: `/Users/mikedemott/.codex/worktrees/e16d/hermes-agent/apps/desktop/public/lunar-city/moon-settlement-approved.jpg`
- Implementation route: desktop `starmap` route with the Lunar City overview and building detail interaction
- Intended comparison: the approved warm red lunar settlement, open-front rooms, animal leaders, and small robot children; the implementation keeps functional controls and detail panels as a separate interaction layer over that artwork

## Capture status

The mock-backed dev-Electron Playwright test passed and captured both the city overview and building-detail state. This is not packaged-binary evidence.

- Overview: `/Users/mikedemott/.codex/worktrees/e16d/hermes-agent/apps/desktop/test-results/lunar-city-renders-the-lun-a3de9-pens-a-building-detail-view/lunar-city-overview.png`
- Screenshot: `/Users/mikedemott/.codex/worktrees/e16d/hermes-agent/apps/desktop/test-results/lunar-city-renders-the-lun-a3de9-pens-a-building-detail-view/lunar-city-building.png`
- State: the approved settlement artwork remains visible with its original leaders and robot children; Research Lab selected and building detail open with Fox Scientist room activity
- Interaction: zoom/reset, building selection, and `Enter building` all passed in dev Electron
- Motion: normal runtime uses the live tick/route animations; the test capture uses reduced motion for deterministic screenshots

## Supporting evidence

- Focused UI tests: passed, 6 tests
- Targeted ESLint: passed
- Desktop TypeScript checks: passed
- Desktop build: passed after the clean commit; the final exact SHA and `dirty: false` state are recorded in `apps/desktop/build/install-stamp.json`. This build receipt is not packaged-binary evidence.
- Source visual and mock/dev-Electron implementation screenshot were inspected together

## Follow-up

Keep the live data adapter as the next integration layer. The current scene uses deterministic fixture data and keeps the SOUL-derived role seeds in the interaction model, while the approved artwork remains the canonical character design source.

# Lunar City Visualizer Handoff

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
- Packaged Electron Playwright test:
  `npx playwright test e2e/lunar-city.spec.ts`
- Final Electron result: 1 test passed.
- `git diff --check` passed.
- `apps/desktop/build/install-stamp.json` reports:

```json
{
  "commit": "aee1ad9ca8902d8e05948508680c6fa27b8c2909",
  "dirty": false,
  "source": "local"
}
```

Final visual captures from the packaged Electron run:

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
8. Re-run the packaged Electron test after visual changes because Electron has previously crashed or behaved differently from browser-only runs.

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
- `apps/desktop/e2e/lunar-city.spec.ts` — packaged Electron visual/interaction coverage.
- `apps/desktop/public/lunar-city/moon-settlement-approved.jpg` — canonical approved artwork.
- `design-qa.md` — visual QA and source-of-truth record.

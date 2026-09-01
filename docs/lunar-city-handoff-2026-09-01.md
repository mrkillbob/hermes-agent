# Lunar City visual-fidelity handoff — 2026-09-01

Step-by-step continuation guide for closing the gap between the Lunar City
3D world and the approved reference art (the isometric moon-settlement
paintings and the user's inspiration video). Read this before making any
further geometry/material/lighting change to `apps/desktop/scripts/lunar-city/`
or `apps/desktop/src/app/lunar-city/world/`.

## Where things stand

Continuation update: Depot and Council are now solidified as enclosed,
open-front volumes. Both passed the per-building silhouette-uniqueness guard,
asset budget validation, and the full generated asset-contract suite. Their
changes are committed on the draft branch in `617515c4cf` (Depot) and
`40ea78076d` (Council).

Two passes have landed on `claude/lunar-city-asset-polish-lx8htv` (forked
from draft PR #8's tip, `f597fe10312a`):

1. **Lighting/material pass** — `.superpowers/sdd/2026-08-31-lunar-city-lighting-pass/report.md`.
   Added a key/rim/fill light rig and a glow layer in
   `src/app/lunar-city/world/world-scene.ts`, and per-material PBR recipes
   in `scripts/lunar-city/modeling/palette.mjs`. No geometry changed.
2. **Hero-building walls** — `.superpowers/sdd/2026-09-01-lunar-city-hero-building-walls/report.md`.
   Gave the Library and Research Lab real exterior walls, windows, and (for
   the Library) a roof cap, so they read as enclosed volumes instead of a
   scatter of freestanding pillars/wings — while keeping the approved
   open-front interior view intact.

Both reports have a `webgl-preview.png` (real WebGL-lit screenshot) for
visual comparison. **Read the second report in full before continuing** —
it documents a real trap (below) that cost a full iteration to find.

The remaining **seven** specialist buildings (Review Office, Triage, Garden,
Arts Studio, Engineering Workshop, Release Gatehouse, and Archive) have not
had this treatment yet and almost certainly have the same thin-armature
silhouette problem. Continue one at a time, starting with Review Office.

## The one rule that matters: check the silhouette-uniqueness test after every building, not at the end

`apps/desktop/scripts/lunar-city/build-models.test.mjs` has a test named
_"gives every specialist a unique dominant city-view silhouette and
readable warm identity."_ It projects each building's near-LOD geometry
from 3 angles onto a normalized 30×20 occupancy grid and fails if any two
buildings' silhouettes converge (Jaccard similarity ≥ 0.82 on any one
angle, or ≥ 0.73 averaged across all three).

Adding solid wall mass is exactly the kind of change that trips this: two
buildings that used to look different because of _how_ they were
assembled (thin ribs vs. asymmetric wings vs. a tower cluster) can
converge once they both just read as "a box" with an opening. On the
hero-building pass, giving Research Lab a wall envelope that matched the
Library's pushed their average similarity to 0.757 — over threshold — and
it took an isolation pass (removing pieces one at a time) to find that a
single ~34-sq-unit back-wall panel was the actual driver.

**Do this instead:** after modifying one building, immediately run just
this test (it's ~150ms) before moving to the next building:

```bash
cd apps/desktop
node scripts/lunar-city/build-models.mjs   # regenerate GLBs from your edit
node --test scripts/lunar-city/build-models.test.mjs 2>&1 | grep -A15 "unique dominant"
```

If it fails, the error message names the two colliding building IDs and
their per-angle similarity scores. Don't move to the next building with a
known collision — fix it first (usually: reduce flat wall area on one of
the two, or introduce a genuinely different closure strategy — angled
panels, a tower cluster, asymmetric massing — rather than a flat vertical
backing wall like the ones already used for Library/Depot-style shapes).

## Per-building budget headroom (checked 2026-09-01, current manifest)

Every `box(...)` call in `scripts/lunar-city/modeling/buildings.mjs` must
pass its material string to `paletteMaterial()`, which is one of the 8
`APPROVED_PALETTE` keys in `palette.mjs`. **Reuse only the materials a
building already uses** — introducing a new material for a building pushes
its material count, and several buildings have zero headroom there:

| Building             | triangles/budget | materials/budget | drawCalls/budget | note                         |
| -------------------- | ---------------- | ---------------- | ---------------- | ---------------------------- |
| library              | 4034/28000       | 4/4 (full)       | 6/8              | done this pass               |
| research-lab         | 2378/32000       | 5/5 (full)       | 7/9              | done this pass (light touch) |
| depot                | 1276/24000       | 4/4 (full)       | 6/7              |                              |
| review-office        | 4488/26000       | 4/4 (full)       | 7/8              |                              |
| triage               | 704/12000        | 3/3 (full)       | 5/5 (full)       | **zero draw-call headroom**  |
| garden               | 2244/18000       | 4/4 (full)       | 6/6 (full)       | **zero draw-call headroom**  |
| council              | 5730/22000       | 4/4 (full)       | 6/7              |                              |
| arts-studio          | 1568/26000       | 6/6 (full)       | 8/8 (full)       | **zero draw-call headroom**  |
| engineering-workshop | 898/28000        | 4/5              | 6/8              | 1 material free              |
| release-gatehouse    | 994/24000        | 5/5 (full)       | 7/8              |                              |
| archive              | 930/26000        | 4/5              | 6/8              | 1 material free              |

Triangle and GPU-MiB budgets have enormous headroom everywhere (every
building is under 25% of its triangle budget) — that axis is not a
constraint. Materials and draw calls are.

Draw calls come from `mergeLodMeshes()` in `scripts/lunar-city/modeling/primitives.mjs`:
all meshes under a given `:lod:near`/`:mid`/`:far` root that share a
material and aren't marked `keepSeparate` get merged into **one** draw
call. So adding more geometry in an _already-used_ material to the `near`
LOD tier costs zero extra draw calls — it folds into the existing merged
mesh for that material. A new material, or anything you mark
`keepIdentity()`/`keepSeparate`, costs a new draw call. For triage/garden/
arts-studio (already at their draw-call cap), that means: reuse existing
materials only, and don't mark new geometry `keepSeparate`, or you will
need to raise that building's `maxDrawCalls` in
`public/lunar-city/v2/world-manifest.v2.json` deliberately (that's a
tracked budget-contract change — `validate-assets.mjs` will tell you if
you forgot to keep the manifest's declared budget and the model's actual
stats in sync, since `build-models.mjs`'s `updateManifestStatistics`
rewrites them for you on every build).

## Step-by-step: solidifying one more building

Follow this loop per building, one at a time, in this order (Depot and
Council first — they have decent draw-call headroom and are structurally
simpler to reason about than triage/garden/arts-studio, which are already
maxed on draw calls):

1. **Read the building's frame function** in `buildings.mjs` (e.g.
   `addDepotFrame`) plus its `specialistFrame`/generic-detail helpers
   (`addLayeredRoomDetail`, `add<Name>Massing`) and its `build<Name>`
   export (interior props). Identify: what already closes the envelope
   (wings, ribs, towers, pods), and where the real gaps are (usually: no
   continuous side wall, gaps in the back).
2. **Render the current state first** so you have a before/after:
   ```bash
   cd apps/desktop
   LUNAR_CITY_PREVIEW_CHROMIUM=<path-to-chromium-if-needed> \
     node scripts/lunar-city/render-webgl-preview.mjs /tmp/before.png
   ```
   (The default preview only places terrain/library/research-lab/council/
   leaders/workers — edit the `placements` array in
   `render-webgl-preview.mjs`'s `ENTRY_SOURCE`, or write a throwaway
   variant like the one described in the 2026-09-01 report, to frame just
   the building you're working on. Don't commit a throwaway variant.)
3. **Add wall/window geometry**, reusing only that building's existing
   materials (check the table above). Follow the Library's pattern in
   `addLibraryFrame` as the reference implementation: a full-width back
   wall, two side walls stopping short of the open front, a few window
   strips per side using the building's `accent` material, positioned by
   `side * (width/2 + offset)` so nothing exceeds the floor footprint.
4. **Rebuild and check budgets:**
   ```bash
   node scripts/lunar-city/build-models.mjs
   node scripts/lunar-city/validate-assets.mjs public/lunar-city/v2/world-manifest.v2.json
   ```
   A budget violation throws immediately with the building ID and which
   limit it exceeded.
5. **Check the silhouette-uniqueness test** (see above) before doing
   anything else. If it fails, isolate which added piece is the driver by
   commenting pieces out one at a time and rebuilding — don't guess.
6. **Run the rest of the asset-contract suite:**
   ```bash
   node --test scripts/lunar-city/build-models.test.mjs scripts/lunar-city/validate-assets.test.mjs
   ```
7. **Visual check:** re-render the preview (step 2's command) and look at
   it — front (open) and at least one side/back angle. Confirm the open-front
   interior read is still intact (this is a hard requirement from
   `docs/lunar-city-design-handoff.md` — do not wall off the front).
8. **Full verification before committing:**
   ```bash
   npx vitest run --project ui src/app/lunar-city   # should stay 539/539 (asset-only changes don't touch this)
   npm run typecheck
   npx eslint --max-warnings=0 scripts/lunar-city/modeling/buildings.mjs
   npx prettier --check scripts/lunar-city/modeling/buildings.mjs
   ```
9. **Commit** with the same evidence shape as the two prior passes: what
   changed, why, the silhouette-test result, verification commands run.
   Add a dated `.superpowers/sdd/<date>-lunar-city-<slug>/report.md` if the
   change is non-trivial (more than one building, or another
   silhouette-collision investigation) — a one-line commit body is fine for
   a single small building if nothing unexpected happened.

## Other open items (lower priority than the geometry pass)

- **Warm window glow.** `APPROVED_PALETTE` (`palette.mjs`) has no warm
  emissive color — only cyan `signal-emissive` and purple
  `archive-emissive`. The reference art's windows are warm/amber. Get the
  user's explicit sign-off before adding a 9th palette color; this is a
  design decision, not a lighting tune. If approved, remember every
  building that starts using it needs material-budget headroom (see
  table) or a manifest budget bump.
- **Roofline variety.** Several buildings still share a similar
  stepped-gable/flat-cap roof silhouette from directly above. Differentiate
  2-3 more once the wall pass is done — the silhouette-uniqueness test will
  catch roofs converging the same way it caught walls.
- **Leader/character scale.** In every preview screenshot so far the six
  leaders read as small props next to the buildings. Check this against
  the camera-focus framing in `manifest.ts` before touching leader mesh
  scale directly — the focus camera may already compensate for raw size,
  in which case the fix belongs in framing, not in `characters.mjs`.

## Environment notes for a fresh session

- `npm install --workspace=apps/desktop --include-workspace-root` from the
  repo root if `node_modules` isn't present.
- `render-webgl-preview.mjs` needs a Chromium build matching
  `apps/desktop/package.json`'s pinned `@playwright/test` version. If
  `npx playwright install` isn't available/needed in your environment, the
  script accepts `LUNAR_CITY_PREVIEW_CHROMIUM=<path>` to point at a
  pre-provisioned build instead.
- Don't touch `apps/desktop/public/lunar-city/moon-settlement-approved.jpg`
  or `source-reference.v2.json`'s recorded SHA — those anchor the approved
  reference image's provenance and are checked by `validate-assets.mjs`.

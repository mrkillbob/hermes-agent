# Lunar City hero-building walls — report

Follow-up to `.superpowers/sdd/2026-08-31-lunar-city-lighting-pass/report.md`,
item 1 of its plan: solidify the building silhouettes, starting with the
Library and Research Lab.

## What changed

`scripts/lunar-city/modeling/buildings.mjs`:

- **Library** (`addLibraryFrame`): added a full-width backing wall behind
  the existing tiered `archive-back` panels (those alone left gaps you
  could see straight through), two solid `charcoal-structure` side walls
  spanning most of the depth, three `archive-emissive` window strips per
  side wall, and a `lunar-rust` roof cap bridging the new wall tops up to
  where the existing spire/stepped-gable roof detail already starts.
- **Research Lab** (`addLabFrame`): added window glow (`signal-emissive`)
  on the existing east `side-return` and the west `service-stack`, using
  the lab's existing asymmetric massing rather than a new wall.

Both reuse only materials already counted against that model's
`maxMaterials` budget (library was already at 4/4, research-lab at 5/5),
so neither needed a manifest budget change.

## The uniqueness test caught a real mistake — twice

`build-models.test.mjs`'s "gives every specialist a unique dominant
city-view silhouette" test failed after the first version of this change:
it projects each model's `:lod:near` geometry from 3 angles onto a coarse
30x20 grid, normalized to that model's own bounding box, and fails if any
two buildings' occupancy sets are too similar (Jaccard ≥ 0.82 on any single
angle, or ≥ 0.73 averaged across all three).

Library and research-lab have similar aspect ratios already (width/height
≈ 1.53 vs 1.71). Once both got dominant flat wall planes, that similarity
stopped being masked by each building's previously-distinct detail (thin
ribs/wings/towers for library, an asymmetric wing/pod/stack armature for
research lab) and the two silhouettes converged to "a box" — 0.799 max /
0.757 average, both over threshold.

First fix attempt (dropping research lab's added roof cap) barely moved
the number (0.798/0.753) — the roof wasn't the driver. Isolating it
confirmed the driver was a **research-lab back-wall backing panel** I'd
added (~34 sq. units of flat area, mirroring the library's own back wall):
removing it and keeping only the window-glow additions (small, low-area,
negligible effect on a 30x20 grid) brought it back under threshold and the
test passed clean. This is the reason research lab did **not** get a
matching full envelope in this pass — see "what's still open" below.

## Verification

- `node scripts/lunar-city/build-models.mjs` — rebuilt; library and
  research-lab were the only models whose sha256 changed.
- `node scripts/lunar-city/validate-assets.mjs public/lunar-city/v2/world-manifest.v2.json`
  — passed.
- `node --test scripts/lunar-city/build-models.test.mjs scripts/lunar-city/validate-assets.test.mjs`
  — 41/41 passed, including the silhouette-uniqueness test above.
- `npx vitest run --project ui src/app/lunar-city` — 539/539 passed
  (unaffected — this pass touched only the asset-generation scripts).
- `npm run typecheck` — passed.
- `npx eslint --max-warnings=0` / `npx prettier --check` on
  `buildings.mjs` — passed.
- Visual: rendered both buildings individually from all four cardinal
  angles via `render-webgl-preview.mjs`'s lighting rig (ad hoc single-model
  variant, not committed) to confirm (a) the envelope now reads as solid
  from the back/sides and (b) the approved open-front interior view — the
  book/orb/desk in the library, the telescope/workbenches/display wall in
  the lab — is still fully visible from the front, framed by the new walls
  rather than obscured by them. `webgl-preview.png` in this folder is the
  overview shot from the shared preview tool for a diff against the prior
  pass's screenshot.

## What's still open

- **Research Lab did not get the same wall treatment as the Library** —
  the uniqueness test forced a lighter touch (windows only, no new wall
  mass). Its enclosure still relies entirely on the pre-existing
  west-wing/east-wing/side-return/service-stack armature, which has visible
  gaps from directly behind (see the report's back-angle note). Closing
  those without re-triggering the uniqueness test needs a wall treatment
  that's structurally *different* from the library's — e.g. angled/sloped
  panels following the existing wing rotations, rather than flat vertical
  backing panels — not just windows.
- The other five specialist buildings (Depot, Review Office, Triage,
  Garden, Council, Arts Studio, Engineering Workshop, Release Gatehouse,
  Archive) have not had this pass at all and most likely have the same
  thin-armature silhouette issue the original gap analysis called out.
  Doing them now would need the same uniqueness-test discipline: check the
  silhouette test after each building, not just at the end, since it's
  cheap (~150ms) and catches convergence immediately rather than after
  several buildings' worth of changes compound it.
- Items 2-4 from the prior report (warm window glow needs a palette
  sign-off since both hero buildings' windows landed on their own existing
  cool accent color instead; roofline variety; leader/character scale)
  are still open.

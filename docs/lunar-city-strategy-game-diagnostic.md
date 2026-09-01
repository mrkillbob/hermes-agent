# Lunar City strategy-game diagnostic

**Date:** 2026-09-01  
**Purpose:** Use the readable, high-level design language of classic
StarCraft/Warcraft-style strategy games as a diagnostic aid for Lunar City.

This is a principles audit, not an asset extraction plan. We do not copy,
download, reverse-engineer, or import proprietary game models, textures,
wireframes, or animations. The references are useful because they make a few
design problems easy to see: a building must read from the overview, a unit's
role must be legible before selection, routes and ownership must be obvious,
and detail must collapse gracefully as the camera pulls back.

## Safe external benchmarks

These are candidates for a separate, quarantined reference folder—not assets
to drop into the shipped Lunar City bundle:

- **Kenney Hexagon Kit / City Kit:** CC0 and useful for studying modular
  footprints, road widths, camera scale, and overview composition. Kenney
  confirms its game assets are public-domain CC0, including commercial use.
- **Quaternius:** CC0 model packs are useful for testing import, LOD, and
  animation pipelines. Their license permits modification and combination,
  but prohibits redistributing the models as a standalone asset pack; keep
  any experiment clearly separated from our generated assets.
- **Poly Haven:** CC0 materials and environmental models are useful for
  lighting/material stress tests, not for defining Lunar City's approved
  character or building language.
- **OpenGameArt:** a discovery index only. Licenses vary per asset and may
  include attribution, share-alike, or GPL obligations, so no asset enters
  the repository without a per-file license record and compatibility review.

The diagnostic workflow is: record source URL and license, import a copy into
an isolated benchmark scene, measure screen-space silhouette/triangle/draw
cost, compare against the Lunar City turntable, then delete the benchmark
copy. Only newly authored or explicitly compatible assets can ship.

## Our current asset inventory

The v2 manifest currently contains 15 model families:

| Family | Near triangles | Near draws | Materials | LODs | Diagnostic role |
| --- | ---: | ---: | ---: | --- | --- |
| Terrain | 2,148 | 6 | 4 | near/far | shared city floor |
| Library | 4,034 | 6 | 4 | near/far | knowledge landmark |
| Research Lab | 2,378 | 7 | 5 | near/far | science landmark |
| Depot | 1,348 | 6 | 4 | near/far | operations landmark |
| Review Office | 4,524 | 7 | 4 | near/far | review landmark |
| Triage | 704 | 5 | 3 | near/far | incident routing |
| Garden | 2,244 | 6 | 4 | near/far | recovery/social space |
| Council | 5,790 | 6 | 4 | near/far | leadership landmark |
| Arts Studio | 1,568 | 8 | 6 | near/far | creative work |
| Engineering Workshop | 898 | 6 | 4 | near/far | implementation work |
| Release Gatehouse | 994 | 7 | 5 | near/far | release boundary |
| Archive | 930 | 6 | 4 | near/far | historical record |
| Bus | 200 | 4 | 3 | near/far | route/readability prop |
| Leaders | 12,386 | 11 | 4 | near/mid/far | six unique leader identities |
| Workers | 3,092 | 6 | 3 | near/mid/far | shared kits + identity accents |

There are also 19 declared worker groups in the manifest. Identity is
canonicalized by connection/profile/session/subagent data and rendered through
deterministic worker signatures, so the same visual kit is not silently used
to represent two different identities.

## Wireframe and silhouette audit

The repository's wireframe diagnostic is `render-turntable.mjs`: it renders
the actual GLB near LODs as deterministic flat-shaded turntables and the test
suite projects each specialist from three city-view angles onto occupancy
grids. `build-models.test.mjs` currently enforces both a genuinely open room
front and distinct dominant silhouettes. This is the right analogue to a
strategy game's “read it before zooming” test.

The recent wall passes fixed the largest readability gap for five of eleven
specialists: Library, Research Lab, Depot, Council, and Review Office now read
as enclosed, open-front volumes. The remaining Triage, Garden, Arts Studio,
Engineering Workshop, Release Gatehouse, and Archive still need enclosure or
distinctive massing. Their triangle budgets are generous; materials and draw
calls are the actual low-power constraints. Geometry that reuses an existing
material merges into the current draw call, while a new material or
`keepSeparate` mesh does not.

## Reference principles translated into our design

| Diagnostic principle | What to look for in our world | Implementable Lunar City rule |
| --- | --- | --- |
| Silhouette hierarchy | Landmark roofs/towers remain unique at overview distance | Finish the six remaining enclosures one at a time; run the uniqueness test after every build |
| Strong landmarks | A player can locate leadership, research, and release areas without labels | Preserve distinct massing for Council, Research Lab, and Release Gatehouse; avoid box-like closures |
| Modular readability | Buildings feel assembled from a coherent kit, not unrelated props | Reuse approved palette materials and frame primitives; vary rooflines with a small number of deliberate modules |
| Ownership/status at a glance | Worker, group, and destination state are distinguishable before opening the inspector | Keep deterministic identity accents; consider a quality-gated floating badge only after measuring its frame cost |
| Hero focus | Selecting a leader changes the visual emphasis, not only the camera | Add a warm-palette leader rim/emissive treatment driven by `focusedEntityKey` |
| Living overview | The city feels active while the player observes | Add slow idle orbit/breathing only when unfocused, disabled by reduced-motion and efficient-tier budget rules |
| Route legibility | Roads, bus, and destination buildings explain where work is moving | Keep routes sparse and high-contrast; never add decorative geometry that competes with worker paths |
| Graceful degradation | Pulling back preserves composition without animating every object | Retain near/mid/far character LODs, stop idle animation work at distance, and keep badges optional |

## Recommended implementation order

1. Complete the six remaining specialist enclosures while preserving each
   open-front interior and the current draw/material ceilings.
2. Add the focused-leader warm rim/glow state; verify it clears when focus
   changes and does not affect other leaders.
3. Add a reduced-motion-aware idle camera drift that yields to input and
   focus/follow intents.
4. Measure floating status badges in the `efficient` quality tier before
   building them. The existing docked inspector already provides the full
   status surface, so badges are optional rather than a correctness gap.
5. Re-run the flat turntable, real-WebGL preview, focused Lunar City tests,
   and the packaged performance/stability acceptance receipts.

## Verification commands

```bash
cd apps/desktop
node scripts/lunar-city/build-models.mjs
node scripts/lunar-city/validate-assets.mjs public/lunar-city/v2/world-manifest.v2.json
node --test scripts/lunar-city/build-models.test.mjs scripts/lunar-city/validate-assets.test.mjs
node scripts/lunar-city/render-turntable.mjs /tmp/lunar-city-turntable.svg
npx vitest run --project ui src/app/lunar-city
```

The diagnostic is successful when the city remains recognizable in the
turntable and overview preview, each specialist has a distinct role-bearing
silhouette, and the runtime can degrade detail without losing identity,
routes, or leadership access.

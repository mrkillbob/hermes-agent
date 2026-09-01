# Lunar City handoff — to a local (non-cloud) session, 2026-09-01

Written by a cloud-sandboxed Claude Code session as the user switches to a
local session that has direct access to their desktop — specifically an
already-open **Blender 5.2.1 GUI with `lunar-city.blend` loaded**, which
the cloud session cannot reach. Read this before touching anything.

## Why this handoff exists

The user's own words, verbatim, on why they're switching: they want live
Blender console access to the scene that's already open locally, not the
headless Blender a cloud sandbox can run. Everything below is what the
cloud session did, found, and was mid-investigation on when the switch
happened.

## Repo state

Branch `claude/lunar-city-asset-polish-lx8htv`, currently mirrored 1:1 onto
draft PR #8's branch (`codex/lunar-city-approved-world-draft-20260831`) —
both point at the same commit. **Before doing anything, `git fetch` both
and confirm they still match** (`git log --oneline -1` on each) — other
sessions (Hermes, Codex) have been pushing directly to the PR branch
throughout this work, sometimes without this session's involvement. If
they've diverged, reconcile before starting new work; don't assume this
document's "current" state is still current.

Latest commit at time of writing: `1a7145e0b` — "bake ambient occlusion,
add shadows, tonemapping and haze."

## What's landed, in order (read the commits for full detail)

1. **Lighting/material pass** (`.superpowers/sdd/2026-08-31-lunar-city-lighting-pass/report.md`) — key/rim/fill lights, glow layer, per-material PBR tuning.
2. **Hero-building walls** (`.superpowers/sdd/2026-09-01-lunar-city-hero-building-walls/report.md`) — Library/Research Lab got real walls; documents the silhouette-uniqueness test trap.
3. **Docs handoffs** — `docs/lunar-city-handoff-2026-09-01.md` (per-building geometry loop + budget table) and `docs/lunar-city-video-reference-roadmap.md` (the full phased plan, including the resolution of what the user's inspiration video actually means — **read that file's "Read this first" section before assuming the video is a literal palette target; it isn't**).
4. **Independent verification pass** — confirmed Codex's parallel work (all 11 buildings solidified, idle camera drift, focused-leader treatment) was real, not just claimed. Found the exact-population (25/100/250) packaged-acceptance blocker is separate infra work, documented precisely in the roadmap's Phase 5.
5. **`1a7145e0b` — baked AO / shadows / tonemapping / fog.** This is the most recent and most technically important commit. Full diagnosis and fix below because it's load-bearing for everything else.

## The core diagnosis (measured, not guessed)

Before this pass, across the **entire world**:

```
textures:        0
UV channels:      0
vertex colors:     0
ShadowGenerator:    none  (dynamicShadows: 'near' was a no-op flag)
tonemapping:         none
fog/haze:              none
triangle budget used:    ~4%
```

Every surface was one flat solid color under one nearly-vertical light.
**That is the entire reason it read as "programmer art"** — not polygon
count, not detail, finish. `1a7145e0b` fixed this:

- **Baked per-vertex AO** (new `scripts/lunar-city/modeling/ambient-occlusion.mjs`) — BVH raycaster, deterministic Hammersley hemisphere sampling (the build is hash-checked, so no `Math.random`), baked into glTF `COLOR_0` which multiplies into base color for free at runtime.
- **Adaptive subdivision**, because vertex AO on a 4-corner box face has nothing to interpolate across without it. **Scoped to terrain only** — tessellating a district breaks `build-models.test.mjs`'s silhouette-uniqueness guard (denser meshes rasterize toward a filled box in that test's coarse occupancy grid, so buildings converge; measured library/depot at 0.958 similarity against a 0.82 threshold). This is the single most important constraint for whatever comes next — **any geometry change that adds triangle density to a district must be checked against that test immediately**, not at the end of a modeling session.
- **A real `ShadowGenerator`** on the key light, making the existing `dynamicShadows` quality-tier flag actually do something for the first time.
- **Fixed the key light direction** — it pointed nearly straight down, so shadows fell directly under their own casters and were invisible. Now rakes at ~40°.
- **ACES tonemapping + linear fog**, because emissive signage was clipping to flat white and there was no depth cue at all.
- **Raised `charcoal-structure` from `#242431` to `#5E5872`** (same hue, higher value). This was RGB(36,36,49) — effectively black — and is the primary structural material for **24% of all geometry**, including every wall. Nothing can shade against black; this was the single biggest lever in the whole pass, confirmed by an A/B render. It's a change to an approved palette value, flagged as such in `palette.mjs` at the callsite, one line to revert. **The user has already approved this — it's not pending sign-off anymore, do not re-litigate it.**

Full before/after comparison image was sent to the user directly (not
committed to the repo).

## What's still wrong, per the user's own words after seeing the above

> "it still looks very low poly very low effort. basic shapes looks more
> like runescape then the reference images... it has no 'city planning'
> we have a ton of groups (buildings) that need to be represented right
> now it just looks like it was thrown up into a layout."

Both complaints are accurate and the cloud session agrees with the
diagnosis. Two separate problems:

### Problem 1: the geometry itself is primitive-box modeling, not real modeling

Every building in `scripts/lunar-city/modeling/buildings.mjs` is built
from `box()`/`cylinder()`/`cone()` calls — stacked primitives, no bevels,
no booleans (window/door openings are surface-mounted emissive strips,
not actual cut openings), no subdivision-smoothed forms. This is a
fundamentally different technique from how the approved reference art and
the user's inspiration examples (StarCraft, Baldur's Gate 2) actually
read — those use beveled edges (light catches an edge instead of a hard
90°), real cut geometry for windows/doors, and varied silhouette curvature
that primitive stacking cannot produce.

**This is exactly what the user wants solved with real Blender modeling**
— bevel, boolean, subdivision-surface, array modifiers, the actual tools
a 3D artist uses, not more box-stacking in a JS DSL.

### Problem 2: the layout has no generative plan — confirmed by reading the source

`scripts/lunar-city/modeling/terrain.mjs` has a `DISTRICTS` constant:

```js
const DISTRICTS = Object.freeze([
  [-28, 0.8, -18], // library
  [25, 1.1, -22], // research-lab
  [-31, 0.45, 12], // depot
  [33, 0.7, 10], // review-office
  [4, 0.4, 25], // triage
  [-8, 0.25, 34], // garden
  [27, 0.35, 31], // council
  [0, 0.55, -1], // bus
  [-29, 0.7, -1], // arts-studio
  [-29, 0.6, 30], // engineering-workshop
  [0, 0.5, 12], // release-gatehouse
  [38, 0.5, 18] // archive
])
```

These are **12 hand-picked coordinates with no underlying logic** — no
radial system, no zoning by function, no consistent facing direction.
Per-model rotations in `world-manifest.v2.json` are separately
hand-authored jitter (16°, -11°, 10°, -15°, 0°, 8°, -10°, 11°, -9°, 5°,
-13° — no pattern). The walkway network in the same file
(`terrain.mjs:164-178`) is a **hand-picked list of edge pairs** between
specific district indices, not derived from proximity or an actual path
algorithm — e.g. `walkway(..., 'library-research', DISTRICTS[0],
DISTRICTS[1], 4.8)` is just someone deciding those two should connect.

**This is the literal, verified root cause of "thrown into a layout."**
There is no city plan; there's a coordinate list someone typed by hand.

`DISTRICTS` in `terrain.mjs` and the per-model `transform.position` in
`world-manifest.v2.json` are **two independently hand-kept copies of the
same 12 positions** — they must currently be updated in lock-step by
hand, which is itself an error-prone design smell worth fixing while
redoing the layout (make one the generated source of truth for the
other, or generate both from one shared coordinate module).

### What a real city-planning pass needs to do

Not started — this is where the cloud session was mid-investigation when
the switch happened. Recommended approach, for whoever picks this up
(ideally with the district _identities_ actually driving the plan, not
just coordinates):

- **Library** — knowledge landmark
- **Research Lab** — science landmark
- **Depot** — operations
- **Review Office** — quality gate
- **Triage** — incident routing
- **Garden** — recovery/social space
- **Council** — leadership (currently near-center; the camera's
  `overview.target` is `[0, 5, 4]`, and `release-gatehouse`/`bus` already
  sit near the origin — there's a latent "this is the front door/plaza"
  intuition already in the data worth preserving, not discarding)
- **Arts Studio** — creative work
- **Engineering Workshop** — implementation
- **Release Gatehouse** — release boundary
- **Archive** — historical record

A real plan groups these by function into legible zones (e.g. a
build→release pipeline reading as adjacent: Engineering Workshop → Depot
→ Release Gatehouse; a leadership/knowledge core near the plaza: Council,
Library; a care/recovery cluster: Triage, Garden), gives every building a
**consistent orientation rule** (open front toward the plaza or its
primary connecting walkway, not arbitrary jitter), and derives the
walkway network **algorithmically** from the resulting positions
(minimum-spanning-tree + a couple of redundant cross-links reads as an
actual road network; a hand-picked edge list does not).

Respect existing hard constraints while redesigning:

- `camera.overview` in the manifest (`target: [0,5,4]`, `radius: 78`,
  `minRadius/maxRadius: 18/120`, `minBeta/maxBeta: 0.72/1.3`) — the
  composition needs to read well from this exact framing, not just from
  directly overhead.
- `camera.bounds` (`min: [-60,-12,-60]`, `max: [60,36,60]`) — the terrain
  and buildings already live inside this; a redesign should stay inside
  it too, or the bounds need a deliberate, documented change.
- The terrain's existing crater/cliff composition
  (`terrain.mjs:56-98`) — it's reasonably good and probably doesn't need
  a full redo, just buildings/walkways composed onto it more
  intentionally.
- `build-models.test.mjs`'s silhouette-uniqueness guard and the
  per-building material/draw-call budgets in
  `docs/lunar-city-handoff-2026-09-01.md`'s table — moving buildings is
  free with respect to these, but any accompanying geometry change is not.
- Worker/leader navigation and destination data that references these
  district positions — check `manifest.destinations` and
  `world/navigation.ts`/`navigation.test.ts` before moving anything, so
  live pathing doesn't silently break.

## The Blender tooling situation

Two separate things exist and should not be confused:

1. **The user's local Blender 5.2.1 GUI, with `lunar-city.blend` already
   open.** The cloud session cannot reach this. Whatever is in that file
   — its current state, how it got there — needs to be established by
   the local session; the cloud session has no visibility into it.
2. **A headless CLI harness the cloud session set up and verified working**
   in its own sandbox: [HKUDS/CLI-Anything](https://github.com/HKUDS/CLI-Anything)'s
   prebuilt `blender/agent-harness` package (`cli-anything-blender`
   console script). Confirmed installable and running:

   ```bash
   apt-get install -y --no-install-recommends blender   # got 4.0.2; use whatever matches locally
   git clone --depth 1 https://github.com/HKUDS/CLI-Anything.git
   python3 -m venv .venv && source .venv/bin/activate
   pip install -e CLI-Anything/blender/agent-harness
   cli-anything-blender --help
   ```

   This gives structured, JSON-output commands (`object`, `material`,
   `modifier` — includes `bevel`, `boolean`, `subdivision_surface`,
   `array`, `mirror`, `solidify`, `decimate`, `smooth` — `light`,
   `camera`, `render`, `scene`, `session` with undo/redo) that generate
   and run real `bpy` scripts via `blender --background --python`. It
   maintains scene state as JSON on the CLI side and regenerates a full
   script per render/export call rather than holding one persistent live
   Blender process — useful for scripted, reviewable modeling work, not
   a live interactive session. It was **not yet used for actual modeling**
   before the switch — only installed and verified to run.

   If the local session prefers driving the already-open GUI's live
   Python console directly instead, that's almost certainly the better
   choice now that live access exists — this harness was a workaround for
   not having that.

Either way, whatever gets modeled has to come back out as **GLB files
matching this repo's existing contract**: the `:root` / `:lod:near` /
`:lod:mid` / `:lod:far` node naming `buildingNodes()` establishes in
`buildings.mjs`, the per-building material/triangle/draw-call/texture
budgets in `world-manifest.v2.json`, and `validate-assets.mjs` /
`build-models.test.mjs` passing on the result. A beautifully modeled
building that doesn't fit that contract won't build.

## Practical next steps for the local session

1. Confirm branch state (see "Repo state" above) before touching anything.
2. Establish what's actually in the open `lunar-city.blend` — is it a
   fresh import of the current GLBs (via `blender_stage.py`, which
   already exists in this repo and imports the shipped GLBs into
   `LUNAR_CITY::BUILDINGS`/`WORKERS`/`TERRAIN` collections), or something
   built from scratch? That determines whether it's a modeling surface on
   top of current geometry or a parallel effort.
3. Decide the city-planning approach (this doc has a starting point, not
   a final plan — the district identities and existing latent
   plaza-at-origin intuition are worth building from, not discarding) and
   implement it as data changes to `terrain.mjs`'s `DISTRICTS` +
   `world-manifest.v2.json`'s per-model transforms + the walkway list,
   ideally de-duplicating the two hand-kept copies of position data while
   at it.
4. Do real modeling (bevel/boolean/subdivision) via whichever Blender
   access path makes sense now, checking the silhouette-uniqueness test
   after every building — not at the end (see the hero-building-walls
   report for exactly what it costs to skip this).
5. Keep committing to `claude/lunar-city-asset-polish-lx8htv` /
   `codex/lunar-city-approved-world-draft-20260831` (they should stay
   mirrored — fast-forward one onto the other after each push, verifying
   the target hasn't moved first) and posting progress to PR #8 so other
   sessions (this cloud one included, if resumed) can pick up from
   accurate state instead of stale assumptions.

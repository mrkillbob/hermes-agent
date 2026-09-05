# Lunar City lighting/material pass — report

## Why

Draft PR #8 (`codex/lunar-city-approved-world-draft-20260831`) landed the
approved districts as authored low-poly GLBs, but the rendered result is
still far from the approved reference art (the isometric moon-settlement
paintings and the inspiration video): everything read as flat, dim, and
untextured. This pass closes part of that gap without touching any
authored geometry, so it carries none of the risk of a mesh rewrite.

## What changed

- `scripts/lunar-city/modeling/palette.mjs`: replaced the one-size PBR
  recipe (`metallic`/`roughness` keyed only off an `.includes('emissive')`
  check) with a per-material recipe table. Structural charcoal is now matte
  (reads as solid architecture), trim metal is glossier (catches rim
  light), and the signage/glow materials (`signal-emissive`,
  `archive-emissive`, `triage-amber`, `garden-green`, `sunset-orange`) carry
  real emissive strength instead of a flat 0.22/0.72 split. No hex values
  in `APPROVED_PALETTE` changed — this is a lighting-response tune, not a
  new design.
- `src/app/lunar-city/world/world-scene.ts` (+ `model.ts` / `create-world.ts`
  for the supporting types and dynamic imports): the scene previously had
  exactly one `DirectionalLight` and no fill, glow, or tone mapping. Added:
  - A warmed key light (`diffuse` pushed toward sunset amber).
  - A cool rim `DirectionalLight` from the back-left, `shadowEnabled=false`
    always — this is the StarCraft-style two-tone edge read, and it never
    touches the existing `keyLight.shadowEnabled` quality-tier toggle.
  - A soft `HemisphericLight` fill (sky-blue top / warm ground bounce,
    zero specular) so unlit faces stop reading as pure black.
  - A `GlowLayer` (`mainTextureRatio: 0.5`) so the emissive materials
    actually bloom instead of just being a bright flat color. Its
    intensity is tied to the existing `settings.decorations` quality flag,
    so the efficient tier's most aggressive degradation step turns it off
    along with everything else it already disables — it does not add a new
    always-on cost tier.
  - `scene.imageProcessingConfiguration` contrast/exposure/vignette, which
    is free (in-shader on the existing PBR pipeline, no extra pass).
  - `HemisphericLight`/`GlowLayer` are optional on `LunarCityWorldModules`
    and every use is guarded, so none of this can break a caller that
    doesn't supply them (matters for the ~30 other lunar-city test files
    that build a minimal fake `modules` object).
- `src/app/lunar-city/world/create-world.test.ts`: added `FakeHemisphericLight`
  / `FakeGlowLayer` and a new test asserting the key/rim/fill rig, the
  rim light's `shadowEnabled=false`, and that `GlowLayer.intensity` follows
  quality tier changes and gets disposed with the world.
- Regenerated every GLB via `node scripts/lunar-city/build-models.mjs`
  (fully deterministic — a second run reproduced identical sha256 for
  every model) and let it rewrite `world-manifest.v2.json`'s per-model
  `bytes`/`sha256`, which is what that script is for.

## New tool: `scripts/lunar-city/render-webgl-preview.mjs`

`render-turntable.mjs` (existing) re-implements its own flat-shaded SVG
projector from the raw GLB triangle data — fast and deterministic, but it
cannot show lighting, glow, or material changes at all, which made it
useless for grading this pass.

The new script renders the actual GLBs through a real WebGL2 context
(headless Chromium via `@playwright/test`, already a devDependency) using
the exact lighting rig above, and screenshots it. It is **not** a
determinism/regression check — GPU/driver output varies — it is a fast
way for a human (or the next agent) to see what changed before touching
Electron packaging. `webgl-preview.png` in this folder is that screenshot
from this pass.

## What the screenshot shows — and what it doesn't fix

The glow/lighting pass makes a real, visible difference: the emissive
accents now bloom and read as lit signage, unlit faces aren't flat black,
and the warm/cool key-rim split gives the silhouettes some depth.

It does **not** close the larger gap to the reference art. Compare
`webgl-preview.png` against `apps/desktop/public/lunar-city/moon-settlement-approved.jpg`
and the user-supplied inspiration video/PNGs: the authored buildings in
`scripts/lunar-city/modeling/buildings.mjs` are built from thin pillar/beam
clusters with a few flat panels, not solid walled volumes with roofs — the
turntable render in `.superpowers/sdd/2026-08-30-lunar-city-playable-world/`
shows this most clearly from directly above. No amount of lighting fixes a
silhouette that reads as a wireframe scaffold instead of a building. That
is a geometry-authoring problem in `buildings.mjs`/`characters.mjs`, not a
lighting one, and it is the next real chunk of work — see the follow-up
plan below.

## Verification

- `node scripts/lunar-city/build-models.mjs` — rebuilt all 15 models +
  navigation mesh + palette texture; budgets held (no triangle/GPU/draw
  call budget was touched).
- `node scripts/lunar-city/validate-assets.mjs public/lunar-city/v2/world-manifest.v2.json`
  — passed.
- `node --test scripts/lunar-city/build-models.test.mjs scripts/lunar-city/validate-assets.test.mjs`
  — 16 + 25 = 41 passed (matches the PR's stated "Asset build/contract
  tests: 40 passed" baseline, +1 new).
- `npx vitest run --project ui src/app/lunar-city` — 538/538 passed
  (matches the PR's stated full Lunar City UI suite baseline exactly).
- `npm run typecheck` (renderer + Electron + e2e projects) — passed.
- `npx eslint --max-warnings=0` on every changed file — passed.
- `npx prettier --check` on every changed file — passed.
- `npm run build` (Vite renderer + Electron main/preload bundle) — passed.
- Did **not** run the packaged Electron/e2e visual suite (Electron's own
  binary isn't provisioned in this environment) — that remains the same
  "known follow-up" the PR already called out for packaged acceptance.

## Follow-up plan (next pass, in priority order)

1. **Solidify building silhouettes.** In `buildings.mjs`, each district's
   `shell` currently reads as a scatter of thin boxes. Add real wall
   thickness and a partial front return per building (a low "reveal" wall
   segment, not a full box) so the overview camera sees an enclosed volume
   while the open-front interior read (explicitly required by
   `docs/lunar-city-design-handoff.md`) survives up close. Start with
   `library`/`research-lab` since they're the two hero buildings in the
   approved reference composition.
2. **Warm window glow.** `APPROVED_PALETTE` has no warm emissive (only
   cyan `signal-emissive` and purple `archive-emissive`); the reference art
   is dominated by warm lantern-lit windows. Get sign-off before adding a
   9th palette color, then trim window/doorway openings with it so the new
   `GlowLayer` has something warm to bloom, not just cool accents.
3. **Roofline variety.** Reference buildings have distinct dome/vault/tier
   silhouettes per district; several current roofs are near-identical
   stepped-gable boxes. Differentiate 2-3 more per building.
4. **Leader/character scale.** In the turntable and the WebGL preview, the
   six leaders read as small props next to the buildings; check them
   against the camera-focus framing in `manifest.ts`, not just raw scale,
   since the focus camera may already compensate.
5. Re-run `render-webgl-preview.mjs` after each geometry pass — it's cheap
   (~2s render) and is the fastest way to see whether a change actually
   closes the gap before spending an Electron packaging cycle on it.

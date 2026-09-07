# Lunar City open-asset bridge catalog

This catalog identifies external sources that can help close the visual gap
without copying proprietary StarCraft, Warcraft, or Baldur's Gate assets.
External files stay in an isolated benchmark/optional-pack repository. They do
not enter `apps/desktop/public/lunar-city/` until their license, hashes,
budgets, metadata, and provenance are recorded.

## Priority sources

| Source | License | Best use | Distribution decision |
| --- | --- | --- | --- |
| [Kenney Hexagon Kit](https://kenney.nl/assets/hexagon-kit) | CC0 | Modular sci-fi/fantasy footprints, roads, tiles, scale | Safe candidate for optional pack or benchmark |
| [Kenney City Kit](https://kenney.nl/assets/city-kit-commercial) | CC0 | Facade rhythm, modular walls, roof massing | Safe candidate for optional pack or benchmark |
| [Kenney Retro Fantasy Kit](https://kenney.nl/assets/retro-fantasy-kit) | CC0 | Fantasy props, silhouettes, decorative kitbash study | Safe candidate for optional pack or benchmark |
| [Quaternius Sci-Fi Essentials](https://quaternius.com/) | CC0 | Robots, props, sci-fi structures, animation pipeline | Safe to modify/combine; do not redistribute as a standalone pack |
| [Quaternius Fantasy Props / Medieval Village](https://quaternius.com/) | CC0 | Furniture, crates, books, plants, towers, roof details | Safe to modify/combine; do not redistribute as a standalone pack |
| [Quaternius Stylized Nature](https://quaternius.com/) | CC0 | Rocks, plants, crystals, ground dressing | Safe candidate for optional pack or benchmark |
| [Poly Haven](https://polyhaven.com/license) | CC0 | Rocks, HDRIs, surfaces, lighting/material tests | Safe candidate; preserve source attribution link |

## Kenney Starter Kit City Builder review

The [Starter-Kit-City-Builder repository](https://github.com/KenneyNL/Starter-Kit-City-Builder)
(staged at source revision `4535092b740b378b700efd9df9e27a631815b84a`) is a
strong diagnostic and optional source. Its code is MIT-licensed and its
included models, sprites, and sounds are CC0. It is a Godot 4.6 template with
smooth camera movement, middle-mouse rotation, zoom, grid placement/removal,
dynamic MeshLibrary creation, and save/load behavior.

Useful pieces for Lunar City:

- `models/road-*`, `pavement*`, and `road-straight-lightposts.glb` can improve
  low-cost route dressing and repeatable modular paths.
- `models/grass*.glb` can add a second, non-emissive nature layer around the
  garden and crater rims.
- `models/building-small-*.glb` and `building-garage.glb` are useful scale and
  facade-rhythm references, but should not replace Lunar City landmarks.
- `scripts/view.gd` is a clear diagnostic reference for our existing camera
  contract: smoothed pan/rotation and a bounded 15–80 m zoom range.
- `scripts/builder.gd` demonstrates a lightweight grid-build interaction that
  could inform a future optional construction mode, but it is Godot-specific
  and should not be copied into the Electron/Babylon runtime.

Recommended disposition: import the CC0 GLBs through the existing quarantine
receipt and Blender staging bridge as a separately labeled `KENNEY_CITY_BUILDER`
collection. Use the visual kit for roads, props, and scale studies first; keep
the current Lunar City identity, worker metadata, open-front building contract,
and asset-neutral distribution boundary intact. No files from this repository
are bundled by this review.

## Diagnostic-only or separately packaged sources

- [0 A.D.](https://play0ad.com/) art is CC-BY-SA 3.0 while code is GPLv2.
  Its city/fortification composition is an excellent diagnostic reference,
  but any reused art needs attribution and share-alike handling in a separate
  optional pack.
- [Battle for Wesnoth](https://wiki.wesnoth.org/Wesnoth%3ACopyrights) art is
  GPLv2-or-later or CC-BY-SA 4.0. It is useful for fantasy role and silhouette
  studies, but is not a default dependency for the Hermes desktop bundle.
- OpenGameArt is a mixed-license index. Review every file individually; do not
  treat a search result or “free” label as a license grant.

## Import order for closing the visual gap

1. Rocks, crystals, plants, and small props (lowest integration risk).
2. Modular wall, roof, window, and trim pieces for landmark construction.
3. Sci-fi/fantasy furniture and operational props for interiors.
4. Retargetable characters and animation clips, only after skeleton/scale
   compatibility is measured.
5. Full building replacements last; preserve Lunar City's identity metadata,
   open-front contract, navigation anchors, and LOD declarations.

## Required receipt for every imported file

Record source URL, creator, exact license, download/version date, SHA-256,
whether modification is allowed, whether redistribution is allowed, triangle/
material/draw/texture budgets, required nodes, LOD tier, and the Lunar City
model or material it replaces. Keep the source package outside the core tree
until the receipt is complete and `validate-assets.mjs` passes.

The quarantine step is automated by
`apps/desktop/scripts/lunar-city/import-open-asset-pack.mjs`:

```bash
node scripts/lunar-city/import-open-asset-pack.mjs <pack-directory> <quarantine-directory>
```

It refuses the shipped Lunar City asset path and writes `asset-receipt.json`
with file sizes and SHA-256 hashes. The receipt is intentionally marked for
human license review before any model is mapped into a runtime manifest.

The target is visual-quality parity through richer composition and authored
detail—not a recognizable clone of another game's art direction.

For a large multi-kit download, curate before Blender import so the benchmark
does not become a 10,000-object scene:

```bash
node apps/desktop/scripts/lunar-city/curate-open-asset-pack.mjs \
  "/Users/mikedemott/Downloads/Free OpenSource Game Assets" \
  /tmp/lunar-city-open-asset-curated
```

The curator selects up to 220 high-value GLB/GLTF/FBX/OBJ files from the space,
city, factory, nature, fantasy, and Quaternius kits and writes
`curation-receipt.json`. An optional third argument adds a focused kit (such as
the Starter Kit City Builder) with a priority score while preserving its source
root in the receipt:

```bash
node apps/desktop/scripts/lunar-city/curate-open-asset-pack.mjs \
  "/Users/mikedemott/Downloads/Free OpenSource Game Assets" \
  /tmp/lunar-city-open-asset-curated \
  /tmp/kenney-starter-kit-city-builder/models
```

Every selected file remains marked for license review before distribution. The
Starter Kit contributes its 15 CC0 GLBs (roads, pavements, small buildings,
garage, lightposts, and grass) as a separately traceable benchmark subset.

## Blender staging bridge

The repository now includes an asset-neutral Blender staging script:

```bash
blender --background --python apps/desktop/scripts/lunar-city/blender_stage.py -- \
  --asset-root apps/desktop/public/lunar-city/v2/models \
  --polyhaven-dir /path/to/quarantined/polyhaven \
  --output /tmp/lunar-city-stage.blend
```

It imports current Lunar City GLBs into editable `LUNAR_CITY::BUILDINGS`,
`LUNAR_CITY::WORKERS`, and `LUNAR_CITY::TERRAIN` collections, creates a small
palette and staging camera/light, and places reviewed Poly Haven models or
texture preview cards in `POLYHAVEN_BENCHMARK`. It writes a sidecar staging
receipt with SHA-256 hashes and marks external content for review. Poly Haven
files are intentionally not bundled; the recommended IDs and source links are
in [`polyhaven-lunar-city-benchmarks.json`](polyhaven-lunar-city-benchmarks.json).

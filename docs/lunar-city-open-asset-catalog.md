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

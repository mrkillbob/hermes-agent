# Lunar City empty-scene authoring rebuild

The one-shot rebuild creates a new Blender authoring world from an empty scene.
The current runtime GLBs are used only for the manifest coordinate and scene
contracts; they are not imported or treated as approved art.

## Run

```bash
node apps/desktop/scripts/lunar-city/one-shot-rebuild.mjs
```

The command merges the existing quarantined curation with the cloned Selene
ISRU, ModKit, and KayKit model sources, records SHA-256 hashes and repository
heads under `/private/tmp/lunar-city-one-shot/`, and launches Blender through
`run-one-shot-rebuild.sh`. The RFX 4K concrete source is attached to the
authoring pressure-shell material when available.

Outputs are intentionally outside the repository:

- `/private/tmp/lunar-city-one-shot/lunar-city-rebuilt.blend`
- `/private/tmp/lunar-city-one-shot/lunar-city-rebuilt.png`
- `/private/tmp/lunar-city-one-shot/lunar-city-rebuilt.receipt.json`
- `/private/tmp/lunar-city-one-shot/one-shot-sources.receipt.json`

## Art and runtime boundary

The rebuild includes all 11 district buildings, terrain and regolith dressing,
transit surfaces, central plaza, leaders, workers, and a quarantined source
gallery. Generated objects are tagged with bake/LOD metadata. No rebuilt
object is runtime-approved: promotion requires art-direction review, texture
baking, reduction to near/mid/far LODs, and the existing asset validator.

The expected reduction targets are near 20k-40k triangles with 2K maps, mid
5k-12k triangles with 1K-2K maps, and far 500-2k triangles using an atlas or
vertex color.

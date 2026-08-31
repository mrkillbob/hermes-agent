import { Color3, PBRMaterial } from './babylon.mjs'

export const APPROVED_PALETTE = Object.freeze({
  'archive-emissive': '#A467E8',
  'bone-metal': '#D7C5AF',
  'charcoal-structure': '#242431',
  'garden-green': '#72C66A',
  'lunar-rust': '#A84623',
  'signal-emissive': '#43D6E8',
  'sunset-orange': '#D96A31',
  'triage-amber': '#F2B53A'
})

const MATERIALS = new WeakMap()

export function colorFromHex(hex) {
  return Color3.FromHexString(hex)
}

// Per-material PBR recipe. Flat "one metallic/roughness for everything"
// reads as toy blocks; giving structural, trim, and signage materials
// distinct finishes is what lets the low-poly shell read as StarCraft-style
// architecture instead of untextured primitives once lit.
const MATERIAL_RECIPES = Object.freeze({
  'archive-emissive': { emissive: 0.85, metallic: 0.08, roughness: 0.22 },
  'bone-metal': { emissive: 0, metallic: 0.62, roughness: 0.32 },
  'charcoal-structure': { emissive: 0, metallic: 0.12, roughness: 0.88 },
  'garden-green': { emissive: 0.24, metallic: 0.08, roughness: 0.56 },
  'lunar-rust': { emissive: 0, metallic: 0.34, roughness: 0.6 },
  'signal-emissive': { emissive: 0.85, metallic: 0.08, roughness: 0.22 },
  'sunset-orange': { emissive: 0.14, metallic: 0.24, roughness: 0.48 },
  'triage-amber': { emissive: 0.4, metallic: 0.1, roughness: 0.4 }
})

export function paletteMaterial(scene, id) {
  if (!(id in APPROVED_PALETTE)) throw new Error(`unknown approved palette material: ${id}`)
  let sceneMaterials = MATERIALS.get(scene)
  if (!sceneMaterials) {
    sceneMaterials = new Map()
    MATERIALS.set(scene, sceneMaterials)
  }
  if (sceneMaterials.has(id)) return sceneMaterials.get(id)

  const material = new PBRMaterial(id, scene)
  const color = colorFromHex(APPROVED_PALETTE[id])
  const recipe = MATERIAL_RECIPES[id]
  material.albedoColor = color
  material.metallic = recipe.metallic
  material.roughness = recipe.roughness
  if (recipe.emissive > 0) material.emissiveColor = color.scale(recipe.emissive)
  sceneMaterials.set(id, material)
  return material
}

export function approvedPaletteBytes() {
  return Object.values(APPROVED_PALETTE).map(hex => {
    const value = Number.parseInt(hex.slice(1), 16)
    return [(value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff, 0xff]
  })
}

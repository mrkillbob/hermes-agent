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
  material.albedoColor = color
  material.metallic = id.includes('emissive') ? 0.2 : 0.48
  material.roughness = id.includes('emissive') ? 0.34 : 0.72
  if (id.includes('emissive') || id === 'garden-green' || id === 'triage-amber') {
    material.emissiveColor = color.scale(id.includes('emissive') ? 0.72 : 0.22)
  }
  sceneMaterials.set(id, material)
  return material
}

export function approvedPaletteBytes() {
  return Object.values(APPROVED_PALETTE).map(hex => {
    const value = Number.parseInt(hex.slice(1), 16)
    return [(value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff, 0xff]
  })
}

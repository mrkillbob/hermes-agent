/**
 * One-off layout solver, not part of the build pipeline: computes a
 * DISTRICTS position for every district from its *actual* footprint
 * (post-shellScale width/depth, or radius for the circular garden) plus a
 * required clearance margin, using simple pairwise repulsion relaxation
 * seeded from zone-grouped starting angles. Run once, paste the printed
 * DISTRICTS object into terrain.mjs by hand -- this script does not write
 * any file itself, so a bad solve can't silently corrupt terrain.mjs.
 *
 * Usage: node scripts/lunar-city/layout-solver.mjs
 */

// width/depth are the footprint's plan-view extents (already includes each
// building's shellScale width multiplier from primitives.mjs's per-profile
// table); garden is circular so only radius is given.
const FOOTPRINTS = {
  archive: { width: 13.5, depth: 11 },
  'arts-studio': { width: 14, depth: 10.5 },
  bus: { width: 12, depth: 8.2 },
  council: { width: 14 * 1.42, depth: 10.5 },
  depot: { width: 14.5 * 1.14, depth: 11 },
  'engineering-workshop': { width: 15.5, depth: 12 },
  garden: { radius: 9.5 },
  library: { width: 15 * 1.06, depth: 12 },
  'release-gatehouse': { width: 12.5, depth: 9.5 },
  'research-lab': { width: 18 * 0.96, depth: 14 },
  'review-office': { width: 15.5 * 0.62, depth: 11.5 },
  triage: { width: 9.5, depth: 7.4 }
}

// Conservative per-building radius: half the footprint diagonal (or the
// circle radius for garden). Using the diagonal, not half-width, means the
// clearance check doesn't assume any particular facing rotation -- correct
// regardless of how facingRotationY ends up orienting the building.
function footprintRadius(id) {
  const f = FOOTPRINTS[id]
  if (f.radius) return f.radius
  return Math.hypot(f.width, f.depth) / 2
}

// Margin added on top of both buildings' radii: walkway width (~4-5) plus
// visual breathing room so buildings don't read as touching even when the
// strict collision check clears.
const MARGIN = 7.5

const ZONES = {
  archive: 'civic',
  'arts-studio': 'civic',
  bus: 'plaza',
  council: 'civic',
  depot: 'pipeline',
  'engineering-workshop': 'pipeline',
  garden: 'care',
  library: 'civic',
  'release-gatehouse': 'pipeline',
  'research-lab': 'civic',
  'review-office': 'pipeline',
  triage: 'care'
}

// Seed angles (degrees, 0 = +X/east, counter-clockwise) and radii per zone
// group -- civic spreads north (negative Z, where camera.bounds gives the
// most room: min Z is -60 vs max Z only +36), pipeline sweeps southwest to
// west approaching the plaza, care sits southeast within the tighter +Z
// bound, plaza anchors stay put.
// angle convention: 0deg = +X, 90deg = +Z. camera.bounds is asymmetric
// (Z: -60..+36, X: -60..+60), so the +Z direction is the tightest -- civic
// (5 buildings, the largest cluster) goes north (-Z, -90deg) where there's
// the most room; pipeline goes west (180deg, X negative, Z near 0); care
// (2 small buildings) takes the remaining +X/+Z sector, which is fine
// precisely because it's small enough not to need much of the tight +Z room.
const SEED = {
  archive: { angle: -60, radius: 46 },
  'arts-studio': { angle: -95, radius: 40 },
  bus: { angle: 0, radius: 0 },
  council: { angle: -75, radius: 24 },
  depot: { angle: 210, radius: 38 },
  'engineering-workshop': { angle: 190, radius: 54 },
  garden: { angle: 55, radius: 30 },
  library: { angle: -130, radius: 34 },
  'release-gatehouse': { angle: 155, radius: 22 },
  'research-lab': { angle: -110, radius: 44 },
  'review-office': { angle: 175, radius: 30 },
  triage: { angle: 30, radius: 22 }
}

// Each district may only drift within its zone's angular wedge (relative to
// the plaza) during relaxation -- unconstrained repulsion resolved overlaps
// fine but scattered buildings across the whole map with no regard for the
// civic/pipeline/care grouping the city plan is built around. Radius is
// unconstrained (zones are allowed to push outward), only angle is clamped.
const ZONE_WEDGE = {
  civic: [-150, -30],
  pipeline: [110, 250],
  care: [0, 70],
  plaza: [0, 0]
}

const CAMERA_BOUNDS = { maxX: 60, maxZ: 36, minX: -60, minZ: -60 }

const ids = Object.keys(FOOTPRINTS)
const pos = {}
for (const id of ids) {
  const { angle, radius } = SEED[id]
  const rad = (angle * Math.PI) / 180
  pos[id] = [Math.cos(rad) * radius, Math.sin(rad) * radius]
}

function requiredDistance(a, b) {
  return footprintRadius(a) + footprintRadius(b) + MARGIN
}

function clampToBounds(id, p) {
  const r = footprintRadius(id)
  p[0] = Math.min(CAMERA_BOUNDS.maxX - r, Math.max(CAMERA_BOUNDS.minX + r, p[0]))
  p[1] = Math.min(CAMERA_BOUNDS.maxZ - r, Math.max(CAMERA_BOUNDS.minZ + r, p[1]))
}

function clampToWedge(id, p) {
  const [minDeg, maxDeg] = ZONE_WEDGE[ZONES[id]]
  const radius = Math.hypot(p[0], p[1])
  if (radius < 0.01) return
  let angleDeg = (Math.atan2(p[1], p[0]) * 180) / Math.PI
  angleDeg = Math.min(maxDeg, Math.max(minDeg, angleDeg))
  const rad = (angleDeg * Math.PI) / 180
  p[0] = Math.cos(rad) * radius
  p[1] = Math.sin(rad) * radius
}

// bus is the plaza anchor and never moves; everything else relaxes around it.
const FIXED = new Set(['bus'])

for (let iteration = 0; iteration < 6000; iteration += 1) {
  let maxViolation = 0
  for (let i = 0; i < ids.length; i += 1) {
    for (let j = i + 1; j < ids.length; j += 1) {
      const a = ids[i]
      const b = ids[j]
      const dx = pos[b][0] - pos[a][0]
      const dz = pos[b][1] - pos[a][1]
      const dist = Math.hypot(dx, dz) || 0.001
      const need = requiredDistance(a, b)
      if (dist >= need) continue
      const push = (need - dist) / 2
      maxViolation = Math.max(maxViolation, need - dist)
      const ux = dx / dist
      const uz = dz / dist
      if (!FIXED.has(a)) {
        pos[a][0] -= ux * push
        pos[a][1] -= uz * push
      }
      if (!FIXED.has(b)) {
        pos[b][0] += ux * push
        pos[b][1] += uz * push
      }
    }
  }
  for (const id of ids)
    if (!FIXED.has(id)) {
      clampToWedge(id, pos[id])
      clampToBounds(id, pos[id])
    }
  if (maxViolation < 0.01) {
    console.log(`converged after ${iteration} iterations`)
    break
  }
}

// Final verification pass, independent of the solver loop above.
let ok = true
for (let i = 0; i < ids.length; i += 1) {
  for (let j = i + 1; j < ids.length; j += 1) {
    const a = ids[i]
    const b = ids[j]
    const dist = Math.hypot(pos[a][0] - pos[b][0], pos[a][1] - pos[b][1])
    const need = requiredDistance(a, b)
    if (dist < need - 0.05) {
      ok = false
      console.log(`VIOLATION: ${a} <-> ${b} dist=${dist.toFixed(2)} need=${need.toFixed(2)}`)
    }
  }
}
for (const id of ids) {
  const r = footprintRadius(id)
  const [x, z] = pos[id]
  if (
    x - r < CAMERA_BOUNDS.minX ||
    x + r > CAMERA_BOUNDS.maxX ||
    z - r < CAMERA_BOUNDS.minZ ||
    z + r > CAMERA_BOUNDS.maxZ
  ) {
    ok = false
    console.log(`OUT OF BOUNDS: ${id} center=(${x.toFixed(1)},${z.toFixed(1)}) radius=${r.toFixed(1)}`)
  }
}
console.log(ok ? 'ALL CLEARANCES OK, WITHIN CAMERA BOUNDS' : 'FAILED -- see violations above')

// Y (terrain pad height) preserved from the previous layout per building --
// unrelated to this x/z clearance solve.
const Y = {
  archive: 0.5,
  'arts-studio': 0.7,
  bus: 0.55,
  council: 0.35,
  depot: 0.45,
  'engineering-workshop': 0.6,
  garden: 0.25,
  library: 0.8,
  'release-gatehouse': 0.5,
  'research-lab': 1.1,
  'review-office': 0.7,
  triage: 0.4
}

console.log('\nexport const DISTRICTS = Object.freeze({')
for (const id of [...ids].sort()) {
  const [x, z] = pos[id]
  console.log(
    `  '${id}': Object.freeze({ position: [${x.toFixed(2)}, ${Y[id]}, ${z.toFixed(2)}], zone: '${ZONES[id]}' }),`
  )
}
console.log('})')

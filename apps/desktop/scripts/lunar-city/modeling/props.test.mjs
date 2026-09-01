import assert from 'node:assert/strict'
import { test } from 'node:test'

import { NullEngine, Scene, TransformNode } from './babylon.mjs'
import { addPortal } from './props.mjs'

function portalMeshes(scene, portal) {
  return scene.meshes.filter(mesh => {
    for (let parent = mesh.parent; parent; parent = parent.parent) if (parent === portal) return true
    return false
  })
}

test('keeps the review portal contract while using low-poly static geometry', () => {
  const engine = new NullEngine({ renderingPipeline: false })
  const scene = new Scene(engine)
  try {
    const parent = new TransformNode('review-office:root', scene)
    const portal = addPortal(scene, parent)
    const meshes = portalMeshes(scene, portal)
    const triangles = meshes.reduce((total, mesh) => total + mesh.getTotalIndices() / 3, 0)
    const materials = new Set(meshes.map(mesh => mesh.material?.name))

    assert.equal(portal.name, 'review-office:portal')
    assert.ok(triangles <= 704, `portal geometry must stay within the 704-triangle low-poly cap (got ${triangles})`)
    assert.deepEqual([...materials].toSorted(), ['archive-emissive', 'bone-metal'])
    assert.ok(scene.animationGroups.some(group => group.name === 'portal-idle'))
    assert.ok(scene.animationGroups.find(group => group.name === 'portal-idle').targetedAnimations.some(({ target }) => target === portal))
  } finally {
    scene.dispose()
    engine.dispose()
  }
})

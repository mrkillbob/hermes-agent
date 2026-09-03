import assert from 'node:assert/strict'
import { test } from 'node:test'
import { join } from 'node:path'
import { auditLunarCityModel } from '../audit-lunar-city-model.mjs'

const FIXTURE_GLB = join(process.cwd(), 'public/lunar-city/v2/models/terrain.glb')

test('audits a GLB without emitting raw model data', async () => {
  const result = await auditLunarCityModel(FIXTURE_GLB)
  assert.match(result.sha256, /^[a-f0-9]{64}$/)
  assert.equal(typeof result.nodes, 'number')
  assert.equal('buffer' in result, false)
  assert.equal('root' in result, false)
})

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { OMNIVERSE_RECEIPT_SCHEMA, validateOmniverseExport } from './validate-omniverse-export.mjs'

const manifest = {
  version: 2,
  assetVersion: '2.0.0',
  source: { sha256: 'a'.repeat(64) },
  qualityBudgets: {
    balancedOverview: { drawCalls: 180, visibleTriangles: 1_500_000, gpuMiB: 256 },
    balancedWorkerFocus: { drawCalls: 220, visibleTriangles: 2_000_000, gpuMiB: 256 }
  }
}

function receipt(overrides = {}) {
  return {
    schema_name: OMNIVERSE_RECEIPT_SCHEMA,
    schema_version: 1,
    asset_version: '2.0.0',
    manifest_source_sha256: 'a'.repeat(64),
    stage: { usd_path: 'omniverse://lunar-city/world.usd', identifier: '/World/LunarCity' },
    exported_at: '2026-09-05T10:00:00Z',
    status: 'validated',
    reference_only: true,
    production_approved: false,
    budget_profile: 'balancedOverview',
    provenance: {
      source_repo: 'NVIDIA/skills',
      source_revision: 'abcdef1234567890',
      workflow: 'omniverse-usd-performance-tuning'
    },
    performance: { draw_calls: 42, visible_triangles: 100_000, gpu_mib: 64 },
    ...overrides
  }
}

test('accepts a bounded reference-only Omniverse export', () => {
  const result = validateOmniverseExport(receipt(), manifest)
  assert.equal(result.ok, true)
  assert.equal(result.classification, 'reference_only')
  assert.equal(result.runtime_eligible, false)
})

test('rejects a digest mismatch and budget overflow', () => {
  const result = validateOmniverseExport(
    receipt({
      manifest_source_sha256: 'b'.repeat(64),
      performance: { draw_calls: 181, visible_triangles: 100_000, gpu_mib: 64 }
    }),
    manifest
  )
  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /source digest/)
  assert.match(result.errors.join('\n'), /draw-call budget/)
})

test('production mode requires a non-reference accepted receipt', () => {
  const result = validateOmniverseExport(
    receipt({ reference_only: false, production_approved: true, status: 'accepted' }),
    manifest,
    { mode: 'production' }
  )
  assert.equal(result.ok, true)
  assert.equal(result.classification, 'production_candidate')
})

test('rejects contradictory approval flags', () => {
  const result = validateOmniverseExport(receipt({ production_approved: true }), manifest)
  assert.equal(result.ok, false)
  assert.match(result.errors.join('\n'), /reference-only export/)
})

import { readFile } from 'node:fs/promises'

export const OMNIVERSE_RECEIPT_SCHEMA = 'nvidia_omniverse_asset_receipt_v1'

function isRecord(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function nonEmpty(value) {
  return typeof value === 'string' && value.trim().length > 0
}

function finiteNonNegative(value) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

/**
 * Validate an Omniverse/USD export receipt against Lunar City's existing
 * manifest and quality budgets. This is an asset-pipeline boundary; it does
 * not make the renderer or Electron depend on Omniverse.
 */
export function validateOmniverseExport(receipt, manifest, { mode = 'preview' } = {}) {
  const errors = []
  if (!isRecord(receipt) || receipt.schema_name !== OMNIVERSE_RECEIPT_SCHEMA) {
    errors.push('receipt schema_name is invalid')
  }
  if (receipt?.schema_version !== 1) errors.push('receipt schema_version must equal 1')
  if (!isRecord(manifest) || manifest.version !== 2) errors.push('world manifest version must equal 2')
  if (receipt?.asset_version !== manifest?.assetVersion) errors.push('asset_version does not match world manifest')
  if (receipt?.manifest_source_sha256 !== manifest?.source?.sha256) {
    errors.push('manifest source digest does not match world manifest')
  }

  const stage = receipt?.stage
  if (!isRecord(stage) || !nonEmpty(stage.usd_path) || !nonEmpty(stage.identifier)) {
    errors.push('USD stage path and identifier are required')
  }
  if (!nonEmpty(receipt?.exported_at)) errors.push('exported_at is required')
  if (!nonEmpty(receipt?.status) || !['validated', 'accepted', 'best_effort'].includes(receipt.status)) {
    errors.push('receipt status is invalid')
  }
  if (typeof receipt?.reference_only !== 'boolean') errors.push('reference_only must be boolean')
  if (typeof receipt?.production_approved !== 'boolean') errors.push('production_approved must be boolean')
  if (!isRecord(receipt?.provenance)) {
    errors.push('provenance is required')
  } else {
    for (const field of ['source_repo', 'source_revision', 'workflow']) {
      if (!nonEmpty(receipt.provenance[field])) errors.push(`provenance.${field} is required`)
    }
  }

  const budgetProfile = receipt?.budget_profile
  const qualityBudgets = manifest?.qualityBudgets
  const hasOwnBudgetProfile = isRecord(qualityBudgets) && Object.hasOwn(qualityBudgets, budgetProfile)
  const budget = hasOwnBudgetProfile ? qualityBudgets[budgetProfile] : undefined
  const budgetHasValidLimits =
    hasOwnBudgetProfile &&
    isRecord(budget) &&
    ['drawCalls', 'visibleTriangles', 'gpuMiB'].every((field) => finiteNonNegative(budget[field]))
  if (!hasOwnBudgetProfile || !isRecord(budget)) {
    errors.push('budget_profile is not in world manifest qualityBudgets')
  } else if (!budgetHasValidLimits) {
    errors.push('quality budget limits must be finite and non-negative')
  }
  const performance = receipt?.performance
  if (!isRecord(performance)) {
    errors.push('performance metrics are required')
  } else {
    for (const field of ['draw_calls', 'visible_triangles', 'gpu_mib']) {
      if (!finiteNonNegative(performance[field])) errors.push(`performance.${field} must be finite and non-negative`)
    }
    if (budgetHasValidLimits && finiteNonNegative(performance.draw_calls) && performance.draw_calls > budget.drawCalls) {
      errors.push('draw-call budget exceeded')
    }
    if (budgetHasValidLimits && finiteNonNegative(performance.visible_triangles) && performance.visible_triangles > budget.visibleTriangles) {
      errors.push('visible-triangle budget exceeded')
    }
    if (budgetHasValidLimits && finiteNonNegative(performance.gpu_mib) && performance.gpu_mib > budget.gpuMiB) {
      errors.push('GPU memory budget exceeded')
    }
  }

  if (mode === 'production') {
    if (receipt.reference_only !== false) errors.push('production mode rejects reference-only exports')
    if (receipt.production_approved !== true) errors.push('production mode requires production_approved')
    if (receipt.status !== 'accepted') errors.push('production mode requires accepted status')
  }
  if (receipt.production_approved === true && receipt.reference_only === true) {
    errors.push('reference-only export cannot be production-approved')
  }

  const classification = receipt.production_approved === true ? 'production_candidate' : 'reference_only'
  return {
    ok: errors.length === 0,
    errors: Object.freeze(errors),
    classification,
    runtime_eligible: false,
    manifest_source_sha256: manifest?.source?.sha256 ?? null,
    budget_profile: budgetProfile ?? null,
    mode
  }
}
function option(args, name, fallback = undefined) {
  const index = args.indexOf(name)
  return index >= 0 ? args[index + 1] ?? fallback : fallback
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const args = process.argv.slice(2)
  const receiptPath = option(args, '--receipt')
  const manifestPath = option(args, '--manifest', 'public/lunar-city/v2/world-manifest.v2.json')
  const mode = option(args, '--mode', 'preview')
  if (!receiptPath) {
    console.error('Usage: node validate-omniverse-export.mjs --receipt <receipt.json> [--manifest <manifest.json>] [--mode preview|production]')
    process.exitCode = 2
  } else {
    const [receipt, manifest] = await Promise.all([
      readFile(receiptPath, 'utf8').then(JSON.parse),
      readFile(manifestPath, 'utf8').then(JSON.parse)
    ])
    const result = validateOmniverseExport(receipt, manifest, { mode })
    console.log(JSON.stringify(result, null, 2))
    if (!result.ok) process.exitCode = 1
  }
}

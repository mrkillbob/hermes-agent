import { createHash } from 'node:crypto'
import { readFile, realpath, stat } from 'node:fs/promises'
import path from 'node:path'

const FORMATS = new Set(['glb', 'gltf', 'obj', 'fbx'])
const ARTIFACT_STATUSES = new Set(['candidate', 'review', 'approved', 'rejected'])
const LICENSE_STATUSES = new Set(['pending', 'cleared', 'restricted'])

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function requiredString(value, label, errors) {
  if (typeof value !== 'string' || value.trim() === '') {
    errors.push(`${label} is required`)
    return false
  }
  return true
}

function isSha256(value) {
  return typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value)
}

function validateRelativeArtifactPath(value, errors) {
  if (typeof value !== 'string' || value.trim() === '') {
    errors.push('artifact.path is required')
    return false
  }
  const normalized = value.replaceAll('\\', '/')
  if (path.posix.isAbsolute(normalized) || normalized === '.' || normalized === '..' || normalized.startsWith('../') || normalized.includes('/../')) {
    errors.push('artifact.path must be relative and stay inside the candidate directory')
    return false
  }
  return true
}

function validateCandidate(candidate, index, errors) {
  const prefix = `candidates[${index}]`
  if (!isRecord(candidate)) {
    errors.push(`${prefix} must be an object`)
    return
  }
  requiredString(candidate.id, `${prefix}.id`, errors)
  requiredString(candidate.targetModelId, `${prefix}.targetModelId`, errors)

  if (!isRecord(candidate.artifact)) {
    errors.push(`${prefix}.artifact is required`)
  } else {
    validateRelativeArtifactPath(candidate.artifact.path, errors)
    if (!FORMATS.has(candidate.artifact.format)) errors.push(`${prefix}.artifact.format must be one of glb, gltf, obj, fbx`)
    if (!isSha256(candidate.artifact.sha256)) errors.push(`${prefix}.artifact.sha256 must be a 64-character SHA-256 digest`)
  }

  if (!isRecord(candidate.source)) {
    errors.push(`${prefix}.source is required`)
  } else {
    if (!Array.isArray(candidate.source.images) || candidate.source.images.length === 0) {
      errors.push(`${prefix}.source.images must contain at least one image reference`)
    } else {
      candidate.source.images.forEach((image, imageIndex) => {
        const imagePrefix = `${prefix}.source.images[${imageIndex}]`
        if (!isRecord(image)) {
          errors.push(`${imagePrefix} must be an object`)
          return
        }
        requiredString(image.path, `${imagePrefix}.path`, errors)
        if (!isSha256(image.sha256)) errors.push(`${imagePrefix}.sha256 must be a 64-character SHA-256 digest`)
      })
    }
    requiredString(candidate.source.designReference, `${prefix}.source.designReference`, errors)
  }

  if (!isRecord(candidate.generator)) {
    errors.push(`${prefix}.generator is required`)
  } else {
    requiredString(candidate.generator.id, `${prefix}.generator.id`, errors)
    requiredString(candidate.generator.repository, `${prefix}.generator.repository`, errors)
    requiredString(candidate.generator.model, `${prefix}.generator.model`, errors)
  }

  if (!isRecord(candidate.review)) {
    errors.push(`${prefix}.review is required`)
  } else {
    if (!ARTIFACT_STATUSES.has(candidate.review.artifactStatus)) errors.push(`${prefix}.review.artifactStatus is invalid`)
    if (!LICENSE_STATUSES.has(candidate.review.licenseStatus)) errors.push(`${prefix}.review.licenseStatus is required`)
  }

  if (!isRecord(candidate.normalization)) {
    errors.push(`${prefix}.normalization is required`)
  } else {
    requiredString(candidate.normalization.anchor, `${prefix}.normalization.anchor`, errors)
    if (!Number.isFinite(candidate.normalization.maxExtent) || candidate.normalization.maxExtent <= 0) {
      errors.push(`${prefix}.normalization.maxExtent must be positive`)
    }
    if (candidate.normalization.preserveAspect !== true) errors.push(`${prefix}.normalization.preserveAspect must be true`)
  }

  if (!isRecord(candidate.constraints) || !Array.isArray(candidate.constraints.hull) || candidate.constraints.hull.length === 0) {
    errors.push(`${prefix}.constraints.hull must contain at least one boundary`)
  }
  for (const key of ['avoidance', 'touch']) {
    if (!isRecord(candidate.constraints) || !Array.isArray(candidate.constraints[key])) errors.push(`${prefix}.constraints.${key} must be an array`)
  }
}

export function validateGeneratedCandidateManifest(manifest) {
  const errors = []
  if (!isRecord(manifest)) return { valid: false, errors: ['manifest must be an object'] }
  if (manifest.version !== 1) errors.push('version must be 1')
  if (!isRecord(manifest.reference)) {
    errors.push('reference is required')
  } else {
    requiredString(manifest.reference.design, 'reference.design', errors)
    if (!Array.isArray(manifest.reference.images) || manifest.reference.images.length === 0) errors.push('reference.images must contain at least one image')
  }
  if (!Array.isArray(manifest.candidates)) {
    errors.push('candidates must be an array')
  } else {
    const ids = new Set()
    manifest.candidates.forEach((candidate, index) => {
      validateCandidate(candidate, index, errors)
      if (candidate?.id) {
        if (ids.has(candidate.id)) errors.push(`candidates[${index}].id must be unique`)
        ids.add(candidate.id)
      }
    })
  }
  return { valid: errors.length === 0, errors }
}

function isContained(root, target) {
  const relative = path.relative(root, target)
  return relative !== '' && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)
}

async function sha256(filePath) {
  const digest = createHash('sha256')
  digest.update(await readFile(filePath))
  return digest.digest('hex')
}

export async function readAndValidateGeneratedCandidateManifest(manifestPath, candidateRoot) {
  let manifest
  try {
    manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
  } catch (error) {
    return { valid: false, errors: [`unable to read generated candidate manifest: ${error.message}`] }
  }
  const contract = validateGeneratedCandidateManifest(manifest)
  if (!contract.valid) return contract

  const root = await realpath(candidateRoot)
  const candidates = []
  const errors = []
  for (const [index, candidate] of manifest.candidates.entries()) {
    const relativeArtifact = candidate.artifact.path.replaceAll('\\', '/')
    const resolvedArtifact = path.resolve(root, relativeArtifact)
    if (!isContained(root, resolvedArtifact)) {
      errors.push(`candidates[${index}].artifact.path must stay inside the candidate directory`)
      continue
    }
    let actualPath
    try {
      actualPath = await realpath(resolvedArtifact)
      const artifactStat = await stat(actualPath)
      if (!artifactStat.isFile()) throw new Error('artifact is not a file')
    } catch (error) {
      errors.push(`candidates[${index}] artifact is unavailable: ${error.message}`)
      continue
    }
    if (!isContained(root, actualPath)) {
      errors.push(`candidates[${index}].artifact.path resolves outside the candidate directory`)
      continue
    }
    const actualSha256 = await sha256(actualPath)
    if (actualSha256.toLowerCase() !== candidate.artifact.sha256.toLowerCase()) {
      errors.push(`candidates[${index}] artifact SHA-256 does not match the manifest`)
      continue
    }
    candidates.push({
      ...candidate,
      artifact: { ...candidate.artifact, absolutePath: actualPath, sha256Verified: true }
    })
  }
  return {
    valid: errors.length === 0,
    errors,
    version: manifest.version,
    reference: manifest.reference,
    candidates
  }
}

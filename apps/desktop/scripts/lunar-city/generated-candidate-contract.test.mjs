import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, realpath, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { test } from 'node:test'

import {
  readAndValidateGeneratedCandidateManifest,
  validateGeneratedCandidateManifest
} from './generated-candidate-contract.mjs'

function candidateFixture(overrides = {}) {
  const artifact = Buffer.from('lunar-city-generated-candidate')
  return {
    id: 'research-lab-trellis2-v001',
    targetModelId: 'research-lab',
    artifact: {
      path: 'research-lab/trellis2-v001.glb',
      format: 'glb',
      sha256: createHash('sha256').update(artifact).digest('hex')
    },
    source: {
      images: [
        {
          path: '/Users/mikedemott/Downloads/exec-b753ec5c-e213-413f-886a-c87cb14298f7.png',
          sha256: 'a'.repeat(64)
        }
      ],
      designReference: 'approved-moon-settlement'
    },
    generator: {
      id: 'trellis.2',
      repository: 'https://github.com/microsoft/trellis.2',
      model: 'TRELLIS.2-4B'
    },
    review: {
      artifactStatus: 'candidate',
      licenseStatus: 'pending',
      notes: 'Awaiting visual, topology, and license review.'
    },
    normalization: {
      anchor: 'manifest-transform',
      maxExtent: 26,
      preserveAspect: true
    },
    constraints: {
      hull: ['research-lab:footprint'],
      avoidance: ['roads', 'neighbor-buildings'],
      touch: ['terrain:world-surface']
    },
    _artifactBytes: artifact,
    ...overrides
  }
}

function manifestFixture(overrides = {}) {
  return {
    version: 1,
    reference: {
      design: 'approved-moon-settlement',
      images: ['/Users/mikedemott/Downloads/exec-b753ec5c-e213-413f-886a-c87cb14298f7.png']
    },
    candidates: [candidateFixture()],
    ...overrides
  }
}

test('accepts a complete generated candidate manifest', () => {
  const result = validateGeneratedCandidateManifest(manifestFixture())
  assert.deepEqual(result, { valid: true, errors: [] })
})

test('rejects candidates without an auditable generator and review state', () => {
  const candidate = candidateFixture({ generator: undefined, review: undefined })
  const result = validateGeneratedCandidateManifest(manifestFixture({ candidates: [candidate] }))
  assert.equal(result.valid, false)
  assert.match(result.errors.join('\n'), /candidates\[0\]\.generator is required/)
  assert.match(result.errors.join('\n'), /candidates\[0\]\.review is required/)
})

test('rejects absolute and traversal artifact paths before Blender can import them', () => {
  for (const path of ['/tmp/escape.glb', '../escape.glb', 'nested/../../escape.glb']) {
    const result = validateGeneratedCandidateManifest(
      manifestFixture({ candidates: [candidateFixture({ artifact: { ...candidateFixture().artifact, path } })] })
    )
    assert.equal(result.valid, false, path)
    assert.match(result.errors.join('\n'), /artifact\.path must be relative and stay inside the candidate directory/)
  }
})

test('verifies the staged artifact digest and keeps candidates outside shipped assets', async () => {
  const root = await mkdtemp(join(tmpdir(), 'lunar-city-candidates-'))
  try {
    const candidate = candidateFixture()
    const artifactPath = join(root, candidate.artifact.path)
    const manifestPath = join(root, 'generated-candidates.v1.json')
    await mkdir(join(root, 'research-lab'), { recursive: true })
    await writeFile(artifactPath, candidate._artifactBytes)
    const { _artifactBytes, ...serializableCandidate } = candidate
    await writeFile(manifestPath, JSON.stringify(manifestFixture({ candidates: [serializableCandidate] })))

    const result = await readAndValidateGeneratedCandidateManifest(manifestPath, root)
    assert.equal(result.valid, true)
    assert.equal(result.candidates[0].artifact.absolutePath, await realpath(artifactPath))
    assert.equal(result.candidates[0].artifact.sha256Verified, true)
    assert.equal(result.candidates[0].review.artifactStatus, 'candidate')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

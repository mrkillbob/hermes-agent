import * as fs from 'node:fs'
import * as path from 'node:path'

import { expect, test } from '@playwright/test'

import {
  assertGpuPackagedEligibility,
  buildPopulationContract,
  createLunarCityPopulationFixture,
  gpuPackagedLaunchOptions,
  GROUP_DISTRICTS,
  LEADER_FAMILIES
} from './lunar-city-fixtures'

for (const population of [25, 100, 250] as const) {
  test(`population ${population} has a deterministic immutable contract`, () => {
    const first = buildPopulationContract(population)
    const second = buildPopulationContract(population)

    expect(first).toEqual(second)
    expect(Object.isFrozen(first)).toBe(true)
    expect(first.population).toBe(population)
    expect(Object.values(first.entitiesByKind).reduce((sum, value) => sum + value, 0)).toBe(population)
    expect(first.activity.active + first.activity.idle + first.activity.unavailable).toBe(population)
    expect(first.lod.near + first.lod.mid + first.lod.far + first.lod.aggregate).toBe(population)
    expect(first.groups).toEqual(GROUP_DISTRICTS)
    expect(first.leaderFamilies).toEqual(LEADER_FAMILIES)
    expect(first.digest).toMatch(/^[a-f0-9]{64}$/u)
    expect(first.entities.some(row => row.kind === 'profile' && row.groups.length === 0)).toBe(true)
    expect(first.entities.some(row => row.kind === 'profile' && row.activity === 'unavailable')).toBe(true)
    expect(new Set(first.entities.flatMap(row => row.groups))).toEqual(new Set(GROUP_DISTRICTS.map(([name]) => name)))
    expect(sourceDistribution(first.entities)).toEqual(
      {
        25: { local: 11, 'remote-lab': 9, 'remote-archive': 5 },
        100: { local: 35, 'remote-lab': 34, 'remote-archive': 31 },
        250: { local: 85, 'remote-lab': 84, 'remote-archive': 81 }
      }[population]
    )
  })
}

test('fixture retains exact-source collisions and every required entity family', () => {
  const contract = buildPopulationContract(100)
  const duplicateRows = contract.entities.filter(row => row.displayId === 'shared-steward')

  expect(new Set(duplicateRows.map(row => row.connectionId))).toEqual(new Set(['local', 'remote-lab']))
  expect(new Set(duplicateRows.map(row => row.durableId)).size).toBe(1)
  expect(new Set(duplicateRows.map(row => row.exactKey)).size).toBe(2)
  expect(contract.entities.some(row => row.kind === 'profile' && row.groups.length === 0)).toBe(true)
  expect(contract.entities.some(row => row.kind === 'profile' && row.activity === 'unavailable')).toBe(true)
  expect(new Set(contract.entities.map(row => row.kind))).toEqual(
    new Set(['profile', 'session', 'subagent', 'task', 'worker'])
  )
})

test('seed uses isolated standard Hermes files, contains no secrets, and cleans up', () => {
  const fixture = createLunarCityPopulationFixture(25)

  expect(fixture.root.startsWith(path.resolve(process.env.TMPDIR || '/tmp'))).toBe(true)
  expect(fs.existsSync(path.join(fixture.hermesHome, 'config.yaml'))).toBe(true)
  expect(fs.existsSync(path.join(fixture.hermesHome, 'state.db'))).toBe(true)
  expect(fs.existsSync(path.join(fixture.hermesHome, 'kanban.db'))).toBe(true)
  expect(fs.existsSync(path.join(fixture.hermesHome, 'profiles'))).toBe(true)

  for (const [connectionId, home] of Object.entries(fixture.sourceHomes)) {
    const expectedProfiles = new Set(
      fixture.contract.entities
        .filter(row => row.kind === 'profile' && row.connectionId === connectionId)
        .map(row => row.profile)
    )

    expect(new Set(fs.readdirSync(path.join(home, 'profiles')))).toEqual(expectedProfiles)
  }

  const files = walkFiles(fixture.root)

  const text = files
    .filter(file => !file.endsWith('.db') && !file.endsWith('.db-shm') && !file.endsWith('.db-wal'))
    .map(file => fs.readFileSync(file, 'utf8'))
    .join('\n')

  expect(text).not.toMatch(/(?:api[_-]?key|token|password|secret)\s*[:=]\s*[^\s"']+/iu)
  expect(JSON.parse(fs.readFileSync(fixture.contractPath, 'utf8')).digest).toBe(fixture.contract.digest)

  fixture.cleanup()
  expect(fs.existsSync(fixture.root)).toBe(false)
})

test('GPU packaged path rejects dev, absent, dirty, fallback, and mismatched packages', () => {
  const clean = { commit: 'a'.repeat(40), dirty: false, source: 'local' }
  const binary = '/fixture/Hermes.app/Contents/MacOS/Hermes'

  expect(() =>
    assertGpuPackagedEligibility({ binaryExists: false, binaryPath: binary, headSha: clean.commit, stamp: clean })
  ).toThrow(/packaged binary is missing/iu)
  expect(() =>
    assertGpuPackagedEligibility({
      binaryExists: true,
      binaryPath: 'node_modules/.bin/electron',
      headSha: clean.commit,
      stamp: clean
    })
  ).toThrow(/dev Electron/iu)
  expect(() =>
    assertGpuPackagedEligibility({
      binaryExists: true,
      binaryPath: binary,
      headSha: clean.commit,
      stamp: { ...clean, dirty: true }
    })
  ).toThrow(/dirty/iu)
  expect(() =>
    assertGpuPackagedEligibility({
      binaryExists: true,
      binaryPath: binary,
      headSha: clean.commit,
      stamp: { ...clean, commit: '0'.repeat(40), source: 'fallback' }
    })
  ).toThrow(/fallback/iu)
  expect(() =>
    assertGpuPackagedEligibility({ binaryExists: true, binaryPath: binary, headSha: 'b'.repeat(40), stamp: clean })
  ).toThrow(/does not match/iu)

  expect(
    assertGpuPackagedEligibility({ binaryExists: true, binaryPath: binary, headSha: clean.commit, stamp: clean })
  ).toEqual({
    buildSha: clean.commit,
    executablePath: binary
  })
})

test('GPU packaged launch never disables the GPU', () => {
  const options = gpuPackagedLaunchOptions({
    executablePath: '/fixture/Hermes.app/Contents/MacOS/Hermes',
    env: { HERMES_HOME: '/tmp/fixture', OPENAI_API_KEY: 'must-not-cross' },
    userDataDir: '/tmp/fixture-user-data'
  })

  expect(options.args).toEqual(['--no-sandbox', '--user-data-dir=/tmp/fixture-user-data'])
  expect(options.args.join(' ')).not.toContain('--disable-gpu')
  expect(options.env.HERMES_HOME).toBe('/tmp/fixture')
  expect(options.env.OPENAI_API_KEY).toBeUndefined()
})

function walkFiles(root: string): string[] {
  return fs.readdirSync(root, { withFileTypes: true }).flatMap(entry => {
    const target = path.join(root, entry.name)

    return entry.isDirectory() ? walkFiles(target) : [target]
  })
}

function sourceDistribution(entities: readonly { connectionId: string }[]): Record<string, number> {
  return Object.fromEntries(
    ['local', 'remote-lab', 'remote-archive'].map(connectionId => [
      connectionId,
      entities.filter(entity => entity.connectionId === connectionId).length
    ])
  )
}

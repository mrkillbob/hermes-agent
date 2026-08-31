import * as fs from 'node:fs'
import * as path from 'node:path'

import { expect, test } from '@playwright/test'

import {
  assertGpuPackagedEligibility,
  buildPopulationContract,
  createLunarCityPopulationFixture,
  gpuPackagedLaunchOptions,
  GROUP_DISTRICTS,
  LEADER_FAMILIES,
  startPopulationGateways
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

    expectedProfiles.delete('default')
    expect(new Set(fs.readdirSync(path.join(home, 'profiles')))).toEqual(expectedProfiles)
    expect(fs.existsSync(path.join(home, 'profile.yaml'))).toBe(true)
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

for (const population of [25, 100, 250] as const) {
  test(`standard gateway routes expose the exact ${population}-entity source population`, async () => {
    test.slow()
    const fixture = createLunarCityPopulationFixture(population)
    let gateways: Awaited<ReturnType<typeof startPopulationGateways>> | undefined

    try {
      gateways = await startPopulationGateways(fixture)
      expect(JSON.parse(fs.readFileSync(path.join(fixture.userDataDir, 'connections.json'), 'utf8')).version).toBe(2)

      const observed = { profile: 0, session: 0, task: 0, worker: 0 }

      for (const source of gateways.sources) {
        const headers = { 'X-Hermes-Session-Token': source.token }

        const profiles = (await (await fetch(`${source.url}/api/profiles`, { headers })).json()) as {
          profiles: unknown[]
        }

        const sessions = (await (
          await fetch(`${source.url}/api/profiles/sessions?limit=500&profile=all`, { headers })
        ).json()) as { sessions: unknown[]; total: number }

        const board = (await (
          await fetch(`${source.url}/api/plugins/kanban/board?board=default`, { headers })
        ).json()) as { columns: Array<{ tasks: unknown[] }> }

        const workers = (await (
          await fetch(`${source.url}/api/plugins/kanban/workers/active?board=default`, { headers })
        ).json()) as { count: number; workers: Array<{ worker_pid: number; ended_at?: unknown }> }

        observed.profile += profiles.profiles.length
        observed.session += sessions.total
        observed.task += board.columns.reduce((sum, column) => sum + column.tasks.length, 0)
        observed.worker += workers.count
        expect(workers.workers.every(row => row.worker_pid === process.pid && row.ended_at == null)).toBe(true)
      }

      expect(observed).toEqual({
        profile: fixture.contract.entitiesByKind.profile,
        session: fixture.contract.entitiesByKind.session,
        task: fixture.contract.entitiesByKind.task,
        worker: fixture.contract.entitiesByKind.worker
      })

      const productionStorePath = '../src/store/subagents'

      const { $subagentsBySession, upsertSubagent } = (await import(productionStorePath)) as {
        $subagentsBySession: {
          get(): Record<string, unknown[]>
          set(value: Record<string, unknown[]>): void
        }
        upsertSubagent(
          sessionId: string,
          payload: Record<string, unknown>,
          createIfMissing: boolean,
          eventType: string
        ): void
      }

      $subagentsBySession.set({})

      const frames = await receiveStandardGatewayFrames(fixture.subagentFrames)

      for (const frame of frames) {
        upsertSubagent(frame.session_id, frame.payload, true, frame.type)
      }

      expect(Object.values($subagentsBySession.get()).flat()).toHaveLength(fixture.contract.entitiesByKind.subagent)
      expect(Object.values(observed).reduce((sum, value) => sum + value, 0) + fixture.subagentFrames.length).toBe(
        population
      )
    } finally {
      await gateways?.close()
      fixture.cleanup()
    }
  })
}

async function receiveStandardGatewayFrames<T extends { type: string }>(frames: readonly T[]): Promise<T[]> {
  interface SocketLike {
    close(): void
    once(event: string, listener: (...args: unknown[]) => void): void
    on(event: string, listener: (data: { toString(): string }) => void): void
    send(data: string): void
  }

  interface ServerLike {
    address(): string | { port: number }
    close(callback: () => void): void
    once(event: string, listener: (...args: unknown[]) => void): void
  }

  const wsModulePath = 'ws'

  const ws = (await import(wsModulePath)) as unknown as {
    default: new (url: string) => SocketLike
    WebSocketServer: new (options: { host: string; port: number }) => ServerLike
  }

  const WebSocket = ws.default
  const WebSocketServer = ws.WebSocketServer
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 })

  await new Promise<void>((resolve, reject) => {
    server.once('listening', () => resolve())
    server.once('error', reject)
  })

  const address = server.address()

  if (typeof address === 'string') {
    await new Promise<void>(resolve => server.close(() => resolve()))
    throw new Error('Standard gateway fixture did not bind a TCP port')
  }

  server.once('connection', (...args) => {
    const socket = args[0] as SocketLike

    for (const frame of frames) {
      socket.send(JSON.stringify(frame))
    }

    socket.close()
  })

  try {
    return await new Promise<T[]>((resolve, reject) => {
      const rows: T[] = []
      const socket = new WebSocket(`ws://127.0.0.1:${address.port}/api/ws`)

      socket.on('message', data => rows.push(JSON.parse(data.toString()) as T))
      socket.once('error', reject)
      socket.once('close', () => resolve(rows))
    })
  } finally {
    await new Promise<void>(resolve => server.close(() => resolve()))
  }
}

test('GPU packaged path rejects dev, absent, dirty, fallback, and mismatched packages', () => {
  const clean = {
    builtAt: '2026-08-31T12:00:00.000Z',
    commit: 'a'.repeat(40),
    dirty: false,
    schemaVersion: 1,
    source: 'local'
  }

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
    env: {
      DATABASE_URL: 'must-not-cross',
      GH_PAT: 'must-not-cross',
      HERMES_HOME: '/tmp/fixture',
      NPM_CONFIG__AUTH: 'must-not-cross',
      NODE_OPTIONS: '--inspect',
      OPENAI_API_KEY: 'must-not-cross'
    },
    userDataDir: '/tmp/fixture-user-data'
  })

  expect(options.args).toEqual(['--no-sandbox', '--user-data-dir=/tmp/fixture-user-data'])
  expect(options.args.join(' ')).not.toContain('--disable-gpu')
  expect(options.env.HERMES_HOME).toBe('/tmp/fixture')
  expect(options.env.OPENAI_API_KEY).toBeUndefined()
  expect(options.env.GH_PAT).toBeUndefined()
  expect(options.env.DATABASE_URL).toBeUndefined()
  expect(options.env.NPM_CONFIG__AUTH).toBeUndefined()
  expect(options.env.NODE_OPTIONS).toBeUndefined()
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

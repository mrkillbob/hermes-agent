import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

const REPO_ROOT = path.resolve(import.meta.dirname, '..', '..', '..')
const CONTRACT_VERSION = 'lunar-city-population-v1' as const
const EXACT_SHA = /^[a-f0-9]{40}$/u

export const GROUP_DISTRICTS = Object.freeze([
  ['Arts Studio', 'garden'],
  ['Acceptance & Release', 'review'],
  ['Archive and Acquisition', 'library'],
  ['CI Repair Triage', 'triage'],
  ['Community Intake', 'bus'],
  ['Content Studio', 'library'],
  ['Control Plane Incidents', 'council'],
  ['Core Runtime & UX Repairs', 'project'],
  ['Data & Performance Repairs', 'lab'],
  ['Editorial Desk', 'library'],
  ['Engineering Guild', 'project'],
  ['Federation Council', 'council'],
  ['Knowledge Commons', 'library'],
  ['Memory Stewardship', 'library'],
  ['Operations and Release', 'depot'],
  ['PR Merge Train', 'depot'],
  ['Research Lab', 'lab'],
  ['Research Review Board', 'review'],
  ['Upstream Hermes Maintenance', 'project']
] as const)

export const LEADER_FAMILIES = Object.freeze([
  'lunar-fox',
  'archive-owl',
  'triage-hare',
  'council-lynx',
  'depot-bear',
  'garden-moth'
] as const)

export type LunarCityPopulation = 25 | 100 | 250
type EntityKind = 'profile' | 'session' | 'subagent' | 'task' | 'worker'
type Activity = 'active' | 'idle' | 'unavailable'
type Lod = 'near' | 'mid' | 'far' | 'aggregate'

export interface PopulationEntity {
  activity: Activity
  connectionId: string
  displayId: string
  durableId: string
  exactKey: string
  groups: readonly string[]
  kind: EntityKind
  leaderFamily?: (typeof LEADER_FAMILIES)[number]
  lod: Lod
  profile: string
  sourceLabel: string
  title: string
}

export interface PopulationContract {
  activity: Readonly<Record<Activity, number>>
  digest: string
  entities: readonly PopulationEntity[]
  entitiesByKind: Readonly<Record<EntityKind, number>>
  groups: typeof GROUP_DISTRICTS
  leaderFamilies: typeof LEADER_FAMILIES
  lod: Readonly<Record<Lod, number>>
  population: LunarCityPopulation
  version: typeof CONTRACT_VERSION
}

interface ScenarioShape {
  activity: Record<Activity, number>
  kinds: Record<EntityKind, number>
  lod: Record<Lod, number>
}

const SHAPES: Readonly<Record<LunarCityPopulation, ScenarioShape>> = Object.freeze({
  25: {
    activity: { active: 8, idle: 16, unavailable: 1 },
    kinds: { profile: 19, session: 2, subagent: 1, task: 2, worker: 1 },
    lod: { near: 24, mid: 0, far: 0, aggregate: 1 }
  },
  100: {
    activity: { active: 25, idle: 70, unavailable: 5 },
    kinds: { profile: 70, session: 12, subagent: 6, task: 8, worker: 4 },
    lod: { near: 24, mid: 32, far: 28, aggregate: 16 }
  },
  250: {
    activity: { active: 60, idle: 180, unavailable: 10 },
    kinds: { profile: 180, session: 25, subagent: 15, task: 20, worker: 10 },
    lod: { near: 24, mid: 48, far: 82, aggregate: 96 }
  }
})

export function buildPopulationContract(population: LunarCityPopulation): PopulationContract {
  const shape = SHAPES[population]
  const entities: PopulationEntity[] = []
  const connections = ['local', 'remote-lab', 'remote-archive'] as const
  const labels = { local: 'This device', 'remote-lab': 'Hermes Revenue Lab', 'remote-archive': 'Hermes Desktop' }
  const profilesByConnection = new Map<string, string[]>()
  let ordinal = 0

  for (const kind of ['profile', 'session', 'subagent', 'task', 'worker'] as const) {
    for (let index = 0; index < shape.kinds[kind]; index += 1) {
      const collision = kind === 'profile' && index < 2
      const connectionId = collision ? connections[index]! : connections[(index + ordinal) % connections.length]!
      const durableId = collision ? 'shared-steward' : `${kind}-${String(index).padStart(3, '0')}`
      const owners = profilesByConnection.get(connectionId) ?? []
      const profile = kind === 'profile' ? durableId : owners[index % owners.length]!
      const groupIndex = kind === 'profile' && index < GROUP_DISTRICTS.length - 1 ? index : -1
      const groups = groupIndex >= 0 ? [GROUP_DISTRICTS[groupIndex]![0]] : []

      // Exercise multi-membership while retaining a true no-group profile.
      if (kind === 'profile' && index === 0) {
        groups.push(GROUP_DISTRICTS.at(-1)![0])
      }

      entities.push({
        activity: 'idle',
        connectionId,
        displayId: durableId,
        durableId,
        exactKey: `${kind}:${encodeURIComponent(connectionId)}:${encodeURIComponent(profile)}:${encodeURIComponent(durableId)}`,
        groups: Object.freeze(groups),
        kind,
        ...(kind === 'profile' ? { leaderFamily: LEADER_FAMILIES[index % LEADER_FAMILIES.length] } : {}),
        lod: 'aggregate',
        profile,
        sourceLabel: labels[connectionId],
        title: `${kind[0]!.toUpperCase()}${kind.slice(1)} ${String(index + 1).padStart(3, '0')}`
      })

      if (kind === 'profile') {
        profilesByConnection.set(connectionId, [...owners, profile])
      }

      ordinal += 1
    }
  }

  assignBuckets(entities, 'activity', shape.activity, ['active', 'idle', 'unavailable'])
  assignBuckets(entities, 'lod', shape.lod, ['near', 'mid', 'far', 'aggregate'])
  const unavailable = entities.find(row => row.activity === 'unavailable')
  const unavailableProfile = entities.findLast(row => row.kind === 'profile' && row.activity !== 'active')

  if (unavailable && unavailableProfile && unavailable.kind !== 'profile') {
    unavailable.activity = unavailableProfile.activity
    unavailableProfile.activity = 'unavailable'
  }

  const unsigned = {
    activity: shape.activity,
    entities,
    entitiesByKind: shape.kinds,
    groups: GROUP_DISTRICTS,
    leaderFamilies: LEADER_FAMILIES,
    lod: shape.lod,
    population,
    version: CONTRACT_VERSION
  }

  const digest = createHash('sha256').update(stableJson(unsigned)).digest('hex')

  return deepFreeze({ ...unsigned, digest })
}

function assignBuckets<K extends 'activity' | 'lod', V extends PopulationEntity[K]>(
  entities: PopulationEntity[],
  field: K,
  counts: Record<V, number>,
  order: readonly V[]
): void {
  let cursor = 0

  for (const value of order) {
    for (let count = 0; count < counts[value]; count += 1) {
      entities[cursor++]![field] = value
    }
  }
}

export interface LunarCityPopulationFixture {
  cleanup: () => void
  contract: PopulationContract
  contractPath: string
  hermesHome: string
  root: string
  sourceHomes: Readonly<Record<string, string>>
  userDataDir: string
}

export function createLunarCityPopulationFixture(population: LunarCityPopulation): LunarCityPopulationFixture {
  const contract = buildPopulationContract(population)
  const root = fs.mkdtempSync(path.join(os.tmpdir(), `hermes-lunar-city-${population}-`))

  const sourceHomes = {
    local: path.join(root, 'sources', 'local', 'hermes-home'),
    'remote-lab': path.join(root, 'sources', 'remote-lab', 'hermes-home'),
    'remote-archive': path.join(root, 'sources', 'remote-archive', 'hermes-home')
  }

  const userDataDir = path.join(root, 'electron-user-data')
  const contractPath = path.join(root, `${CONTRACT_VERSION}.json`)

  try {
    fs.mkdirSync(userDataDir, { recursive: true })

    for (const home of Object.values(sourceHomes)) {
      fs.mkdirSync(path.join(home, 'profiles'), { recursive: true })
      fs.writeFileSync(
        path.join(home, 'config.yaml'),
        '# Deterministic credential-free Lunar City E2E fixture\n',
        'utf8'
      )
    }

    for (const entity of contract.entities.filter(row => row.kind === 'profile')) {
      const home = sourceHomes[entity.connectionId as keyof typeof sourceHomes]
      const profileDir = path.join(home, 'profiles', entity.profile)
      fs.mkdirSync(profileDir, { recursive: true })
      fs.writeFileSync(
        path.join(profileDir, 'SOUL.md'),
        `# ${entity.title}\n\nDeterministic E2E fixture persona.\n`,
        'utf8'
      )
      fs.writeFileSync(path.join(profileDir, 'profile.yaml'), profileYaml(entity), 'utf8')
    }

    fs.writeFileSync(contractPath, `${JSON.stringify(contract, null, 2)}\n`, 'utf8')
    seedStandardStores(contractPath, sourceHomes)

    return {
      cleanup: () => fs.rmSync(root, { recursive: true, force: true }),
      contract,
      contractPath,
      hermesHome: sourceHomes.local,
      root,
      sourceHomes: deepFreeze({ ...sourceHomes }),
      userDataDir
    }
  } catch (error) {
    fs.rmSync(root, { recursive: true, force: true })
    throw error
  }
}

function profileYaml(entity: PopulationEntity): string {
  const groups = entity.groups.length
    ? `\n      groups:\n${entity.groups.map(group => `        - ${JSON.stringify(group)}`).join('\n')}\n      group: ${JSON.stringify(entity.groups[0])}`
    : ''

  return `display_name: ${JSON.stringify(entity.title)}
description: Deterministic Lunar City population fixture
ui_meta:
  hermes-bots:
    title: ${JSON.stringify(entity.title)}${groups}
_ui_meta_revisions:
  hermes-bots: 1
`
}

function seedStandardStores(contractPath: string, sourceHomes: Record<string, string>): void {
  const script = String.raw`
import json, os, pathlib, sqlite3, sys
from hermes_state import SessionDB
from hermes_cli import kanban_db

contract = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
homes = json.loads(sys.argv[2])
rows = contract["entities"]

for connection_id, home_raw in homes.items():
    home = pathlib.Path(home_raw)
    os.environ["HERMES_HOME"] = str(home)
    os.environ["HERMES_KANBAN_HOME"] = str(home)
    for row in rows:
        if row["connectionId"] != connection_id or row["kind"] not in ("session", "subagent"):
            continue
        profile_home = home / "profiles" / row["profile"]
        profile_home.mkdir(parents=True, exist_ok=True)
        db = SessionDB(db_path=profile_home / "state.db")
        try:
            source = "tool" if row["kind"] == "subagent" else "desktop"
            db.create_session(row["durableId"], source, profile_name=row["profile"], cwd=str(home))
            if row["activity"] != "active":
                db.end_session(row["durableId"], "completed")
        finally:
            db.close()

    if connection_id != "local":
        SessionDB(db_path=home / "state.db").close()
        continue

    db = SessionDB(db_path=home / "state.db")
    db.close()
    conn = kanban_db.connect(db_path=home / "kanban.db")
    try:
        now = 1788172800
        task_rows = [row for row in rows if row["kind"] == "task"]
        workers = [row for row in rows if row["kind"] == "worker"]
        for index, row in enumerate(task_rows):
            status = "running" if row["activity"] == "active" else "ready"
            conn.execute(
                "INSERT INTO tasks (id,title,assignee,status,created_at,workspace_kind,project_id,session_id) VALUES (?,?,?,?,?,?,?,?)",
                (row["durableId"], row["title"], row["profile"], status, now + index, "scratch", "fixture-project", f"session-{index:03d}"),
            )
        for index, row in enumerate(workers):
            task = task_rows[index % len(task_rows)]
            run_id = 1000 + index
            status = "running" if row["activity"] == "active" else "done"
            conn.execute(
                "INSERT INTO task_runs (id,task_id,profile,status,claim_lock,worker_pid,last_heartbeat_at,started_at,ended_at,outcome) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, task["durableId"], row["profile"], status, row["durableId"] if status == "running" else None, None, now, now, None if status == "running" else now + 1, None if status == "running" else "completed"),
            )
            if status == "running":
                conn.execute("UPDATE tasks SET status='running', current_run_id=?, claim_lock=?, last_heartbeat_at=? WHERE id=?", (run_id, row["durableId"], now, task["durableId"]))
        conn.commit()
    finally:
        conn.close()
`

  const python = path.join(REPO_ROOT, 'venv', 'bin', 'python')
  const fallbackPython = path.join(os.homedir(), '.hermes', 'hermes-agent', 'venv', 'bin', 'python')
  const executable = fs.existsSync(python) ? python : fallbackPython

  const result = spawnSync(executable, ['-c', script, contractPath, JSON.stringify(sourceHomes)], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    env: { ...process.env, PYTHONPATH: REPO_ROOT }
  })

  if (result.status !== 0) {
    throw new Error(`Could not seed standard Hermes stores:\n${result.stderr || result.stdout}`)
  }
}

export interface PackagedBuildStamp {
  commit: string
  dirty: boolean
  source: string
}

export interface GpuPackagedEligibilityInput {
  binaryExists: boolean
  binaryPath: string
  headSha: string
  stamp: PackagedBuildStamp
}

export function assertGpuPackagedEligibility(input: GpuPackagedEligibilityInput): {
  buildSha: string
  executablePath: string
} {
  if (!input.binaryExists) {
    throw new Error(`Packaged binary is missing: ${input.binaryPath}`)
  }

  if (/(?:node_modules[\\/]\.bin[\\/]electron|(^|[\\/])electron(?:\.exe)?$)/iu.test(input.binaryPath)) {
    throw new Error('Refusing dev Electron for packaged GPU evidence')
  }

  if (input.stamp.dirty) {
    throw new Error('Refusing dirty packaged build stamp')
  }

  if (input.stamp.source === 'fallback' || /^0{40}$/u.test(input.stamp.commit)) {
    throw new Error('Refusing fallback packaged build stamp')
  }

  if (!EXACT_SHA.test(input.stamp.commit) || !EXACT_SHA.test(input.headSha)) {
    throw new Error('Packaged build and HEAD must be exact 40-character SHAs')
  }

  if (input.stamp.commit !== input.headSha) {
    throw new Error('Packaged build SHA does not match checkout HEAD')
  }

  return { buildSha: input.stamp.commit, executablePath: input.binaryPath }
}

export function gpuPackagedLaunchOptions(input: {
  env: Record<string, string>
  executablePath: string
  userDataDir?: string
}): { args: string[]; env: Record<string, string>; executablePath: string } {
  const env = Object.fromEntries(
    Object.entries(input.env).filter(
      ([key]) => !/(?:_API_KEY|_TOKEN|_SECRET|_PASSWORD|_CREDENTIALS|_ACCESS_KEY|_PRIVATE_KEY)$/u.test(key)
    )
  )

  const args = ['--no-sandbox', ...(input.userDataDir ? [`--user-data-dir=${input.userDataDir}`] : [])]

  return { args, env, executablePath: input.executablePath }
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableJson).join(',')}]`
  }

  if (value && typeof value === 'object') {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, child]) => `${JSON.stringify(key)}:${stableJson(child)}`)
      .join(',')}}`
  }

  return JSON.stringify(value)
}

function deepFreeze<T>(value: T): T {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value)

    for (const child of Object.values(value)) {
      deepFreeze(child)
    }
  }

  return value
}

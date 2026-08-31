import { pluginRest, pluginSocket, type PluginSourceScope } from '@/api/plugins'

import { entityKey, projectCompoundKey } from '../identity'
import type {
  AuthorityState,
  LunarEntity,
  ProjectSlotManifestEntry,
  SourceHealth,
  Vec3,
  WorldBounds,
  WorldManifestV2
} from '../model'
import { mapObservedState } from '../state-map'

export interface KanbanRestOptions {
  method: 'GET'
  scope: PluginSourceScope
  timeoutMs?: number
}

export type KanbanRest = <T = unknown>(path: string, options: KanbanRestOptions) => Promise<T>
export interface KanbanSocketLifecycle {
  onReconnect?: () => void
}

export type KanbanSocket = (
  path: string,
  onMessage: (message: unknown) => void,
  lifecycle?: KanbanSocketLifecycle
) => () => void

export type KanbanSourceHealth = 'authoritative' | 'error' | 'malformed' | 'unauthorized' | 'unavailable'

export interface KanbanFrameResult {
  accepted: boolean
  dirtyTaskIds: readonly string[]
  needsReconcile: boolean
}

export interface ProjectCompoundInput {
  connectionId: string
  projectId: string
  taskCount: number
}

export interface ProjectCompoundPlacement extends ProjectCompoundInput {
  bounds?: WorldBounds
  key: string
  position?: Vec3
  slotId?: string
  unplaced: boolean
}

export interface KanbanReadResult {
  authoritative: boolean
  compounds: readonly ProjectCompoundPlacement[]
  details: ReadonlyMap<string, unknown>
  entities: readonly LunarEntity[]
  health: KanbanSourceHealth
  selectedBoard?: string
  sources: readonly SourceHealth[]
}

export interface KanbanCitySource {
  onFrame(frame: unknown): KanbanFrameResult
  read(): Promise<KanbanReadResult>
  start(onInvalidate: (result: KanbanFrameResult) => void): () => void
}

export interface KanbanCitySourceOptions {
  manifest?: Pick<WorldManifestV2, 'camera' | 'navigation' | 'projectSlots'>
  now?: () => number
  rest?: KanbanRest
  scope: PluginSourceScope
  selectedTaskId?: () => string | undefined
  socket?: KanbanSocket
  timeoutMs?: number
}

interface BoardRow {
  isCurrent: boolean
  projectId?: string
  slug: string
}

interface TaskRow {
  currentRunId?: string
  id: string
  projectId?: string
  status: string
}

interface WorkerRow {
  runId?: string
  status: string
  taskId: string
  workerId: string
}

const EMPTY_FRAME_RESULT: KanbanFrameResult = Object.freeze({
  accepted: false,
  dirtyTaskIds: Object.freeze([]),
  needsReconcile: false
})

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

function array(value: unknown): readonly unknown[] {
  return Array.isArray(value) ? value : []
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

function optionalId(value: unknown): string | undefined {
  if (typeof value === 'number' && Number.isSafeInteger(value)) {
    return String(value)
  }

  return optionalString(value)
}

function natural(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : undefined
}

function scopeCopy(scope: PluginSourceScope): PluginSourceScope {
  const connectionId = optionalString(scope.connectionId)
  const profile = optionalString(scope.profile)

  if (!connectionId) {
    throw new Error('createKanbanCitySource: scope.connectionId must not be empty')
  }

  if (!profile) {
    throw new Error('createKanbanCitySource: scope.profile must not be empty')
  }

  return Object.freeze({ connectionId, profile })
}

function sourceName(scope: PluginSourceScope): string {
  return `kanban:${encodeURIComponent(scope.connectionId)}:${encodeURIComponent(scope.profile)}`
}

function getOptions(scope: PluginSourceScope, timeoutMs: number | undefined): KanbanRestOptions {
  return { method: 'GET', scope, ...(timeoutMs === undefined ? {} : { timeoutMs }) }
}

function defaultRest<T>(path: string, options: KanbanRestOptions): Promise<T> {
  return pluginRest<T>('kanban', path, options)
}

function defaultSocket(scope: PluginSourceScope): KanbanSocket {
  return (path, onMessage, lifecycle) =>
    pluginSocket('kanban', path, onMessage, { onReconnect: lifecycle?.onReconnect, scope })
}

function pathWithBoard(path: string, board: string): string {
  return `${path}?board=${encodeURIComponent(board)}`
}

export function compoundKey(connectionId: string, projectId: string): string {
  return projectCompoundKey(connectionId, projectId)
}

export function allocateProjectCompounds(
  projects: readonly ProjectCompoundInput[],
  slots: readonly ProjectSlotManifestEntry[],
  retained: readonly ProjectCompoundPlacement[] = []
): readonly ProjectCompoundPlacement[] {
  const normalized = projects
    .filter(
      project => optionalString(project.connectionId) && optionalString(project.projectId) && project.taskCount > 0
    )
    .map(project => ({
      connectionId: project.connectionId.trim(),
      projectId: project.projectId.trim(),
      taskCount: Math.max(0, Math.floor(project.taskCount))
    }))
    .sort(
      (left, right) =>
        left.connectionId.localeCompare(right.connectionId) || left.projectId.localeCompare(right.projectId)
    )

  const slotsById = new Map(slots.map(slot => [slot.id, slot]))
  const retainedByKey = new Map(retained.map(placement => [placement.key, placement]))
  const assignedSlots = new Map<string, ProjectSlotManifestEntry>()
  const usedSlotIds = new Set<string>()

  for (const project of normalized) {
    const key = compoundKey(project.connectionId, project.projectId)
    const prior = retainedByKey.get(key)
    const slot = prior?.slotId ? slotsById.get(prior.slotId) : undefined

    if (slot && !usedSlotIds.has(slot.id)) {
      assignedSlots.set(key, slot)
      usedSlotIds.add(slot.id)
    }
  }

  for (const project of normalized) {
    const key = compoundKey(project.connectionId, project.projectId)

    if (assignedSlots.has(key)) {
      continue
    }

    const slot = slots.find(candidate => !usedSlotIds.has(candidate.id))

    if (slot) {
      assignedSlots.set(key, slot)
      usedSlotIds.add(slot.id)
    }
  }

  return normalized.map(project => {
    const key = compoundKey(project.connectionId, project.projectId)
    const slot = assignedSlots.get(key)

    return {
      ...project,
      ...(slot
        ? {
            bounds: slot.bounds,
            position: slot.position,
            slotId: slot.id,
            unplaced: false
          }
        : { unplaced: true }),
      key
    }
  })
}

function containsBounds(outer: WorldBounds, inner: WorldBounds): boolean {
  return (
    inner.min.x >= outer.min.x &&
    inner.max.x <= outer.max.x &&
    inner.min.y >= outer.min.y &&
    inner.max.y <= outer.max.y &&
    inner.min.z >= outer.min.z &&
    inner.max.z <= outer.max.z
  )
}

function hasVolume(bounds: WorldBounds): boolean {
  return bounds.max.x > bounds.min.x && bounds.max.y > bounds.min.y && bounds.max.z > bounds.min.z
}

function overlaps(left: WorldBounds, right: WorldBounds): boolean {
  return (
    left.min.x < right.max.x &&
    left.max.x > right.min.x &&
    left.min.y < right.max.y &&
    left.max.y > right.min.y &&
    left.min.z < right.max.z &&
    left.max.z > right.min.z
  )
}

function pointKey(point: Vec3): string {
  return `${point.x},${point.y},${point.z}`
}

function linkKey(link: { bidirectional: boolean; from: Vec3; to: Vec3 }): string {
  return `${pointKey(link.from)}>${pointKey(link.to)}:${link.bidirectional ? '1' : '0'}`
}

export function projectSlotContractIssues(
  manifest: Pick<WorldManifestV2, 'camera' | 'navigation' | 'projectSlots'>,
  protectedBounds: readonly { bounds: WorldBounds; id: string }[] = []
): readonly string[] {
  const issues: string[] = []
  const navigationLinks = new Set(manifest.navigation.links.map(linkKey))

  for (const [index, slot] of manifest.projectSlots.entries()) {
    if (!hasVolume(slot.bounds)) {
      issues.push(`projectSlots[${slot.id || index}] has no volume`)
    }

    if (!containsBounds(manifest.camera.bounds, slot.bounds)) {
      issues.push(`projectSlots[${slot.id || index}] is outside camera world bounds`)
    }

    if (!navigationLinks.has(linkKey(slot.navigationLink))) {
      issues.push(`projectSlots[${slot.id || index}] navigationLink is missing from navigation.links`)
    }

    for (const other of manifest.projectSlots.slice(index + 1)) {
      if (overlaps(slot.bounds, other.bounds)) {
        issues.push(`projectSlots[${slot.id}] overlaps projectSlots[${other.id}]`)
      }
    }

    for (const protectedEntry of protectedBounds) {
      if (overlaps(slot.bounds, protectedEntry.bounds)) {
        issues.push(`projectSlots[${slot.id}] overlaps protected geometry ${protectedEntry.id}`)
      }
    }
  }

  return issues
}

function boardRows(response: unknown): readonly BoardRow[] {
  return array(record(response).boards).flatMap(item => {
    const row = record(item)
    const slug = optionalString(row.slug)

    if (!slug) {
      return []
    }

    return [
      {
        isCurrent: row.is_current === true,
        ...(optionalString(row.project_id) ? { projectId: optionalString(row.project_id) } : {}),
        slug
      }
    ]
  })
}

function selectedBoard(response: unknown): BoardRow | undefined {
  const rows = boardRows(response)
  const current = optionalString(record(response).current)

  return rows.find(row => row.slug === current) ?? rows.find(row => row.isCurrent) ?? rows[0]
}

function taskRows(response: unknown, board: BoardRow): readonly TaskRow[] {
  return array(record(response).columns).flatMap(column => {
    const columnRecord = record(column)
    const columnStatus = optionalString(columnRecord.name) ?? 'unknown'

    return array(columnRecord.tasks).flatMap(item => {
      const row = record(item)
      const id = optionalString(row.id)

      if (!id) {
        return []
      }

      return [
        {
          currentRunId: optionalId(row.current_run_id ?? row.run_id),
          id,
          projectId: optionalString(row.project_id) ?? board.projectId,
          status: optionalString(row.status) ?? columnStatus
        }
      ]
    })
  })
}

function workerId(row: Record<string, unknown>): string | undefined {
  const pid = optionalId(row.worker_pid)

  if (pid) {
    return `pid:${pid}`
  }

  const claim = optionalString(row.claim_lock)

  return claim ? `claim:${claim}` : undefined
}

function workerRows(response: unknown): readonly WorkerRow[] {
  return array(record(response).workers).flatMap(item => {
    const row = record(item)
    const taskId = optionalString(row.task_id)
    const worker = workerId(row)

    if (!taskId || !worker) {
      return []
    }

    return [
      {
        runId: optionalId(row.run_id),
        status: optionalString(row.task_status) ?? optionalString(row.status) ?? 'unknown',
        taskId,
        workerId: worker
      }
    ]
  })
}

function projectInputs(scope: PluginSourceScope, tasks: readonly TaskRow[]): readonly ProjectCompoundInput[] {
  const counts = new Map<string, number>()

  for (const task of tasks) {
    if (!task.projectId) {
      continue
    }

    counts.set(task.projectId, (counts.get(task.projectId) ?? 0) + 1)
  }

  return [...counts].map(([projectId, taskCount]) => ({ connectionId: scope.connectionId, projectId, taskCount }))
}

function entityPosition(
  placements: ReadonlyMap<string, ProjectCompoundPlacement>,
  connectionId: string,
  projectId: string | undefined
): Vec3 | undefined {
  if (!projectId) {
    return undefined
  }

  return placements.get(compoundKey(connectionId, projectId))?.position
}

function freezeDetails(details: Map<string, unknown>): ReadonlyMap<string, unknown> {
  return Object.freeze({
    [Symbol.iterator]: () => details[Symbol.iterator](),
    entries: () => details.entries(),
    forEach: (callback: (value: unknown, key: string, map: ReadonlyMap<string, unknown>) => void) =>
      details.forEach((value, key) => callback(value, key, details)),
    get: (key: string) => details.get(key),
    get size() {
      return details.size
    },
    has: (key: string) => details.has(key),
    keys: () => details.keys(),
    values: () => details.values()
  } satisfies ReadonlyMap<string, unknown>) as ReadonlyMap<string, unknown>
}

function sourceState(health: KanbanSourceHealth): AuthorityState {
  return health === 'authoritative' ? 'authoritative' : 'unknown'
}

function result(
  scope: PluginSourceScope,
  observedAt: number,
  health: KanbanSourceHealth,
  fields: Omit<Partial<KanbanReadResult>, 'health' | 'sources'> & { error?: string } = {}
): KanbanReadResult {
  const sources = [
    {
      authority: sourceState(health),
      ...(fields.error ? { error: fields.error } : {}),
      observedAt,
      source: sourceName(scope)
    }
  ] satisfies readonly SourceHealth[]

  return {
    authoritative: health === 'authoritative',
    compounds: fields.compounds ?? [],
    details: fields.details ?? freezeDetails(new Map()),
    entities: fields.entities ?? [],
    health,
    ...(fields.selectedBoard ? { selectedBoard: fields.selectedBoard } : {}),
    sources
  }
}

function errorStatus(error: unknown): number | undefined {
  const item = record(error)

  return natural(item.status) ?? natural(item.statusCode) ?? natural(record(item.response).status)
}

function failure(scope: PluginSourceScope, observedAt: number, error: unknown): KanbanReadResult {
  const status = errorStatus(error)

  if (status === 404) {
    return result(scope, observedAt, 'unavailable', { error: 'Kanban plugin unavailable' })
  }

  if (status === 401 || status === 403) {
    return result(scope, observedAt, 'unauthorized', { error: 'Kanban plugin unauthorized' })
  }

  const message =
    error instanceof Error && /unexpected request|malformed|must be|invalid/iu.test(error.message)
      ? 'Kanban response malformed'
      : 'Kanban source read failed'

  return result(scope, observedAt, message === 'Kanban response malformed' ? 'malformed' : 'error', { error: message })
}

function normalizeEntities(
  scope: PluginSourceScope,
  board: BoardRow,
  tasks: readonly TaskRow[],
  workers: readonly WorkerRow[],
  placements: readonly ProjectCompoundPlacement[],
  observedAt: number
): readonly LunarEntity[] {
  const placementsByProject = new Map(placements.map(placement => [placement.key, placement]))
  const tasksByRun = new Map<string, { projectId?: string; status: string }>()

  for (const task of tasks) {
    if (task.currentRunId) {
      tasksByRun.set(`${task.id}\u0000${task.currentRunId}`, { projectId: task.projectId, status: task.status })
    }
  }

  const taskEntities = tasks.map(task => {
    const identity = {
      board: board.slug,
      connectionId: scope.connectionId,
      kind: 'kanban' as const,
      profile: scope.profile,
      ...(task.currentRunId ? { runId: task.currentRunId } : {}),
      taskId: task.id
    }

    const state = mapObservedState({ fresh: true, source: 'kanban', status: task.status })
    const position = entityPosition(placementsByProject, scope.connectionId, task.projectId)

    return {
      ...state,
      identity,
      key: entityKey(identity),
      observedAt,
      ...(position ? { position } : {}),
      ...(task.projectId ? { projectId: task.projectId } : {})
    } satisfies LunarEntity
  })

  const workerEntities = workers.map(worker => {
    const matched = worker.runId ? tasksByRun.get(`${worker.taskId}\u0000${worker.runId}`) : undefined

    const identity = {
      board: board.slug,
      connectionId: scope.connectionId,
      kind: 'kanban' as const,
      profile: scope.profile,
      ...(worker.runId ? { runId: worker.runId } : {}),
      taskId: worker.taskId,
      workerId: worker.workerId
    }

    const state = mapObservedState({
      authority: matched ? undefined : 'partial',
      fresh: true,
      source: 'kanban',
      status: matched?.status ?? worker.status
    })

    const position = entityPosition(placementsByProject, scope.connectionId, matched?.projectId)

    return {
      ...state,
      identity,
      key: entityKey(identity),
      observedAt,
      ...(position ? { position } : {}),
      ...(matched?.projectId ? { projectId: matched.projectId } : {})
    } satisfies LunarEntity
  })

  return [...taskEntities, ...workerEntities].sort((left, right) => left.key.localeCompare(right.key))
}

function eventPath(board: string, cursor: number): string {
  return `${pathWithBoard('/events', board)}&since=${encodeURIComponent(String(cursor))}`
}

export function createKanbanCitySource(options: KanbanCitySourceOptions): KanbanCitySource {
  const scope = scopeCopy(options.scope)
  const now = options.now ?? Date.now
  const rest = options.rest ?? defaultRest
  const socket = options.socket ?? defaultSocket(scope)
  const slots = options.manifest?.projectSlots ?? []
  const cursorByBoard = new Map<string, number>()
  let disposed = false
  let selected: string | undefined
  let socketPath: string | undefined
  let socketDispose: (() => void) | undefined
  let invalidate: ((result: KanbanFrameResult) => void) | undefined
  let inFlight: Promise<KanbanReadResult> | undefined
  let retainedCompounds: readonly ProjectCompoundPlacement[] = []
  let selectedDetail: { board: string; dirty: boolean; taskId: string; value: unknown } | undefined

  const closeSocket = (): void => {
    socketDispose?.()
    socketDispose = undefined
    socketPath = undefined
  }

  const openSocket = (): void => {
    if (disposed || !invalidate || !selected) {
      return
    }

    const nextPath = eventPath(selected, cursorByBoard.get(selected) ?? 0)

    if (nextPath === socketPath) {
      return
    }

    closeSocket()
    socketPath = nextPath
    const socketBoard = selected
    socketDispose = socket(
      nextPath,
      frame => {
        if (disposed || selected !== socketBoard || socketPath !== nextPath) {
          return
        }

        const frameResult = source.onFrame(frame)

        if (frameResult.accepted && (frameResult.needsReconcile || frameResult.dirtyTaskIds.length > 0)) {
          invalidate?.(frameResult)
        }
      },
      {
        onReconnect: () => {
          if (disposed || selected !== socketBoard || socketPath !== nextPath) {
            return
          }

          if (selectedDetail?.board === socketBoard) {
            selectedDetail = { ...selectedDetail, dirty: true }
          }

          invalidate?.({ accepted: true, dirtyTaskIds: [], needsReconcile: true })
        }
      }
    )
  }

  const readOnce = async (): Promise<KanbanReadResult> => {
    const observedAt = now()

    const disposedResult = (): KanbanReadResult =>
      result(scope, observedAt, 'unavailable', { error: 'Kanban source disposed' })

    if (disposed) {
      return disposedResult()
    }

    try {
      const boards = await rest('/boards', getOptions(scope, options.timeoutMs))

      if (disposed) {
        return disposedResult()
      }

      if (!Array.isArray(record(boards).boards)) {
        throw new Error('malformed Kanban board list')
      }

      const board = selectedBoard(boards)

      if (!board) {
        throw new Error('malformed Kanban board list')
      }

      const boardPayload = await rest(pathWithBoard('/board', board.slug), getOptions(scope, options.timeoutMs))

      if (disposed) {
        return disposedResult()
      }

      const workersPayload = await rest(
        pathWithBoard('/workers/active', board.slug),
        getOptions(scope, options.timeoutMs)
      )

      if (disposed) {
        return disposedResult()
      }

      if (
        !Array.isArray(record(boardPayload).columns) ||
        natural(record(boardPayload).latest_event_id) === undefined ||
        !Array.isArray(record(workersPayload).workers)
      ) {
        throw new Error('malformed Kanban board response')
      }

      const tasks = taskRows(boardPayload, board)
      const workers = workerRows(workersPayload)
      const compounds = allocateProjectCompounds(projectInputs(scope, tasks), slots, retainedCompounds)
      const entities = normalizeEntities(scope, board, tasks, workers, compounds, observedAt)
      const latestEventId = natural(record(boardPayload).latest_event_id) ?? 0
      const details = new Map<string, unknown>()
      const selectedTask = options.selectedTaskId?.()?.trim()

      if (selected && selected !== board.slug) {
        cursorByBoard.delete(selected)
        selectedDetail = undefined
        closeSocket()
      }

      selected = board.slug
      cursorByBoard.set(board.slug, latestEventId)

      if (selectedTask && tasks.some(task => task.id === selectedTask)) {
        if (
          !selectedDetail ||
          selectedDetail.board !== board.slug ||
          selectedDetail.taskId !== selectedTask ||
          selectedDetail.dirty
        ) {
          selectedDetail = {
            board: board.slug,
            dirty: false,
            taskId: selectedTask,
            value: await rest(
              pathWithBoard(`/tasks/${encodeURIComponent(selectedTask)}`, board.slug),
              getOptions(scope, options.timeoutMs)
            )
          }

          if (disposed) {
            return disposedResult()
          }
        }

        details.set(selectedTask, selectedDetail.value)
      }

      openSocket()
      retainedCompounds = compounds

      return result(scope, observedAt, 'authoritative', {
        compounds,
        details: freezeDetails(details),
        entities,
        selectedBoard: board.slug
      })
    } catch (error) {
      return failure(scope, observedAt, error)
    }
  }

  const read = (): Promise<KanbanReadResult> => {
    if (inFlight) {
      return inFlight
    }

    const pending = readOnce()
    inFlight = pending
    void pending.finally(() => {
      if (inFlight === pending) {
        inFlight = undefined
      }
    })

    return pending
  }

  const source: KanbanCitySource = {
    onFrame(frame): KanbanFrameResult {
      if (disposed || !selected) {
        return EMPTY_FRAME_RESULT
      }

      const payload = record(frame)
      const events = array(payload.events)
      const dirty = new Set<string>()
      let prior = cursorByBoard.get(selected) ?? 0
      let accepted = false
      let needsReconcile = false

      for (const item of events) {
        const event = record(item)
        const id = natural(event.id)

        if (id === undefined) {
          accepted = true
          needsReconcile = true

          continue
        }

        if (id <= prior) {
          continue
        }

        if (id > prior + 1) {
          needsReconcile = true
        }

        prior = id
        accepted = true

        const taskId = optionalString(event.task_id)

        if (taskId) {
          dirty.add(taskId)

          if (selectedDetail?.board === selected && selectedDetail.taskId === taskId) {
            selectedDetail = { ...selectedDetail, dirty: true }
          }
        }
      }

      const cursor = natural(payload.cursor)

      if (cursor !== undefined) {
        if (cursor < prior) {
          needsReconcile = true
        } else if (cursor > prior) {
          if (cursor > prior + 1) {
            needsReconcile = true
          }

          prior = cursor
          accepted = true
        }
      }

      if (!accepted && !needsReconcile) {
        return EMPTY_FRAME_RESULT
      }

      cursorByBoard.set(selected, prior)

      return Object.freeze({
        accepted,
        dirtyTaskIds: Object.freeze([...dirty].sort((left, right) => left.localeCompare(right))),
        needsReconcile
      })
    },
    read,
    start(onInvalidate) {
      if (disposed) {
        return () => undefined
      }

      invalidate = onInvalidate
      openSocket()

      return () => {
        disposed = true
        invalidate = undefined
        closeSocket()
      }
    }
  }

  return source
}

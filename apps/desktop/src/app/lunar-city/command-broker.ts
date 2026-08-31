import { entityKey as canonicalEntityKey } from './identity'
import type { LeaderOwner } from './leader-sessions'
import type { AuthorityState, EntityIdentity, EntityKey, LunarCitySnapshot, LunarEntity, SourceHealth } from './model'

export type CommandVerification = 'verified' | 'rejected' | 'timed_out' | 'verification_required'

export type CommandOperation =
  | 'open-session'
  | 'inspect-evidence'
  | 'send-guidance'
  | 'interrupt-session'
  | 'interrupt-subagent'
  | 'terminate-run'
  | 'retry-task'
  | 'reclaim-task'
  | 'reassign-task'
  | 'dispatch-task'
  | 'change-task-state'

export type CommandEvidenceKind = 'attachments' | 'comments' | 'diagnostics' | 'events' | 'log' | 'run' | 'task'

export type CommandIntent =
  | { kind: 'open-session'; entityKey: EntityKey }
  | { kind: 'inspect-evidence'; entityKey: EntityKey; evidence: CommandEvidenceKind }
  | { kind: 'send-guidance'; entityKey: EntityKey; text: string }
  | { kind: 'interrupt-session'; entityKey: EntityKey }
  | { kind: 'interrupt-subagent'; entityKey: EntityKey }
  | { kind: 'terminate-run'; entityKey: EntityKey }
  | { kind: 'retry-task'; entityKey: EntityKey }
  | { kind: 'reclaim-task'; entityKey: EntityKey }
  | { kind: 'reassign-task'; entityKey: EntityKey; assignee: string }
  | { kind: 'dispatch-task'; entityKey: EntityKey }
  | { kind: 'change-task-state'; entityKey: EntityKey; state: string }

export interface ReadbackPlan {
  kind: 'session' | 'subagent' | 'kanban-task' | 'kanban-run'
  id: string
  expectedState?: string
}

export interface CommandPlanContext {
  canonicalProjectId?: string
  currentState: string
  repositoryId?: string
  source: SourceHealth
}

export interface CommandPlan {
  confirmation: boolean
  consequence: string
  context: CommandPlanContext
  entityKey: EntityKey
  identity: EntityIdentity
  method: string
  operation: CommandOperation
  owner: LeaderOwner
  params: Record<string, unknown>
  plannedAt: number
  readback: ReadbackPlan
}

export interface CommandReceipt {
  error?: string
  identity: EntityIdentity
  readback?: CommandReadback | null
  response?: unknown
  verification: CommandVerification
}

export interface CommandTargetState {
  availableOperations: readonly CommandOperation[]
  canonicalProjectId?: string
  currentState: string
  entity: LunarEntity
  ownerCandidates: readonly LeaderOwner[]
  readbackCapabilities: readonly ReadbackPlan['kind'][]
  repositoryId?: string
  source: SourceHealth
}

export interface CommandPlanningSnapshot {
  city: LunarCitySnapshot
  targets: ReadonlyMap<EntityKey, CommandTargetState>
}

export interface CommandReadback {
  authority: AuthorityState
  identity: EntityIdentity
  observedAt: number
  operation: CommandOperation
  outcome: 'rejected' | 'unknown' | 'verified'
  owner: LeaderOwner
  state?: string
}

export interface CommandExecutor {
  readback(plan: CommandPlan): Promise<CommandReadback | null>
  send(plan: CommandPlan): Promise<unknown>
}

export interface CommandExecutors {
  kanbanRun: CommandExecutor
  kanbanTask: CommandExecutor
  session: CommandExecutor
  subagent: CommandExecutor
}

export interface ExecuteCommandOptions {
  confirmed?: boolean
}

export class CommandRejectedError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'CommandRejectedError'
  }
}

export class CommandTimeoutError extends Error {
  constructor(
    message: string,
    readonly possiblyApplied = true
  ) {
    super(message)
    this.name = 'CommandTimeoutError'
  }
}

function fail(code: string, detail: string): never {
  throw new Error(`${code}: ${detail}`)
}

function required(value: string | undefined, code: string, label: string): string {
  const normalized = value?.trim() ?? ''

  if (!normalized) {
    fail(code, `${label} is required`)
  }

  return normalized
}

function exactOwner(identity: EntityIdentity, candidates: readonly LeaderOwner[]): LeaderOwner {
  if (candidates.length !== 1) {
    fail('owner-is-ambiguous', `expected one owner route and found ${candidates.length}`)
  }

  const candidate = candidates[0]
  const connectionId = required(candidate.connectionId, 'owner-is-ambiguous', 'connectionId')
  const profile = required(candidate.profile, 'owner-is-ambiguous', 'profile')

  if (connectionId !== identity.connectionId || profile !== identity.profile) {
    fail('owner-is-ambiguous', 'the resolved route does not own the complete typed identity')
  }

  return { connectionId, profile }
}

function validateIdentity(identity: EntityIdentity): void {
  required(identity.connectionId, 'identity-incomplete', 'connectionId')
  required(identity.profile, 'identity-incomplete', 'profile')

  if (identity.kind === 'session' || identity.kind === 'subagent') {
    required(identity.sessionId, 'identity-incomplete', 'sessionId')
  }

  if (identity.kind === 'subagent') {
    required(identity.subagentId, 'identity-incomplete', 'subagentId')
  }

  if (identity.kind === 'kanban') {
    required(identity.board, 'identity-incomplete', 'board')
    required(identity.taskId, 'identity-incomplete', 'taskId')
  }
}

function sameOwner(left: LeaderOwner, right: LeaderOwner): boolean {
  return left.connectionId === right.connectionId && left.profile === right.profile
}

function sameIdentity(left: EntityIdentity, right: EntityIdentity): boolean {
  if (left.kind !== right.kind || left.connectionId !== right.connectionId || left.profile !== right.profile) {
    return false
  }

  if (left.kind === 'profile' && right.kind === 'profile') {
    return true
  }

  if (left.kind === 'session' && right.kind === 'session') {
    return left.sessionId === right.sessionId
  }

  if (left.kind === 'subagent' && right.kind === 'subagent') {
    return left.sessionId === right.sessionId && left.subagentId === right.subagentId
  }

  if (left.kind === 'kanban' && right.kind === 'kanban') {
    return (
      left.board === right.board &&
      left.taskId === right.taskId &&
      left.runId === right.runId &&
      left.workerId === right.workerId
    )
  }

  return false
}

function sourceFor(target: CommandTargetState, city: LunarCitySnapshot): SourceHealth {
  const sources = city.sources.filter(source => source.source === target.source.source)

  if (sources.length !== 1) {
    fail('source-is-ambiguous', `expected one source named ${target.source.source}`)
  }

  const source = sources[0]

  if (
    source.authority !== target.source.authority ||
    source.observedAt !== target.source.observedAt ||
    source.error !== target.source.error
  ) {
    fail('source-is-ambiguous', 'target source metadata does not match the immutable city snapshot')
  }

  return { ...source }
}

function readbackFor(identity: EntityIdentity, operation: CommandOperation, expectedState?: string): ReadbackPlan {
  if (operation === 'terminate-run') {
    if (identity.kind !== 'kanban') {
      fail('unsupported-command', 'run termination requires a Kanban identity')
    }

    return { expectedState, id: required(identity.runId, 'identity-incomplete', 'runId'), kind: 'kanban-run' }
  }

  if (
    operation === 'retry-task' ||
    operation === 'reclaim-task' ||
    operation === 'reassign-task' ||
    operation === 'dispatch-task' ||
    operation === 'change-task-state'
  ) {
    if (identity.kind !== 'kanban') {
      fail('unsupported-command', `${operation} requires a Kanban identity`)
    }

    return { expectedState, id: identity.taskId, kind: 'kanban-task' }
  }

  if (identity.kind === 'subagent') {
    return { expectedState, id: identity.subagentId, kind: 'subagent' }
  }

  if (identity.kind === 'session') {
    return { expectedState, id: identity.sessionId, kind: 'session' }
  }

  fail('unsupported-command', `${operation} requires a session, subagent, task, or run identity`)
}

function identityParams(identity: EntityIdentity): Record<string, unknown> {
  if (identity.kind === 'session') {
    return { session_id: identity.sessionId }
  }

  if (identity.kind === 'subagent') {
    return { session_id: identity.sessionId, subagent_id: identity.subagentId }
  }

  if (identity.kind === 'kanban') {
    return {
      board: identity.board,
      task_id: identity.taskId,
      ...(identity.runId ? { run_id: identity.runId } : {}),
      ...(identity.workerId ? { worker_id: identity.workerId } : {})
    }
  }

  return {}
}

function operationDetails(
  intent: CommandIntent,
  identity: EntityIdentity,
  currentState: string
): { consequence: string; expectedState?: string; method: string; params: Record<string, unknown> } {
  const params = identityParams(identity)

  switch (intent.kind) {
    case 'open-session':
      return {
        consequence: 'Open the exact owning standard session without changing execution.',
        method: 'session.open',
        params
      }

    case 'inspect-evidence':
      return {
        consequence: `Inspect ${intent.evidence} from its labeled owning source without changing execution.`,
        method: 'evidence.inspect',
        params: { ...params, evidence: intent.evidence }
      }
    case 'send-guidance': {
      const text = required(intent.text, 'identity-incomplete', 'guidance text')

      const method =
        identity.kind === 'subagent' ? 'subagent.steer' : currentState === 'running' ? 'session.steer' : 'prompt.submit'

      return {
        consequence: 'Send ordinary guidance to the exact owned conversation; it does not assert task completion.',
        method,
        params: { ...params, text }
      }
    }

    case 'interrupt-session':
      return {
        consequence: 'Interrupt the active turn in the exact owning session; unfinished work may stop.',
        method: 'session.interrupt',
        params
      }

    case 'interrupt-subagent':
      return {
        consequence: 'Interrupt the exact child worker; unfinished delegated work may stop.',
        method: 'subagent.interrupt',
        params
      }

    case 'terminate-run':
      return {
        consequence: 'Terminate the exact Kanban run; the worker may stop before producing acceptance evidence.',
        method: 'kanban.run.terminate',
        params
      }

    case 'retry-task':
      return {
        consequence:
          'Request one fresh retry for the exact task after confirmation; no retry occurs on an ambiguous result.',
        method: 'kanban.task.retry',
        params
      }

    case 'reclaim-task':
      return {
        consequence: 'Reclaim the exact task from its current worker and return it to dispatcher control.',
        method: 'kanban.task.reclaim',
        params
      }
    case 'reassign-task': {
      const assignee = required(intent.assignee, 'identity-incomplete', 'assignee')

      return {
        consequence: `Reassign the exact task to ${assignee}; its current worker may be replaced.`,
        method: 'kanban.task.reassign',
        params: { ...params, assignee }
      }
    }

    case 'dispatch-task':
      return {
        consequence: 'Dispatch the exact task once; an ambiguous acknowledgement will not be retried.',
        method: 'kanban.task.dispatch',
        params
      }
    case 'change-task-state': {
      const state = required(intent.state, 'identity-incomplete', 'task state')

      return {
        consequence: `Change the exact task state from ${currentState} to ${state}.`,
        expectedState: state,
        method: 'kanban.task.patch',
        params: { ...params, status: state }
      }
    }
  }
}

function mutationRequiresAuthoritativeState(operation: CommandOperation): boolean {
  return operation !== 'open-session' && operation !== 'inspect-evidence'
}

function confirmationFor(operation: CommandOperation): boolean {
  return operation !== 'open-session' && operation !== 'inspect-evidence' && operation !== 'send-guidance'
}

const ALLOWED_METHODS: Readonly<Record<CommandOperation, readonly string[]>> = {
  'change-task-state': ['kanban.task.patch'],
  'dispatch-task': ['kanban.task.dispatch'],
  'inspect-evidence': ['evidence.inspect'],
  'interrupt-session': ['session.interrupt'],
  'interrupt-subagent': ['subagent.interrupt'],
  'open-session': ['session.open'],
  'reassign-task': ['kanban.task.reassign'],
  'reclaim-task': ['kanban.task.reclaim'],
  'retry-task': ['kanban.task.retry'],
  'send-guidance': ['prompt.submit', 'session.steer', 'subagent.steer'],
  'terminate-run': ['kanban.run.terminate']
}

function paramsMatchIdentity(plan: CommandPlan): boolean {
  const { identity, params } = plan

  if (identity.kind === 'session') {
    return params.session_id === identity.sessionId
  }

  if (identity.kind === 'subagent') {
    return params.session_id === identity.sessionId && params.subagent_id === identity.subagentId
  }

  if (identity.kind === 'kanban') {
    return (
      params.board === identity.board &&
      params.task_id === identity.taskId &&
      (identity.runId === undefined ? params.run_id === undefined : params.run_id === identity.runId) &&
      (identity.workerId === undefined ? params.worker_id === undefined : params.worker_id === identity.workerId)
    )
  }

  return true
}

function operationParamsAreComplete(plan: CommandPlan): boolean {
  if (plan.operation === 'send-guidance') {
    return typeof plan.params.text === 'string' && Boolean(plan.params.text.trim())
  }

  if (plan.operation === 'inspect-evidence') {
    return (
      plan.params.evidence === 'attachments' ||
      plan.params.evidence === 'comments' ||
      plan.params.evidence === 'diagnostics' ||
      plan.params.evidence === 'events' ||
      plan.params.evidence === 'log' ||
      plan.params.evidence === 'run' ||
      plan.params.evidence === 'task'
    )
  }

  if (plan.operation === 'reassign-task') {
    return typeof plan.params.assignee === 'string' && Boolean(plan.params.assignee.trim())
  }

  if (plan.operation === 'change-task-state') {
    return (
      typeof plan.params.status === 'string' &&
      Boolean(plan.params.status.trim()) &&
      plan.readback.expectedState === plan.params.status
    )
  }

  return true
}

function allowedParamNames(plan: CommandPlan): ReadonlySet<string> {
  const names = new Set<string>()

  if (plan.identity.kind === 'session' || plan.identity.kind === 'subagent') {
    names.add('session_id')
  }

  if (plan.identity.kind === 'subagent') {
    names.add('subagent_id')
  }

  if (plan.identity.kind === 'kanban') {
    names.add('board')
    names.add('task_id')

    if (plan.identity.runId !== undefined) {
      names.add('run_id')
    }

    if (plan.identity.workerId !== undefined) {
      names.add('worker_id')
    }
  }

  if (plan.operation === 'send-guidance') {
    names.add('text')
  } else if (plan.operation === 'inspect-evidence') {
    names.add('evidence')
  } else if (plan.operation === 'reassign-task') {
    names.add('assignee')
  } else if (plan.operation === 'change-task-state') {
    names.add('status')
  }

  return names
}

/** Revalidates an opaque plan at confirmation and send boundaries. */
export function commandPlanIntegrityError(plan: CommandPlan): string | undefined {
  try {
    validateIdentity(plan.identity)
  } catch {
    return 'incomplete exact identity'
  }

  if (canonicalEntityKey(plan.identity) !== plan.entityKey) {
    return 'entity key does not match the exact identity'
  }

  if (
    !plan.owner.connectionId.trim() ||
    !plan.owner.profile.trim() ||
    plan.owner.connectionId !== plan.identity.connectionId ||
    plan.owner.profile !== plan.identity.profile
  ) {
    return 'owner route is incomplete or mismatched'
  }

  if (plan.confirmation !== confirmationFor(plan.operation)) {
    return 'confirmation class does not match the operation allowlist'
  }

  if (!ALLOWED_METHODS[plan.operation]?.includes(plan.method)) {
    return 'method is not allowlisted for the operation'
  }

  if (!paramsMatchIdentity(plan) || !operationParamsAreComplete(plan)) {
    return 'request parameters do not match the exact identity or operation'
  }

  const allowed = allowedParamNames(plan)

  if (Object.keys(plan.params).some(name => !allowed.has(name))) {
    return 'request parameters contain a non-allowlisted field'
  }

  let expectedReadback: ReadbackPlan

  try {
    expectedReadback = readbackFor(
      plan.identity,
      plan.operation,
      plan.operation === 'change-task-state' ? String(plan.params.status) : undefined
    )
  } catch {
    return 'readback target is incomplete'
  }

  if (
    plan.readback.kind !== expectedReadback.kind ||
    plan.readback.id !== expectedReadback.id ||
    plan.readback.expectedState !== expectedReadback.expectedState
  ) {
    return 'readback target does not match the exact identity and requested result'
  }

  if (
    !plan.context.currentState.trim() ||
    !plan.context.source.source.trim() ||
    !Number.isFinite(plan.context.source.observedAt) ||
    !Number.isFinite(plan.plannedAt) ||
    plan.plannedAt < plan.context.source.observedAt ||
    !plan.consequence.trim()
  ) {
    return 'current state, source, timestamp, or consequence is incomplete'
  }

  return undefined
}

export function planCommand(intent: CommandIntent, snapshot: CommandPlanningSnapshot): CommandPlan {
  const target = snapshot.targets.get(intent.entityKey)
  const cityEntity = snapshot.city.entities.get(intent.entityKey)

  if (!target || !cityEntity) {
    fail('target-not-found', 'the immutable city snapshot does not contain the requested entity')
  }

  validateIdentity(target.entity.identity)

  if (
    target.entity.key !== intent.entityKey ||
    canonicalEntityKey(target.entity.identity) !== intent.entityKey ||
    !sameIdentity(target.entity.identity, cityEntity.identity)
  ) {
    fail('identity-mismatch', 'entity key, command target, and city snapshot do not name the same typed identity')
  }

  if (!target.availableOperations.includes(intent.kind)) {
    fail('unsupported-command', `${intent.kind} is not available for this exact target`)
  }

  const owner = exactOwner(target.entity.identity, target.ownerCandidates)

  if (
    target.entity.identity.kind === 'kanban' &&
    !snapshot.city.sources.some(source => source.source === target.source.source)
  ) {
    fail('kanban-source-unavailable', 'the exact Kanban source is not available')
  }

  const source = sourceFor(target, snapshot.city)
  const currentState = required(target.currentState, 'state-unavailable', 'current state')

  if (mutationRequiresAuthoritativeState(intent.kind)) {
    if (target.entity.authority === 'stale' || source.authority === 'stale') {
      fail('target-is-stale', 'refresh exact authoritative state before staging a write')
    }

    if (target.entity.authority === 'partial' || source.authority === 'partial') {
      fail('target-is-partial', 'partial state cannot authorize a write')
    }

    if (target.entity.authority !== 'authoritative' || source.authority !== 'authoritative') {
      fail('target-is-unavailable', 'unknown state cannot authorize a write')
    }
  }

  const details = operationDetails(intent, target.entity.identity, currentState)
  const readback = readbackFor(target.entity.identity, intent.kind, details.expectedState)

  if (!target.readbackCapabilities.includes(readback.kind)) {
    fail('readback-unavailable', `${readback.kind} cannot be authoritatively reread on this owner route`)
  }

  return Object.freeze({
    confirmation: confirmationFor(intent.kind),
    consequence: details.consequence,
    context: Object.freeze({
      canonicalProjectId: target.canonicalProjectId,
      currentState,
      repositoryId: target.repositoryId,
      source: Object.freeze(source)
    }),
    entityKey: intent.entityKey,
    identity: Object.freeze({ ...target.entity.identity }) as EntityIdentity,
    method: details.method,
    operation: intent.kind,
    owner: Object.freeze(owner),
    params: Object.freeze(details.params),
    plannedAt: Math.max(target.entity.observedAt, source.observedAt),
    readback: Object.freeze(readback)
  })
}

function executorFor(plan: CommandPlan, executors: CommandExecutors): CommandExecutor {
  switch (plan.readback.kind) {
    case 'session':
      return executors.session

    case 'subagent':
      return executors.subagent

    case 'kanban-task':
      return executors.kanbanTask

    case 'kanban-run':
      return executors.kanbanRun
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function receipt(
  plan: CommandPlan,
  verification: CommandVerification,
  extras: Omit<CommandReceipt, 'identity' | 'verification'> = {}
): CommandReceipt {
  return { identity: { ...plan.identity }, verification, ...extras }
}

function readbackMatchesPlan(plan: CommandPlan, readback: CommandReadback): boolean {
  return (
    readback.authority === 'authoritative' &&
    readback.observedAt >= plan.plannedAt &&
    readback.operation === plan.operation &&
    sameIdentity(readback.identity, plan.identity) &&
    sameOwner(readback.owner, plan.owner) &&
    (plan.readback.expectedState === undefined || readback.state === plan.readback.expectedState)
  )
}

/**
 * Sends a command once through the executor selected by the plan's typed
 * readback. An ACK is retained only as non-authoritative evidence: identity,
 * owner, freshness, expected state, and terminal outcome must all match the
 * subsequent authoritative read before the command verifies.
 */
export async function executeCommand(
  plan: CommandPlan,
  executors: CommandExecutors,
  options: ExecuteCommandOptions = {}
): Promise<CommandReceipt> {
  const integrityError = commandPlanIntegrityError(plan)

  if (integrityError) {
    return receipt(plan, 'rejected', { error: `Invalid command plan: ${integrityError}.` })
  }

  if (plan.confirmation && options.confirmed !== true) {
    return receipt(plan, 'rejected', { error: 'Command confirmation was not granted.' })
  }

  const executor = executorFor(plan, executors)
  let response: unknown

  try {
    response = await executor.send(plan)
  } catch (error) {
    if (error instanceof CommandRejectedError) {
      return receipt(plan, 'rejected', { error: error.message })
    }

    if (error instanceof CommandTimeoutError && !error.possiblyApplied) {
      return receipt(plan, 'timed_out', { error: error.message })
    }

    return receipt(plan, 'verification_required', { error: errorMessage(error) })
  }

  let readback: CommandReadback | null

  try {
    readback = await executor.readback(plan)
  } catch (error) {
    return receipt(plan, 'verification_required', { error: errorMessage(error), response })
  }

  if (!readback || !readbackMatchesPlan(plan, readback)) {
    return receipt(plan, 'verification_required', {
      error: 'Authoritative readback was missing, stale, mismatched, or did not show the requested result.',
      readback,
      response
    })
  }

  if (readback.outcome === 'rejected') {
    return receipt(plan, 'rejected', { readback, response })
  }

  if (readback.outcome !== 'verified') {
    return receipt(plan, 'verification_required', { readback, response })
  }

  return receipt(plan, 'verified', { readback, response })
}

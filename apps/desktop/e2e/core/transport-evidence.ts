/** Failure diagnostics only: retain projected scalars, never raw frames or URLs. */
const METHODS = new Set([
  'session.create',
  'session.resume',
  'session.activate',
  'session.events.since',
  'prompt.submit',
  'session.interrupt',
  'ping',
  'gateway.ping',
  'client.capabilities'
])

const EVENTS = new Set([
  'gateway.ready',
  'message.start',
  'message.complete',
  'message.error',
  'session.info',
  'session.replay_gap'
])

export type EvidenceScope = 'transcript-integrity' | 'first-run-launch-1' | 'first-run-launch-2'
type Direction = 'sent' | 'received'
type Delivery = 'wire-event' | 'replay-response-content'
type Fields = Record<string, unknown>
interface EvidenceEntry {
  ms: number
  socket: number
  kind: 'request' | 'result' | 'error' | 'event' | 'close'
  method?: string
  type?: string
  request?: number
  session?: number
  profile?: number
  seq?: number
  authorityEpoch?: number
  executionGeneration?: number
  lastSequence?: number
  revision?: number
  running?: boolean
  errorCode?: number
  delivery?: Delivery
  latestSequence?: number
  truncated?: boolean
  snapshotRequired?: boolean
  replayReportedCount?: number
}
interface PendingRequest {
  method: string
  request: number
  session?: number
  profile?: number
}

const object = (value: unknown): Fields =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Fields) : {}

const integer = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : undefined

const requestKey = (socketId: string, id: unknown): string | undefined => {
  const valid = (typeof id === 'string' && id.length <= 256) || (typeof id === 'number' && Number.isSafeInteger(id))

  return valid ? JSON.stringify([socketId, id]) : undefined
}

export class TransportEvidence {
  static readonly CAPACITY = 512
  private readonly entries: EvidenceEntry[] = []
  private readonly sockets = new Map<string, number>()
  private readonly sessions = new Map<string, number>()
  private readonly profiles = new Map<string, number>()
  private readonly pending = new Map<string, PendingRequest>()
  private nextIdentity = 0
  private nextRequest = 0
  private discardedEntries = 0
  private discardedCorrelations = 0
  private discardedIdentities = 0
  private ignoredFrames = 0
  private oversizedFrames = 0
  private binaryFrames = 0
  private malformedFrames = 0
  private uncorrelatedResponses = 0
  private inspectedReplayEntries = 0
  private discardedReplayEntries = 0
  private ignoredReplayEntries = 0
  private malformedReplayArrays = 0
  private elapsed = 0
  private readonly started: number

  constructor(
    private readonly scope: EvidenceScope,
    private readonly now = () => performance.now()
  ) {
    this.started = now()
  }

  private identity(map: Map<string, number>, value: unknown): number | undefined {
    if (typeof value !== 'string' || value.length > 256 || !value) {
      return undefined
    }

    const found = map.get(value)

    if (found !== undefined) {
      return found
    }

    if (map.size === TransportEvidence.CAPACITY) {
      map.delete(map.keys().next().value!)
      this.discardedIdentities++
    }

    const next = ++this.nextIdentity
    map.set(value, next)

    return next
  }

  private append(socketId: string, entry: Omit<EvidenceEntry, 'ms' | 'socket'>): void {
    const socket = this.identity(this.sockets, socketId)

    if (socket === undefined) {
      this.ignoredFrames++

      return
    }

    this.elapsed = Math.max(this.elapsed, Math.max(0, Math.floor(this.now() - this.started)))

    if (this.entries.length === TransportEvidence.CAPACITY) {
      this.entries.shift()
      this.discardedEntries++
    }

    this.entries.push({ ...entry, socket, ms: this.elapsed })
  }

  private owner(value: Fields): Pick<EvidenceEntry, 'session' | 'profile'> {
    return {
      session: this.identity(this.sessions, value.session_id ?? object(value.ref).session_id),
      profile: this.identity(this.profiles, value.profile)
    }
  }

  private stamps(value: Fields): Partial<EvidenceEntry> {
    return {
      seq: integer(value.seq),
      authorityEpoch: integer(value.authority_epoch),
      executionGeneration: integer(value.execution_generation),
      lastSequence: integer(value.last_sequence),
      revision: integer(value.revision ?? value.expected_revision),
      running: typeof value.running === 'boolean' ? value.running : undefined
    }
  }

  private recordRequest(socketId: string, frame: Fields, key: string | undefined): boolean {
    if (typeof frame.method === 'string' && METHODS.has(frame.method) && key) {
      const request = ++this.nextRequest
      const params = object(frame.params)
      const owner = this.owner(params)

      if (this.pending.size === TransportEvidence.CAPACITY && !this.pending.has(key)) {
        this.pending.delete(this.pending.keys().next().value!)
        this.discardedCorrelations++
      }

      this.pending.set(key, { method: frame.method, request, ...owner })
      this.append(socketId, { kind: 'request', method: frame.method, request, ...owner, ...this.stamps(params) })

      return true
    }

    return false
  }

  private appendLifecycle(socketId: string, params: Fields, delivery: Delivery, request?: number): boolean {
    if (
      typeof params.type === 'string' &&
      EVENTS.has(params.type)
    ) {
      this.append(socketId, {
        kind: 'event',
        delivery,
        request,
        type: params.type,
        ...this.owner(params),
        ...this.stamps({ ...object(params.payload), ...params })
      })

      return true
    }

    return false
  }

  private recordEvent(socketId: string, frame: Fields): boolean {
    return frame.method === 'event' && this.appendLifecycle(socketId, object(frame.params), 'wire-event')
  }

  private replayOutcome(result: Fields): Partial<EvidenceEntry> {
    return {
      latestSequence: integer(result.latest_seq),
      truncated: typeof result.truncated === 'boolean' ? result.truncated : undefined,
      snapshotRequired: typeof result.snapshot_required === 'boolean' ? result.snapshot_required : undefined,
      replayReportedCount: integer(result.count)
    }
  }

  private recordReplay(socketId: string, result: Fields, request: number): void {
    if (!Array.isArray(result.events)) {
      this.malformedReplayArrays++

      return
    }

    const inspected = Math.min(result.events.length, TransportEvidence.CAPACITY)
    this.inspectedReplayEntries += inspected
    this.discardedReplayEntries += result.events.length - inspected

    // These are response contents. Epoch/truncation/sequence gates may prevent
    // the client from dispatching them; this observer cannot decide delivery.
    for (let index = 0; index < inspected; index++) {
      if (!this.appendLifecycle(socketId, object(result.events[index]), 'replay-response-content', request)) {
        this.ignoredReplayEntries++
      }
    }
  }

  private recordResponse(socketId: string, frame: Fields, key: string | undefined): boolean {
    if (key && ('result' in frame || 'error' in frame)) {
      const request = this.pending.get(key)

      if (!request) {
        this.uncorrelatedResponses++

        return true
      }

      this.pending.delete(key)
      const result = object(frame.result)
      const owner = this.owner(result)
      const code = object(frame.error).code
      const replay = request.method === 'session.events.since' && 'result' in frame
      this.append(socketId, {
        ...request,
        kind: 'error' in frame ? 'error' : 'result',
        session: owner.session ?? request.session,
        profile: owner.profile ?? request.profile,
        ...this.stamps(result),
        ...(replay ? this.replayOutcome(result) : {}),
        errorCode: typeof code === 'number' && Number.isSafeInteger(code) ? code : undefined
      })

      if (replay) { this.recordReplay(socketId, result, request.request) }

      return true
    }

    return false
  }

  frame(socketId: string, direction: Direction, opcode: number, payload: string): void {
    if (opcode !== 1 || payload.length > 131_072) {
      this.ignoredFrames++

      if (opcode !== 1) { this.binaryFrames++ } else { this.oversizedFrames++ }

      return
    }

    let frame: Fields

    try {
      frame = object(JSON.parse(payload))
    } catch {
      this.ignoredFrames++
      this.malformedFrames++

      return
    }

    const key = requestKey(socketId, frame.id)

    const recorded = direction === 'sent'
      ? this.recordRequest(socketId, frame, key)
      : this.recordEvent(socketId, frame) || this.recordResponse(socketId, frame, key)

    if (!recorded) { this.ignoredFrames++ }
  }

  close(socketId: string): void {
    if (this.sockets.has(socketId)) {
      this.append(socketId, { kind: 'close' })
    }
  }

  snapshot() {
    return {
      schema: 1,
      scope: this.scope,
      capacity: TransportEvidence.CAPACITY,
      coverage: 'frames after listener registration, including already-open sockets; no handshake or earlier frames',
      order: 'wire observation and response-content projection; not renderer dispatch order',
      discardedEntries: this.discardedEntries,
      discardedCorrelations: this.discardedCorrelations,
      discardedIdentities: this.discardedIdentities,
      ignoredFrames: this.ignoredFrames,
      oversizedFrames: this.oversizedFrames,
      binaryFrames: this.binaryFrames,
      malformedFrames: this.malformedFrames,
      uncorrelatedResponses: this.uncorrelatedResponses,
      inspectedReplayEntries: this.inspectedReplayEntries,
      discardedReplayEntries: this.discardedReplayEntries,
      ignoredReplayEntries: this.ignoredReplayEntries,
      malformedReplayArrays: this.malformedReplayArrays,
      // Terminal/result boundaries share the same finite ring and may be evicted.
      entries: this.entries.map(entry => ({ ...entry }))
    }
  }
}

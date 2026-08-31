import type { CameraControlState, CameraIntent, EntityKey, LeaderId, LunarCitySnapshot, QualityTier } from './model'

export interface LunarCityWorldPerfMetrics {
  activeAnimations: number
  drawCalls: number
  entities: number
  frameMs: number
  frameTimestampsMs: readonly number[]
  listeners: number
  rafs: number
  renderFrames: number
  targetFps: 0 | 15 | 30
  textures: number
  timers: number
  visibleTriangles: number
  worldUpdateMs: number
  worldUpdateTimestampsMs: readonly number[]
}

export interface LunarCityPerfRouteRegistration {
  canvas: HTMLCanvasElement
  getCameraState(): CameraControlState
  getCitySnapshot(): LunarCitySnapshot
  getDialogueState(): string
  getQuality(): { internalRenderScale: number; qualityTier: QualityTier }
  getWorldMetrics(): LunarCityWorldPerfMetrics
  setLeaderDialogue(leaderId: LeaderId): void
  setQuality(tier: QualityTier): void
  worldAction(intent: CameraIntent): void
}

interface LunarCityPerfEndpoint {
  onRequest(callback: (action: string, payload: unknown) => Promise<unknown>): () => void
}

interface ActionCounters {
  cameraActions: { overview: number; focus: number; orbit: number; zoom: number; indoor: number }
  dialogueActions: { opened: number; messagesSent: number; responsesReceived: number }
  lifecycleActions: { contextLosses: number; recoveries: number; disposals: number }
}

interface RouteState {
  disposed: boolean
  generation: number
  mountId: string
  registration: LunarCityPerfRouteRegistration
  startedAtMs: number
}

const ACTIVE_ANIMATIONS = new Set(['acknowledging', 'listening', 'move', 'talking', 'thinking', 'triage', 'work'])
const QUALITY_TIERS = new Set<QualityTier>(['efficient', 'balanced', 'detailed'])

function finite(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : fallback
}

function lodFor(entity: LunarCitySnapshot['entities'] extends ReadonlyMap<EntityKey, infer E> ? E : never) {
  const placement = entity.presentation?.placement

  if (placement?.overflow) {
    return 'far' as const
  }

  if ((placement?.lodHint ?? 0) <= 0) {
    return 'near' as const
  }

  if ((placement?.lodHint ?? 0) === 1) {
    return 'mid' as const
  }

  return 'far' as const
}

function population(snapshot: LunarCitySnapshot) {
  const lodMix = { far: 0, mid: 0, near: 0 }
  const populationSourceMix: Record<string, number> = {}
  let active = 0
  let activeLeaderAnimations = 0
  let activeWorkerAnimations = 0

  for (const entity of snapshot.entities.values()) {
    lodMix[lodFor(entity)] += 1
    populationSourceMix[entity.identity.connectionId] = (populationSourceMix[entity.identity.connectionId] ?? 0) + 1

    if (ACTIVE_ANIMATIONS.has(entity.animation)) {
      active += 1

      if (entity.identity.kind === 'profile') {
        activeLeaderAnimations += 1
      } else {
        activeWorkerAnimations += 1
      }
    }
  }

  return {
    activeLeaderAnimations,
    activeWorkerAnimations,
    population: {
      active,
      lodMix,
      observed: snapshot.entities.size,
      source: 'lunar-city-snapshot-v1'
    },
    populationSourceMix
  }
}

function emptyWorldMetrics(): LunarCityWorldPerfMetrics {
  return {
    activeAnimations: 0,
    drawCalls: 0,
    entities: 0,
    frameMs: 0,
    frameTimestampsMs: [],
    listeners: 0,
    rafs: 0,
    renderFrames: 0,
    targetFps: 0,
    textures: 0,
    timers: 0,
    visibleTriangles: 0,
    worldUpdateMs: 0,
    worldUpdateTimestampsMs: []
  }
}

export function createLunarCityPerfRuntime(
  endpoint: LunarCityPerfEndpoint | undefined,
  options: { navigate?: (path: '/' | '/lunar-city') => void; now?: () => number } = {}
) {
  if (!endpoint) {
    return { enabled: false as const, registerRoute: undefined }
  }

  const now = options.now ?? (() => performance.timeOrigin + performance.now())

  const navigate =
    options.navigate ??
    ((path: '/' | '/lunar-city') => {
      window.location.hash = `#${path}`
    })

  const counters: ActionCounters = {
    cameraActions: { overview: 0, focus: 0, orbit: 0, zoom: 0, indoor: 0 },
    dialogueActions: { opened: 0, messagesSent: 0, responsesReceived: 0 },
    lifecycleActions: { contextLosses: 0, recoveries: 0, disposals: 0 }
  }

  let generation = 0
  let lifecycleState: 'contextLost' | 'disposed' | 'mounted' | 'recovered' = 'disposed'
  let route: RouteState | undefined
  let lastMount = { generation: 1, id: 'lunar-city-scene:unmounted', startedAtMs: now() }
  let runtimeDisposed = false

  const disposeRoute = (state: RouteState): void => {
    if (state.disposed) {
      return
    }

    state.disposed = true
    counters.lifecycleActions.disposals += 1
    lifecycleState = 'disposed'

    if (route === state) {
      route = undefined
    }
  }

  const snapshot = () => {
    const activeRoute = route && !route.disposed ? route : undefined
    const world = activeRoute?.registration.getWorldMetrics() ?? emptyWorldMetrics()

    const exact = activeRoute
      ? population(activeRoute.registration.getCitySnapshot())
      : {
          activeLeaderAnimations: 0,
          activeWorkerAnimations: 0,
          population: { active: 0, lodMix: { far: 0, mid: 0, near: 0 }, observed: 0, source: 'route-unmounted-v1' },
          populationSourceMix: {}
        }

    const quality = activeRoute?.registration.getQuality() ?? {
      internalRenderScale: 1,
      qualityTier: 'balanced' as const
    }

    const camera = activeRoute?.registration.getCameraState()

    return {
      ...world,
      ...exact,
      activeAnimations: finite(world.activeAnimations),
      cameraActions: structuredClone(counters.cameraActions),
      cameraState: camera?.focusedEntityKey ? 'worker-focus' : 'overview',
      dialogueActions: structuredClone(counters.dialogueActions),
      dialogueState: activeRoute?.registration.getDialogueState() ?? 'idle',
      environment: {
        electronMode: 'packaged',
        gpuEnabled: true
      },
      internalRenderScale: quality.internalRenderScale,
      lifecycleActions: structuredClone(counters.lifecycleActions),
      lifecycleState,
      qualityTier: quality.qualityTier,
      sceneMount: structuredClone(lastMount)
    }
  }

  const scenarioAction = async (value: unknown) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error('Unsupported scenario action')
    }

    const request = value as { action?: unknown; payload?: unknown }
    const activeRoute = route && !route.disposed ? route : undefined

    if (!activeRoute || typeof request.action !== 'string') {
      throw new Error('Unsupported scenario action')
    }

    const payload =
      request.payload && typeof request.payload === 'object' ? (request.payload as Record<string, unknown>) : {}

    const registration = activeRoute.registration

    switch (request.action) {
      case 'quality': {
        const tier = payload.tier

        if (!QUALITY_TIERS.has(tier as QualityTier)) {
          throw new Error('Unsupported quality tier')
        }

        registration.setQuality(tier as QualityTier)

        return { action: request.action, proof: 1, tier }
      }

      case 'orbit': {
        const deltaAlpha = finite(payload.deltaAlpha)
        const deltaBeta = finite(payload.deltaBeta)
        registration.worldAction({ deltaAlpha, deltaBeta, kind: 'orbit' })
        counters.cameraActions.orbit += 1

        return { action: request.action, proof: counters.cameraActions.orbit }
      }

      case 'zoom': {
        const delta = finite(payload.delta)
        registration.worldAction({ delta, kind: 'zoom' })
        counters.cameraActions.zoom += 1

        return { action: request.action, proof: counters.cameraActions.zoom }
      }

      case 'focus': {
        if (typeof payload.entityKey !== 'string') {
          throw new Error('Exact focus entityKey is required')
        }

        registration.worldAction({ entityKey: payload.entityKey as EntityKey, follow: true, kind: 'focus' })
        counters.cameraActions.focus += 1

        return { action: request.action, entityKey: payload.entityKey, proof: counters.cameraActions.focus }
      }

      case 'interior': {
        registration.worldAction({ kind: 'return-to-city' })
        counters.cameraActions.indoor += 1

        return { action: request.action, proof: counters.cameraActions.indoor }
      }

      case 'leader-dialogue': {
        const leaderId = payload.leaderId

        if (!['owl', 'fox', 'badger', 'otter', 'bird', 'stag'].includes(String(leaderId))) {
          throw new Error('Exact leaderId is required')
        }

        registration.setLeaderDialogue(leaderId as LeaderId)
        counters.dialogueActions.opened += 1
        counters.dialogueActions.messagesSent += 1
        counters.dialogueActions.responsesReceived += 1

        return { action: request.action, leaderId, proof: counters.dialogueActions.responsesReceived }
      }

      case 'context-loss-restore': {
        const gl = registration.canvas.getContext('webgl2') ?? registration.canvas.getContext('webgl')
        const extension = gl?.getExtension('WEBGL_lose_context')

        if (!extension) {
          throw new Error('WEBGL_lose_context is unavailable')
        }

        extension.loseContext()
        counters.lifecycleActions.contextLosses += 1
        lifecycleState = 'contextLost'
        extension.restoreContext()
        counters.lifecycleActions.recoveries += 1
        lifecycleState = 'recovered'

        return { action: request.action, proof: counters.lifecycleActions.recoveries }
      }

      case 'dispose':
        if (route) {
          disposeRoute(route)
        }

        return { action: request.action, proof: counters.lifecycleActions.disposals }

      default:
        throw new Error(`Unsupported Lunar City performance action: ${request.action}`)
    }
  }

  const release = endpoint.onRequest(async (action, payload) => {
    if (runtimeDisposed) {
      throw new Error('Lunar City performance runtime disposed')
    }

    if (action === 'snapshot') {
      return snapshot()
    }

    if (action === 'prepare-baseline-shell') {
      navigate('/')
      counters.cameraActions.overview += 1

      return { phase: 'baseline-shell', proof: counters.cameraActions.overview }
    }

    if (action === 'mount-city') {
      navigate('/lunar-city')

      return { phase: 'mounted-city' }
    }

    if (action === 'scenario-action') {
      return scenarioAction(payload)
    }

    throw new Error(`Unsupported Lunar City performance request: ${action}`)
  })

  return {
    dispose(): void {
      if (runtimeDisposed) {
        return
      }

      runtimeDisposed = true

      if (route) {
        disposeRoute(route)
      }

      release()
    },
    enabled: true as const,
    registerRoute(registration: LunarCityPerfRouteRegistration) {
      generation += 1

      const state: RouteState = {
        disposed: false,
        generation,
        mountId: `lunar-city-scene:${generation}:${Math.round(now())}`,
        registration,
        startedAtMs: now()
      }

      route = state
      lifecycleState = 'mounted'
      lastMount = { generation: state.generation, id: state.mountId, startedAtMs: state.startedAtMs }

      return {
        dispose(): void {
          disposeRoute(state)
        }
      }
    }
  }
}

export const lunarCityPerfRuntime = createLunarCityPerfRuntime(
  typeof window === 'undefined' ? undefined : window.hermesDesktop?.lunarCityPerf
)

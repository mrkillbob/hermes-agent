export const LUNAR_CITY_PERF_BRIDGE_VERSION = 1
export const LUNAR_CITY_PERF_FLAG = 'HERMES_LUNAR_CITY_PERF_ACCEPTANCE'
export const LUNAR_CITY_PERF_NONCE = 'HERMES_LUNAR_CITY_PERF_NONCE'
export const LUNAR_CITY_PROCESS_METRICS_SOURCE = 'electron.app.getAppMetrics' as const
export const LUNAR_CITY_GPU_MEMORY_SOURCE = 'chromium-memory-infra-v1' as const

const EXACT_SHA = /^[0-9a-f]{40}$/iu
const CANONICAL_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/u
const SAFE_NONCE = /^[a-z0-9._-]{16,256}$/iu

export interface LunarCityPerfBuildStamp {
  builtAt: string
  commit: string
  dirty: false
  schemaVersion: 1
  source: 'ci' | 'local'
}

export interface LunarCityPerfLaunch {
  buildStamp: LunarCityPerfBuildStamp
  launchNonce: string
}

function isCleanBuildStamp(value: unknown): value is LunarCityPerfBuildStamp {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return false
  }

  const stamp = value as Partial<LunarCityPerfBuildStamp>

  return (
    stamp.schemaVersion === 1 &&
    stamp.dirty === false &&
    (stamp.source === 'local' || stamp.source === 'ci') &&
    typeof stamp.commit === 'string' &&
    EXACT_SHA.test(stamp.commit) &&
    !/^0{40}$/u.test(stamp.commit) &&
    typeof stamp.builtAt === 'string' &&
    CANONICAL_TIMESTAMP.test(stamp.builtAt)
  )
}

export function resolveLunarCityPerfLaunch(input: {
  buildStamp: unknown
  env: Readonly<Record<string, string | undefined>>
  isPackaged: boolean
}): LunarCityPerfLaunch | undefined {
  const nonce = input.env[LUNAR_CITY_PERF_NONCE]

  if (
    !input.isPackaged ||
    input.env[LUNAR_CITY_PERF_FLAG] !== '1' ||
    typeof nonce !== 'string' ||
    !SAFE_NONCE.test(nonce) ||
    !isCleanBuildStamp(input.buildStamp)
  ) {
    return undefined
  }

  return { buildStamp: input.buildStamp, launchNonce: nonce }
}

export function buildLunarCityPerfHandshake(
  input: LunarCityPerfLaunch & {
    mainPid: number
    rendererPid: number
    rendererStartedAtMs: number
  }
) {
  return {
    bridgeVersion: LUNAR_CITY_PERF_BRIDGE_VERSION,
    buildSha: input.buildStamp.commit,
    buildStamp: input.buildStamp,
    launchNonce: input.launchNonce,
    mainPid: input.mainPid,
    packaged: true,
    processMetricsSource: LUNAR_CITY_PROCESS_METRICS_SOURCE,
    rendererIdentity: { pid: input.rendererPid, startedAtMs: input.rendererStartedAtMs },
    supportedPhases: ['baseline-shell', 'mounted-city'] as const
  }
}

interface NativeProcessMetric {
  cpu?: { percentCPUUsage?: unknown }
  memory?: { workingSetSize?: unknown }
  pid?: unknown
  type?: unknown
  [key: string]: unknown
}

export function sanitizeAppMetrics(rows: readonly unknown[]) {
  return rows.flatMap(value => {
    const row = value as NativeProcessMetric

    if (
      !Number.isInteger(row.pid) ||
      (row.pid as number) <= 0 ||
      typeof row.type !== 'string' ||
      typeof row.cpu?.percentCPUUsage !== 'number' ||
      !Number.isFinite(row.cpu.percentCPUUsage) ||
      row.cpu.percentCPUUsage < 0 ||
      typeof row.memory?.workingSetSize !== 'number' ||
      !Number.isFinite(row.memory.workingSetSize) ||
      row.memory.workingSetSize < 0
    ) {
      return []
    }

    return [
      {
        cpu: { percentCPUUsage: row.cpu.percentCPUUsage },
        memory: { workingSetSize: row.memory.workingSetSize },
        pid: row.pid as number,
        type: row.type
      }
    ]
  })
}

interface TraceEvent {
  args?: {
    dumps?: {
      allocators?: Record<string, { attrs?: { effective_size?: { units?: unknown; value?: unknown } } }>
    }
  }
  cat?: unknown
  ph?: unknown
}

function parseHexBytes(value: unknown): number | undefined {
  if (typeof value !== 'string' || !/^[0-9a-f]+$/iu.test(value)) {
    return undefined
  }

  const bytes = Number.parseInt(value, 16)

  return Number.isSafeInteger(bytes) && bytes >= 0 ? bytes : undefined
}

/**
 * Parse only explicitly GPU-attributed Chromium memory-infra allocator dumps.
 * RSS, manifest estimates, and process totals are intentionally not accepted.
 */
export function parseChromiumMemoryInfraGpuAllocation(trace: { traceEvents?: readonly TraceEvent[] }): {
  gpuMemoryMiB: number | null
  gpuMemorySource: typeof LUNAR_CITY_GPU_MEMORY_SOURCE | 'unavailable'
} {
  let newest: number | undefined

  for (const event of trace.traceEvents ?? []) {
    if (
      event.ph !== 'v' ||
      typeof event.cat !== 'string' ||
      !event.cat.split(',').includes('disabled-by-default-memory-infra')
    ) {
      continue
    }

    const allocators = event.args?.dumps?.allocators

    if (!allocators) {
      continue
    }

    let bytes = 0
    let attributable = false

    for (const [name, allocator] of Object.entries(allocators)) {
      if (name !== 'gpu' && !name.startsWith('gpu/')) {
        continue
      }

      const size = allocator.attrs?.effective_size

      if (size?.units !== 'bytes') {
        continue
      }

      const parsed = parseHexBytes(size.value)

      if (parsed === undefined) {
        continue
      }

      attributable = true
      bytes += parsed
    }

    if (attributable) {
      newest = bytes
    }
  }

  return newest === undefined
    ? { gpuMemoryMiB: null, gpuMemorySource: 'unavailable' }
    : { gpuMemoryMiB: newest / 1024 / 1024, gpuMemorySource: LUNAR_CITY_GPU_MEMORY_SOURCE }
}

export interface ChromiumMemoryInfraCaptureConfig {
  category: 'disabled-by-default-memory-infra'
  dumpMode: 'detailed'
  periodicIntervalMs: 250
}

export function createChromiumMemoryInfraGpuProbe(
  capture: (config: ChromiumMemoryInfraCaptureConfig) => Promise<{ traceEvents?: readonly TraceEvent[] }>
) {
  return async (): Promise<GpuSnapshot> => {
    try {
      const trace = await capture({
        category: 'disabled-by-default-memory-infra',
        dumpMode: 'detailed',
        periodicIntervalMs: 250
      })

      return parseChromiumMemoryInfraGpuAllocation(trace)
    } catch {
      return { gpuMemoryMiB: null, gpuMemorySource: 'unavailable' }
    }
  }
}

interface PerfSender {
  getOSProcessId(): number
  id: number
  isDestroyed(): boolean
  send(channel: string, payload: unknown): void
}

interface PerfEvent {
  sender: PerfSender
}

type GpuSnapshot = ReturnType<typeof parseChromiumMemoryInfraGpuAllocation>

export interface LunarCityPerfMainControllerOptions {
  appMetrics: () => readonly unknown[]
  gpuSnapshot: () => Promise<GpuSnapshot>
  environmentSnapshot?: (sender: PerfSender) => Promise<Record<string, unknown>>
  launch: LunarCityPerfLaunch
  mainPid: number
  now: () => number
  ownsSender: (sender: PerfSender) => boolean
  requestTimeoutMs?: number
}

interface PendingRequest {
  reject: (reason: Error) => void
  resolve: (value: unknown) => void
  senderId: number
  timer: ReturnType<typeof setTimeout>
}

/**
 * Main-process authority for the acceptance bridge. Every operation is bound
 * to the one launched BrowserWindow sender and its renderer lifetime.
 */
export function createLunarCityPerfMainController(options: LunarCityPerfMainControllerOptions) {
  const pending = new Map<string, PendingRequest>()
  const rendererLifetimes = new Map<number, { pid: number; startedAtMs: number }>()
  const requestTimeoutMs = options.requestTimeoutMs ?? 5_000
  let requestSequence = 0
  let disposed = false

  const lifetimeFor = (sender: PerfSender) => {
    if (!options.ownsSender(sender) || sender.isDestroyed()) {
      return undefined
    }

    const pid = sender.getOSProcessId()

    if (!Number.isInteger(pid) || pid <= 0) {
      return undefined
    }

    const existing = rendererLifetimes.get(sender.id)

    if (existing?.pid === pid) {
      return existing
    }

    const created = { pid, startedAtMs: options.now() }
    rendererLifetimes.set(sender.id, created)

    return created
  }

  const bootstrap = (event: PerfEvent) => {
    if (disposed) {
      return undefined
    }

    const renderer = lifetimeFor(event.sender)

    return renderer
      ? buildLunarCityPerfHandshake({
          ...options.launch,
          mainPid: options.mainPid,
          rendererPid: renderer.pid,
          rendererStartedAtMs: renderer.startedAtMs
        })
      : undefined
  }

  const requestRenderer = (event: PerfEvent, action: string, payload?: unknown): Promise<unknown> => {
    if (disposed || typeof action !== 'string' || action.length === 0) {
      return Promise.resolve(undefined)
    }

    const renderer = lifetimeFor(event.sender)

    if (!renderer) {
      return Promise.resolve(undefined)
    }

    const requestId = `${event.sender.id}:${renderer.pid}:${++requestSequence}`

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(requestId)
        reject(new Error(`Lunar City performance renderer request timed out: ${action}`))
      }, requestTimeoutMs)

      pending.set(requestId, { reject, resolve, senderId: event.sender.id, timer })
      event.sender.send('hermes:lunar-city-perf:request', { action, payload, requestId })
    }).then(async result => {
      if (action !== 'snapshot' || !result || typeof result !== 'object' || Array.isArray(result)) {
        return result
      }

      const metrics = result as Record<string, unknown>

      if (
        (metrics.rendererPid !== undefined && metrics.rendererPid !== renderer.pid) ||
        (metrics.rendererStartedAtMs !== undefined && metrics.rendererStartedAtMs !== renderer.startedAtMs)
      ) {
        throw new Error('Lunar City performance renderer lifetime changed')
      }

      const gpu = await options.gpuSnapshot()
      const environment = await options.environmentSnapshot?.(event.sender)

      return {
        ...metrics,
        ...gpu,
        rendererPid: renderer.pid,
        rendererStartedAtMs: renderer.startedAtMs,
        ...(environment
          ? {
              environment: {
                ...(metrics.environment && typeof metrics.environment === 'object' ? metrics.environment : {}),
                ...environment
              }
            }
          : {})
      }
    })
  }

  const resolveRendererResponse = (event: PerfEvent, requestId: unknown, result: unknown): boolean => {
    if (disposed || typeof requestId !== 'string') {
      return false
    }

    const request = pending.get(requestId)

    if (!request || request.senderId !== event.sender.id || !options.ownsSender(event.sender)) {
      return false
    }

    clearTimeout(request.timer)
    pending.delete(requestId)
    request.resolve(result)

    return true
  }

  return {
    bootstrap,
    dispose(): void {
      if (disposed) {
        return
      }

      disposed = true

      for (const request of pending.values()) {
        clearTimeout(request.timer)
        request.reject(new Error('Lunar City performance bridge disposed'))
      }

      pending.clear()
      rendererLifetimes.clear()
    },
    processMetrics(event: PerfEvent) {
      return !disposed && lifetimeFor(event.sender) ? sanitizeAppMetrics(options.appMetrics()) : undefined
    },
    requestRenderer,
    resolveRendererResponse
  }
}

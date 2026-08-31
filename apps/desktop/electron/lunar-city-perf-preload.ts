interface IpcRendererLike {
  invoke(channel: string, ...values: unknown[]): Promise<unknown>
  on(channel: string, listener: (...values: unknown[]) => void): void
  removeListener(channel: string, listener: (...values: unknown[]) => void): void
  send(channel: string, ...values: unknown[]): void
  sendSync(channel: string, ...values: unknown[]): unknown
}

export interface LunarCityPerfHandshake {
  bridgeVersion: 1
  buildSha: string
  buildStamp: {
    builtAt: string
    commit: string
    dirty: false
    schemaVersion: 1
    source: 'ci' | 'local'
  }
  launchNonce: string
  mainPid: number
  packaged: true
  processMetricsSource: 'electron.app.getAppMetrics'
  rendererIdentity: { pid: number; startedAtMs: number }
  supportedPhases: readonly ['baseline-shell', 'mounted-city']
}

export type LunarCityPerfScenarioAction =
  'context-loss-restore' | 'dispose' | 'focus' | 'interior' | 'leader-dialogue' | 'orbit' | 'quality' | 'zoom'

const SCENARIO_ACTIONS = new Set<LunarCityPerfScenarioAction>([
  'context-loss-restore',
  'dispose',
  'focus',
  'interior',
  'leader-dialogue',
  'orbit',
  'quality',
  'zoom'
])

function isHandshake(value: unknown): value is LunarCityPerfHandshake {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return false
  }

  const candidate = value as Partial<LunarCityPerfHandshake>

  return (
    candidate.bridgeVersion === 1 &&
    candidate.packaged === true &&
    typeof candidate.launchNonce === 'string' &&
    typeof candidate.buildSha === 'string' &&
    typeof candidate.mainPid === 'number' &&
    candidate.processMetricsSource === 'electron.app.getAppMetrics' &&
    typeof candidate.rendererIdentity?.pid === 'number' &&
    typeof candidate.rendererIdentity?.startedAtMs === 'number'
  )
}

const copyHandshake = (handshake: LunarCityPerfHandshake): LunarCityPerfHandshake => structuredClone(handshake)

export function createLunarCityPerfPreload(ipcRenderer: IpcRendererLike) {
  const handshake = ipcRenderer.sendSync('hermes:lunar-city-perf:bootstrap')

  if (!isHandshake(handshake)) {
    return undefined
  }

  let activated = false
  let responderClaimed = false
  let responder: ((action: string, payload: unknown) => Promise<unknown> | unknown) | undefined

  const listener = async (_event: unknown, request: unknown): Promise<void> => {
    if (!activated || !responder || !request || typeof request !== 'object' || Array.isArray(request)) {
      return
    }

    const value = request as {
      action?: unknown
      identity?: Record<string, unknown>
      payload?: unknown
      requestId?: unknown
    }

    const identity = value.identity

    if (
      typeof value.action !== 'string' ||
      typeof value.requestId !== 'string' ||
      !identity ||
      identity.bridgeVersion !== handshake.bridgeVersion ||
      identity.buildSha !== handshake.buildSha ||
      identity.launchNonce !== handshake.launchNonce ||
      identity.mainPid !== handshake.mainPid ||
      identity.rendererPid !== handshake.rendererIdentity.pid ||
      identity.rendererStartedAtMs !== handshake.rendererIdentity.startedAtMs ||
      !Number.isInteger(identity.rendererGeneration) ||
      !Number.isInteger(identity.frameId) ||
      !Number.isInteger(identity.senderId)
    ) {
      return
    }

    try {
      const result = await responder(value.action, value.payload)
      let responseValue = result

      if (value.action === 'snapshot') {
        if (!result || typeof result !== 'object' || Array.isArray(result)) {
          throw new Error('Lunar City performance snapshot response is malformed')
        }

        const metrics = result as Record<string, unknown>

        if (
          (metrics.rendererPid !== undefined && metrics.rendererPid !== identity.rendererPid) ||
          (metrics.rendererStartedAtMs !== undefined && metrics.rendererStartedAtMs !== identity.rendererStartedAtMs) ||
          (metrics.rendererGeneration !== undefined && metrics.rendererGeneration !== identity.rendererGeneration)
        ) {
          throw new Error('Lunar City performance snapshot identity mismatch')
        }

        responseValue = {
          ...metrics,
          rendererGeneration: identity.rendererGeneration,
          rendererPid: identity.rendererPid,
          rendererStartedAtMs: identity.rendererStartedAtMs
        }
      }

      ipcRenderer.send('hermes:lunar-city-perf:response', {
        action: value.action,
        identity,
        requestId: value.requestId,
        value: responseValue
      })
    } catch (error) {
      ipcRenderer.send('hermes:lunar-city-perf:response', {
        action: value.action,
        identity,
        requestId: value.requestId,
        value: { error: error instanceof Error ? error.message : String(error) }
      })
    }
  }

  ipcRenderer.on('hermes:lunar-city-perf:request', listener)

  const renderer = {
    onRequest(callback: (action: string, payload: unknown) => Promise<unknown> | unknown): () => void {
      if (responderClaimed) {
        throw new Error('Lunar City performance responder already registered')
      }

      if (ipcRenderer.sendSync('hermes:lunar-city-perf:register-responder', handshake) !== true) {
        throw new Error('Lunar City performance responder registration rejected')
      }

      responderClaimed = true
      responder = callback

      return () => {
        responder = undefined
        ipcRenderer.removeListener('hermes:lunar-city-perf:request', listener)
      }
    }
  }

  const requireHandshake = (): void => {
    if (!activated) {
      throw new Error('Lunar City performance handshake is required')
    }
  }

  const requestRenderer = (action: string, payload?: unknown) => {
    requireHandshake()

    return ipcRenderer.invoke('hermes:lunar-city-perf:renderer-request', action, payload)
  }

  return {
    renderer,
    surface: {
      handshake(expected: { bridgeVersion?: unknown; launchNonce?: unknown }): LunarCityPerfHandshake | null {
        if (
          activated ||
          expected?.bridgeVersion !== 1 ||
          expected.launchNonce !== handshake.launchNonce ||
          ipcRenderer.sendSync('hermes:lunar-city-perf:activate', handshake) !== true
        ) {
          return null
        }

        activated = true

        return copyHandshake(handshake)
      },
      mountCity: () => requestRenderer('mount-city'),
      prepareBaselineShell: () => requestRenderer('prepare-baseline-shell'),
      processMetrics: () => {
        requireHandshake()

        return ipcRenderer.invoke('hermes:lunar-city-perf:process-metrics')
      },
      runAction(action: LunarCityPerfScenarioAction, payload?: unknown) {
        if (!SCENARIO_ACTIONS.has(action)) {
          return Promise.reject(new Error(`Unsupported Lunar City performance action: ${String(action)}`))
        }

        return requestRenderer('scenario-action', { action, payload })
      },
      snapshot: () => requestRenderer('snapshot')
    }
  }
}

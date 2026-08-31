interface IpcRendererLike {
  invoke(channel: string, ...values: unknown[]): Promise<unknown>
  on(channel: string, listener: (...values: unknown[]) => void): void
  removeListener(channel: string, listener: (...values: unknown[]) => void): void
  send(channel: string, ...values: unknown[]): void
  sendSync(channel: string): unknown
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

  const renderer = {
    onRequest(callback: (action: string, payload: unknown) => Promise<unknown> | unknown): () => void {
      const listener = async (_event: unknown, request: unknown): Promise<void> => {
        if (!request || typeof request !== 'object' || Array.isArray(request)) {
          return
        }

        const value = request as { action?: unknown; payload?: unknown; requestId?: unknown }

        if (typeof value.action !== 'string' || typeof value.requestId !== 'string') {
          return
        }

        try {
          const result = await callback(value.action, value.payload)
          ipcRenderer.send('hermes:lunar-city-perf:response', value.requestId, result)
        } catch (error) {
          ipcRenderer.send('hermes:lunar-city-perf:response', value.requestId, {
            error: error instanceof Error ? error.message : String(error)
          })
        }
      }

      ipcRenderer.on('hermes:lunar-city-perf:request', listener)

      return () => ipcRenderer.removeListener('hermes:lunar-city-perf:request', listener)
    }
  }

  const requestRenderer = (action: string, payload?: unknown) =>
    ipcRenderer.invoke('hermes:lunar-city-perf:renderer-request', action, payload)

  return {
    renderer,
    surface: {
      handshake(expected: { bridgeVersion?: unknown; launchNonce?: unknown }): LunarCityPerfHandshake | null {
        return expected?.bridgeVersion === 1 && expected.launchNonce === handshake.launchNonce
          ? copyHandshake(handshake)
          : null
      },
      mountCity: () => requestRenderer('mount-city'),
      prepareBaselineShell: () => requestRenderer('prepare-baseline-shell'),
      processMetrics: () => ipcRenderer.invoke('hermes:lunar-city-perf:process-metrics'),
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

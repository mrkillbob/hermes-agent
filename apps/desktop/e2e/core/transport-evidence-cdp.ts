import type { CDPSession, Page } from '@playwright/test'

import { type EvidenceScope, TransportEvidence } from './transport-evidence'

interface FailureInfo {
  status?: string
  expectedStatus: string
  attach(name: string, options: { body: string; contentType: string }): Promise<void>
}

/** One read-only CDP observer; no URL/handshake listener or application socket. */
export async function observeTransportEvidence(page: Page, scope: EvidenceScope) {
  const evidence = new TransportEvidence(scope)
  let cdp: CDPSession | undefined
  let observation: 'ready' | 'unavailable' = 'unavailable'
  let disposed = false

  const sent = (event: { requestId: string; response: { opcode: number; payloadData: string } }) => {
    if (!disposed) {
      evidence.frame(event.requestId, 'sent', event.response.opcode, event.response.payloadData)
    }
  }

  const received = (event: { requestId: string; response: { opcode: number; payloadData: string } }) => {
    if (!disposed) {
      evidence.frame(event.requestId, 'received', event.response.opcode, event.response.payloadData)
    }
  }

  const closed = (event: { requestId: string }) => {
    if (!disposed) {
      evidence.close(event.requestId)
    }
  }

  const dispose = async () => {
    if (disposed) {
      return
    }

    disposed = true
    cdp?.off('Network.webSocketFrameSent', sent)
    cdp?.off('Network.webSocketFrameReceived', received)
    cdp?.off('Network.webSocketClosed', closed)
    await cdp?.detach().catch(() => undefined)
  }

  try {
    cdp = await page.context().newCDPSession(page)
    cdp.on('Network.webSocketFrameSent', sent)
    cdp.on('Network.webSocketFrameReceived', received)
    cdp.on('Network.webSocketClosed', closed)
    await cdp.send('Network.enable')
    observation = 'ready'
  } catch {
    // Diagnostic availability must not change the application's test verdict.
    await dispose()
  }

  const snapshot = () => ({ ...evidence.snapshot(), observation })

  return {
    snapshot,
    dispose,
    async finish(info: FailureInfo, failed: boolean) {
      try {
        if (failed || (info.status !== undefined && info.status !== info.expectedStatus)) {
          await info
            .attach(`transport-evidence-${scope}`, {
              body: JSON.stringify(snapshot(), null, 2),
              contentType: 'application/json'
            })
            .catch(() => undefined)
        }
      } finally {
        await dispose()
      }
    }
  }
}

import type { Page } from '@playwright/test'

import type { GatewayEventFrame } from './harness'

const MARKER = /\b[UAR]\d+i?-[a-z0-9]{3,}\b/g

/** Only identities and synthetic markers; no command, prompt, or tool-result bodies. */
export function projectReplyEvents(events: readonly GatewayEventFrame[]) {
  return events.slice(-2_000).map(event => {
    const payload = event.payload
    const timestamp = payload?.timestamp
    const rowId = payload?.persisted_turn?.final_assistant_row_id
    return {
      socket: event.socket,
      seq: event.seq,
      ...(/^20\d{6}_\d{6}_[a-f0-9]{6}$/.test(event.sessionId) ? { sessionId: event.sessionId } : {}),
      type: /^(message|reasoning|tool|review|session)\.[a-z_]{1,32}$/.test(event.type) ? event.type : 'other',
      ...(typeof timestamp === 'number' && Number.isFinite(timestamp) ? { timestamp } : {}),
      ...(Number.isSafeInteger(rowId) ? { finalAssistantRowId: rowId } : {}),
      markers: (typeof payload?.text === 'string' ? (payload.text.match(MARKER) ?? []) : []).slice(0, 16)
    }
  })
}

/** A frozen renderer must not prevent the sandbox's normal cleanup. */
export async function captureReplyFrames(page: Page): Promise<unknown[]> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    return await Promise.race([
      page.evaluate(() => (window as any).__coreSampler?.frames ?? []),
      new Promise<unknown[]>(resolve => {
        timer = setTimeout(() => resolve([]), 2_000)
      })
    ])
  } catch {
    return []
  } finally {
    clearTimeout(timer)
  }
}

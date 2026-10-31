import { useAuiState } from '@assistant-ui/react'
import { useMemo, useRef } from 'react'

interface TranscriptMessageIdentity {
  id: string
  rowId?: number
}

export interface TranscriptMessageKeyCache {
  byMessageId: Map<string, string>
  byRowId: Map<number, string>
}

export function createTranscriptMessageKeyCache(): TranscriptMessageKeyCache {
  return { byMessageId: new Map(), byRowId: new Map() }
}

export function transcriptMessageKeys(
  messages: readonly TranscriptMessageIdentity[],
  cache: TranscriptMessageKeyCache
): string[] {
  const seen = new Set<string>()

  // Keep the first renderer key when persistence adds a row id, then carry it
  // across later hydrations that rebuild a positional renderer id.
  return messages.map(message => {
    const durableKey =
      message.rowId === undefined ? undefined : (cache.byRowId.get(message.rowId) ?? `row:${message.rowId}`)

    const messageKey = `message:${message.id}`
    const key = cache.byMessageId.get(message.id) ?? durableKey ?? messageKey
    const uniqueKey = seen.has(key) ? messageKey : key

    seen.add(uniqueKey)
    cache.byMessageId.set(message.id, uniqueKey)

    if (message.rowId !== undefined && !cache.byRowId.has(message.rowId)) {
      cache.byRowId.set(message.rowId, uniqueKey)
    }

    return uniqueKey
  })
}

export function useTranscriptMessageKeys(): string[] {
  const signature = useAuiState(s =>
    JSON.stringify(
      s.thread.messages.map(message => ({
        id: message.id,
        rowId: typeof message.metadata.custom.rowId === 'number' ? message.metadata.custom.rowId : undefined
      }))
    )
  )

  const cache = useRef(createTranscriptMessageKeyCache())

  return useMemo(() => transcriptMessageKeys(JSON.parse(signature), cache.current), [signature])
}

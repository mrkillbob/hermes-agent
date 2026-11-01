import { useAuiState } from '@assistant-ui/react'
import { useMemo } from 'react'

interface TranscriptMessageIdentity {
  id: string
  rowId?: number
}

interface MessageKeyRecord {
  key: string
  rowId?: number
}

export interface TranscriptMessageKeyCache {
  byMessageId: Map<string, MessageKeyRecord>
  byRowId: Map<number, string>
  nextKey: number
}

interface TranscriptMessageScope {
  profile?: string
  sessionId?: null | string
  sessionKey?: null | string
}

export function createTranscriptMessageKeyCache(): TranscriptMessageKeyCache {
  return { byMessageId: new Map(), byRowId: new Map(), nextKey: 0 }
}

function previousIdentity(message: TranscriptMessageIdentity, cache: TranscriptMessageKeyCache) {
  const previous = cache.byMessageId.get(message.id)

  if (!previous) {
    return undefined
  }

  if (message.rowId !== undefined) {
    if (previous.rowId !== undefined && previous.rowId !== message.rowId) {
      return undefined
    }

    const durableKey = cache.byRowId.get(message.rowId)

    // A positional renderer alias cannot override an already-known durable row.
    if (previous.rowId === undefined && durableKey !== undefined && durableKey !== previous.key) {
      return undefined
    }
  }

  return previous
}

export function transcriptMessageKeys(
  messages: readonly TranscriptMessageIdentity[],
  cache: TranscriptMessageKeyCache
): string[] {
  const previous = messages.map(message => previousIdentity(message, cache))
  const reserved = new Map<string, string>()
  const seen = new Set<string>()
  const byMessageId = new Map<string, MessageKeyRecord>()
  const byRowId = new Map<number, string>()

  // Existing fragments keep their own identity before new fragments can claim
  // the shared durable-row fallback. Read only the preceding snapshot here.
  previous.forEach((record, index) => {
    if (record) {
      reserved.set(record.key, messages[index]!.id)
    }
  })

  const keys = messages.map((message, index) => {
    const record = previous[index]
    const rowId = message.rowId ?? record?.rowId
    const preferred = record?.key ?? (rowId === undefined ? undefined : cache.byRowId.get(rowId))
    const reservedBy = preferred === undefined ? undefined : reserved.get(preferred)

    const key =
      preferred !== undefined && !seen.has(preferred) && (reservedBy === undefined || reservedBy === message.id)
        ? preferred
        : `identity:${cache.nextKey++}`

    seen.add(key)
    byMessageId.set(message.id, { key, rowId })

    if (rowId !== undefined && !byRowId.has(rowId)) {
      byRowId.set(rowId, key)
    }

    return key
  })

  // Retired pages and renderer aliases own no mounted state. Keep only the
  // active projection, with stable keys carried by its surviving durable rows.
  cache.byMessageId = byMessageId
  cache.byRowId = byRowId

  return keys
}

export function useTranscriptMessageKeys({ profile, sessionId, sessionKey }: TranscriptMessageScope = {}): string[] {
  const signature = useAuiState(s =>
    JSON.stringify(
      s.thread.messages.map(message => ({
        id: message.id,
        rowId: typeof message.metadata.custom.rowId === 'number' ? message.metadata.custom.rowId : undefined
      }))
    )
  )

  const scope = JSON.stringify([profile ?? null, sessionKey ?? sessionId ?? null])
  const identity = useMemo(() => ({ cache: createTranscriptMessageKeyCache(), scope }), [scope])

  return useMemo(
    () => transcriptMessageKeys(JSON.parse(signature), identity.cache).map(key => `${identity.scope}:${key}`),
    [identity, signature]
  )
}

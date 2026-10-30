import type { PersistedTurn } from '@hermes/shared'

import type { ChatMessage } from '@/lib/chat-messages'

/** A durable final receipt can settle an interim that has no stored row identity yet. */
export function isPersistedInterimCompletion(existing: ChatMessage, persistedTurn?: PersistedTurn | null): boolean {
  const finalRowId = persistedTurn?.final_assistant_row_id

  return (
    existing.interim === true &&
    existing.rowId === undefined &&
    typeof finalRowId === 'number' &&
    Number.isSafeInteger(finalRowId) &&
    finalRowId > 0
  )
}

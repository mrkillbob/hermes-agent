import type { ChatMessage } from '@/lib/chat-messages'
import type { SessionMessagesResponse, SessionResumeResult } from '@/types/hermes'

interface TranscriptTurnState {
  messages: ChatMessage[]
  busy?: boolean
  turnLive?: boolean
  awaitingResponse?: boolean
}

/** Freeze the viewed turn before resume clears or rebinds its runtime. */
export function captureReconnectTurn(
  runtimeId: string | null,
  states: ReadonlyMap<string, TranscriptTurnState>,
  messages: ChatMessage[]
): TranscriptTurnState {
  const state = runtimeId ? states.get(runtimeId) : undefined

  return { ...state, messages }
}

const unfinishedTurn = (state: TranscriptTurnState) =>
  state.busy ||
  state.turnLive ||
  state.awaitingResponse ||
  state.messages.some(message => message.role === 'assistant' && message.pending)

/** A pre-attach page cannot recover a turn that completed in the socket gap. */
export async function reconnectTranscriptPrefetch({
  prefetch,
  resume,
  previousState,
  currentState,
  read
}: {
  prefetch: Promise<SessionMessagesResponse>
  resume: Promise<SessionResumeResult>
  previousState: TranscriptTurnState
  currentState: (runtimeId: string) => TranscriptTurnState | undefined
  read: () => Promise<SessionMessagesResponse>
}): Promise<SessionMessagesResponse> {
  const page = await prefetch

  if (!unfinishedTurn(previousState)) {
    return page
  }

  const attached = await resume
  const current = currentState(attached.session_id)

  // A known settled cache was repaired by terminal delivery. A rebound runtime
  // with no cache has no such proof, so recover its history after attachment.
  // A failed recovery read must still retain the successful first page.
  return attached.running === false && (!current || unfinishedTurn(current)) ? read().catch(() => page) : page
}

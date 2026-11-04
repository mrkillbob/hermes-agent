/**
 * Reconciling a resume snapshot with reply text that streamed in before its ACK.
 *
 * A runtime first seen before `session.resume` answers already holds its live
 * rows, so the ACK-time state cannot serve as the merge baseline. The live
 * segment's first wire offset and the snapshot's reply length say what each
 * side covers: text starting at or after the snapshot's end is new, and text
 * starting earlier repeats what the snapshot already has.
 */

import { type ChatMessage, chatMessageText, concatPartsByToolCall } from '@/lib/chat-messages'
import type { SessionResumeResult } from '@/types/hermes'

import { clearLiveSegmentStart, liveSegmentStart } from '../use-message-stream/segment-identity'

/** Both offsets are UTF-16 units within the same, first, segment of the turn. */
export interface ResumeLiveCoverage {
  liveStart: number
  snapshotEnd: number
}

export interface ResumeBaseline {
  coverage?: ResumeLiveCoverage
  messages: ChatMessage[]
}

/**
 * The baseline the resume overlays concurrent live changes against. A runtime
 * frozen before dispatch keeps its frozen rows. A first-seen runtime with a
 * known first-segment start is baselined empty, with its coverage; any other
 * first-seen runtime keeps its ACK-time rows. One ACK consumes the start.
 */
export function resumeRuntimeBaseline(
  resumed: Pick<SessionResumeResult, 'inflight' | 'session_id'>,
  frozen: ReadonlyMap<string, ChatMessage[]>,
  runtimeStates: ReadonlyMap<string, { messages: ChatMessage[] }>
): ResumeBaseline {
  const known = frozen.get(resumed.session_id)

  if (known) {
    return { messages: known }
  }

  const start = liveSegmentStart(resumed.session_id)
  clearLiveSegmentStart(resumed.session_id)

  if (start?.segment === 1 && resumed.inflight) {
    return {
      coverage: { liveStart: start.start, snapshotEnd: resumed.inflight.assistant?.length ?? 0 },
      messages: []
    }
  }

  return { messages: runtimeStates.get(resumed.session_id)?.messages ?? [] }
}

function dropLeadingText(parts: ChatMessage['parts'], count: number): ChatMessage['parts'] {
  let left = count

  return parts.flatMap(part => {
    if (left <= 0 || part.type !== 'text') {
      return [part]
    }

    if (part.text.length <= left) {
      left -= part.text.length

      return []
    }

    const kept = { ...part, text: part.text.slice(left) }
    left = 0

    return [kept]
  })
}


/**
 * The row that takes the activation stream row's place when a live row of the
 * same turn replaces it: the live row, with the activation text kept as its
 * prefix unless the live text already starts with it, and with any part the
 * snapshot covers dropped from the live side.
 */
export function activationReplacement(
  activationStream: ChatMessage,
  current: ChatMessage,
  coverage?: ResumeLiveCoverage
): ChatMessage {
  const overlap = coverage ? Math.max(0, coverage.snapshotEnd - coverage.liveStart) : 0

  if (overlap) {
    return { ...current, parts: concatPartsByToolCall(activationStream.parts, dropLeadingText(current.parts, overlap)) }
  }

  const activationText = chatMessageText(activationStream)

  return activationText && !chatMessageText(current).startsWith(activationText)
    ? { ...current, parts: concatPartsByToolCall(activationStream.parts, current.parts) }
    : current
}

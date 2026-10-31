import { AssistantRuntimeProvider, type ThreadMessage, useAuiState } from '@assistant-ui/react'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { createContext, useContext, useLayoutEffect, useState } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { useRuntimeMessageRepository } from '@/app/chat/runtime-repository'
import { type ChatMessage, toChatMessages } from '@/lib/chat-messages'
import { useIncrementalExternalStoreRuntime } from '@/lib/incremental-external-store-runtime'
import type { SessionMessage } from '@/types/hermes'

import { stubThreadEnvironment, stubThreadViewportSize } from '../test-utils'

import { ThreadMessageList } from './list'

beforeEach(() => {
  stubThreadEnvironment()
  stubThreadViewportSize()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const CommitObserver = createContext<(owner: string, current: string) => void>(() => {})

// A stateful message consumer makes a changed scope observable: disclosure,
// selection and copied-feedback state must belong to the same durable row.
function BoundMessage() {
  const identity = useAuiState(s => {
    const rowId = s.message.metadata.custom.rowId

    return typeof rowId === 'number' ? `row:${rowId}` : s.message.id
  })
  const text = useAuiState(s =>
    s.message.content.flatMap(part => (part.type === 'text' ? [part.text] : [])).join('')
  )
  const [owner] = useState(identity)
  const [held, setHeld] = useState(false)
  const record = useContext(CommitObserver)

  useLayoutEffect(() => record(owner, identity), [identity, owner, record])

  return (
    <button aria-pressed={held} onClick={() => setHeld(value => !value)} type="button">
      hold {text}
    </button>
  )
}

const components = { Message: BoundMessage }

function Transcript({
  messages,
  record
}: {
  messages: ChatMessage[]
  record: (owner: string, current: string) => void
}) {
  const messageRepository = useRuntimeMessageRepository(messages)

  const runtime = useIncrementalExternalStoreRuntime<ThreadMessage>({
    messageRepository,
    isRunning: false,
    setMessages: () => {},
    onNew: async () => {},
    onCancel: async () => {}
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <CommitObserver.Provider value={record}>
        <ThreadMessageList
          clampToComposer={false}
          components={components}
          sessionId="scope-test"
          sessionKey="scope-test"
        />
      </CommitObserver.Provider>
    </AssistantRuntimeProvider>
  )
}

const initialRows: SessionMessage[] = [
  { role: 'user', content: 'Run the process', timestamp: 10, row_id: 10 },
  { role: 'assistant', content: 'Starting the process', timestamp: 11, row_id: 11 },
  { role: 'assistant', content: 'Noted the process', timestamp: 14, row_id: 14 },
  { role: 'user', content: 'Next question', timestamp: 15, row_id: 15 },
  { role: 'assistant', content: 'Next answer', timestamp: 16, row_id: 16 }
]

const processNotice: SessionMessage = {
  role: 'user',
  content: '[IMPORTANT: Background process proc_scope completed normally (exit code 0).\nOutput:\ncore-ran]',
  timestamp: 13,
  row_id: 13,
  display_kind: 'process_complete',
  display_metadata: { display_text: 'Background Process Finished: scope' }
}

it('keeps message-local state attached to its durable reply when hydration inserts a process notice', async () => {
  // Hydration changes the positional renderer id of row 14, not its backend
  // identity. A custom list keyed/scoped by position hands its state to row 13.
  // This reproduces the identity boundary, not the hosted trace's scheduling.
  const mismatches: string[] = []
  const record = (owner: string, current: string) => {
    if (owner !== current) {
      mismatches.push(`${owner} -> ${current}`)
    }
  }
  const { getByRole, queryByRole, rerender } = render(
    <Transcript messages={toChatMessages(initialRows)} record={record} />
  )
  const reply = () => getByRole('button', { name: 'hold Noted the process' })

  await waitFor(() => expect(reply()).not.toBeNull())
  fireEvent.click(reply())
  expect(reply().getAttribute('aria-pressed')).toBe('true')

  const refreshed = [...initialRows.slice(0, 2), processNotice, ...initialRows.slice(2)]
  rerender(<Transcript messages={toChatMessages(refreshed)} record={record} />)

  await waitFor(() =>
    expect(getByRole('button', { name: 'hold Background Process Finished: scope' })).not.toBeNull()
  )
  expect(reply().getAttribute('aria-pressed')).toBe('true')
  expect(
    getByRole('button', { name: 'hold Background Process Finished: scope' }).getAttribute('aria-pressed')
  ).toBe('false')
  expect(getByRole('button', { name: 'hold Next answer' }).getAttribute('aria-pressed')).toBe('false')
  expect(mismatches).toEqual([])

  // Retire the inserted id and then the whole response; stale providers must
  // disappear instead of borrowing the next row's content or local state.
  rerender(<Transcript messages={toChatMessages(initialRows)} record={record} />)
  await waitFor(() =>
    expect(queryByRole('button', { name: 'hold Background Process Finished: scope' })).toBeNull()
  )
  expect(reply().getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(getByRole('button', { name: 'hold Next answer' }))
  expect(getByRole('button', { name: 'hold Next answer' }).getAttribute('aria-pressed')).toBe('true')
  rerender(<Transcript messages={toChatMessages(initialRows.slice(3))} record={record} />)
  await waitFor(() => expect(queryByRole('button', { name: 'hold Noted the process' })).toBeNull())
  expect(getByRole('button', { name: 'hold Next answer' }).getAttribute('aria-pressed')).toBe('true')
  expect(mismatches).toEqual([])
})

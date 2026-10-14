import { useAuiState } from '@assistant-ui/react'
import type { GatewayEvent } from '@hermes/shared'
import { useStore } from '@nanostores/react'
import { QueryClient } from '@tanstack/react-query'
import { act, cleanup, render, screen } from '@testing-library/react'
import { useCallback, useRef } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { ChatRuntimeBoundary } from '@/app/chat'
import { PRIMARY_SESSION_VIEW } from '@/app/chat/session-view'
import { mergeOlderTranscriptPage } from '@/app/chat/transcript-backfill'
import { useMessageStream } from '@/app/session/hooks/use-message-stream'
import { useSessionStateCache } from '@/app/session/hooks/use-session-state-cache'
import { stubThreadEnvironment } from '@/components/assistant-ui/test-utils'
import { getLatestSessionMessages } from '@/hermes'
import { chatMessageText, toChatMessages } from '@/lib/chat-messages'
import { resetLiveSync } from '@/store/live-sync'
import {
  $busy,
  setActiveSessionId,
  setAwaitingResponse,
  setBusy,
  setMessages,
  setSelectedStoredSessionId
} from '@/store/session'
import { $sessionStates, $sessionTiles, clearAllSessionStates } from '@/store/session-states'
import {
  $retainedTodosBySession,
  $todosBySession,
  clearSessionTodos,
  restoreSessionTodosFromSnapshot,
  setSessionTodos
} from '@/store/todos'
import type { SessionMessage } from '@/types/hermes'

import {
  hydrateStoredSessionTranscript,
  reconcileActiveTranscript,
  reconcileTileTranscripts,
  useBackgroundSync
} from './use-background-sync'

vi.mock('@/hermes', async original => ({
  ...(await original<Record<string, unknown>>()),
  getLatestSessionMessages: vi.fn()
}))

stubThreadEnvironment()

const RUNTIME = 'refresh-runtime'
const STORED = 'refresh-stored'
const FINAL = 'The completed answer must stay visible.'

const history: SessionMessage[] = [
  { id: 1, role: 'user', content: 'Earlier question', timestamp: 1 },
  { id: 2, role: 'assistant', content: 'Earlier answer', timestamp: 2 },
  { id: 3, role: 'user', content: 'Latest question', timestamp: 3 }
]

const toolRound: SessionMessage[] = [
  ...history,
  {
    id: 4,
    role: 'assistant',
    content: '',
    timestamp: 4,
    tool_calls: [
      { id: 'read-tool', type: 'function', function: { name: 'read_file', arguments: '{"path":"example.txt"}' } }
    ]
  },
  { id: 5, role: 'tool', content: 'example content', timestamp: 5, tool_call_id: 'read-tool', tool_name: 'read_file' }
]

const completeHistory: SessionMessage[] = [...toolRound, { id: 6, role: 'assistant', content: FINAL, timestamp: 6 }]
const noop = async () => undefined
let cache: ReturnType<typeof useSessionStateCache>
let stream: ReturnType<typeof useMessageStream>
let refresh: () => Promise<void>

function ObserveRuntime() {
  const messages = useAuiState(state => state.thread.messages)

  return (
    <output data-testid="runtime">
      {messages
        .map(message =>
          message.content
            .filter(part => part.type === 'text')
            .map(part => part.text)
            .join('')
        )
        .join('\n')}
    </output>
  )
}

function Harness({
  backgroundSync = false,
  tile = false,
  fallback = false
}: {
  backgroundSync?: boolean
  tile?: boolean
  fallback?: boolean
}) {
  const busyRef = useRef(false)
  const queryClient = useRef(new QueryClient()).current
  const requestSequenceRef = useRef(0)
  const signatureRef = useRef(new Map<string, string>())
  cache = useSessionStateCache({
    activeSessionId: tile ? 'other-runtime' : RUNTIME,
    selectedStoredSessionId: tile ? 'other-stored' : STORED,
    busyRef,
    setBusy,
    setAwaitingResponse,
    setMessages
  })
  const { activeSessionIdRef, selectedStoredSessionIdRef, updateSessionState } = cache

  const hydrate = useCallback(
    async (attempts = 1, storedSessionId: string | null = STORED, runtimeSessionId: string | null = RUNTIME) => {
      return storedSessionId && runtimeSessionId
        ? hydrateStoredSessionTranscript({
            attempts,
            storedSessionId,
            runtimeSessionId,
            storedProfile: 'default',
            updateSessionState
          })
        : false
    },
    [updateSessionState]
  )

  stream = useMessageStream({
    ...cache,
    queryClient,
    hydrateFromStoredSession: fallback ? hydrate : noop,
    refreshHermesConfig: noop,
    refreshSessions: noop
  })
  refresh = useCallback(
    () =>
      tile
        ? reconcileTileTranscripts({
            requestSequenceRef,
            signatureRef,
            updateSessionState,
            tiles: [{ runtimeId: RUNTIME, storedSessionId: STORED }]
          })
        : reconcileActiveTranscript({
            activeSessionIdRef,
            selectedStoredSessionIdRef,
            updateSessionState,
            busyRef,
            requestSequenceRef,
            signatureRef,
            resolveSession: () => ({ profile: 'default' })
          }),
    [activeSessionIdRef, selectedStoredSessionIdRef, updateSessionState, tile]
  )
  useBackgroundSync({
    activeConnectionId: 'local',
    activeGatewayProfile: 'default',
    activeIsMessaging: false,
    activeSessionId: tile ? 'other-runtime' : RUNTIME,
    activeStoredSessionId: tile ? 'other-stored' : STORED,
    freshDraftReady: false,
    gatewayState: backgroundSync ? 'open' : 'closed',
    refreshActiveTranscript: refresh,
    refreshCronJobs: noop,
    refreshCurrentModel: noop,
    refreshHermesConfig: noop,
    refreshMessagingSessions: noop,
    refreshSessions: noop,
    requestGateway: async () => ({ sessions: [] }) as never,
    updateSessionState: cache.updateSessionState
  })
  const busy = useStore(PRIMARY_SESSION_VIEW.$busy)

  return (
    <ChatRuntimeBoundary
      busy={busy}
      onCancel={noop}
      onEdit={noop}
      onReload={noop}
      onThreadMessagesChange={noop}
      suppressMessages={false}
    >
      <ObserveRuntime />
    </ChatRuntimeBoundary>
  )
}

function send(type: GatewayEvent['type'], payload: GatewayEvent['payload'] = {}) {
  act(() => stream.handleGatewayEvent({ session_id: RUNTIME, type, payload }))
}

function deferredHistory() {
  let resolve!: (page: Awaited<ReturnType<typeof getLatestSessionMessages>>) => void

  const promise = new Promise<Awaited<ReturnType<typeof getLatestSessionMessages>>>(yes => {
    resolve = yes
  })

  return { promise, resolve }
}

async function finishTurn(persistedTurn?: {
  row_ids: number[]
  user_row_id: number
  final_assistant_row_id: number
  complete: boolean
}) {
  send('message.start')
  send('tool.start', { name: 'read_file', tool_id: 'read-tool', args: { path: 'example.txt' } })
  send('tool.complete', { name: 'read_file', tool_id: 'read-tool', result: 'example content' })
  send('message.delta', { text: FINAL })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100)
  })
  send('message.complete', { text: FINAL, persisted_turn: persistedTurn })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(getLatestSessionMessages).mockReset()
  setActiveSessionId(RUNTIME)
  setSelectedStoredSessionId(STORED)
})

afterEach(() => {
  cleanup()
  clearSessionTodos(RUNTIME)
  clearAllSessionStates()
  $sessionTiles.set([])
  resetLiveSync()
  setActiveSessionId(null)
  setSelectedStoredSessionId(null)
  setBusy(false)
  setAwaitingResponse(false)
  setMessages([])
  localStorage.clear()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

it('keeps an idle Todo snapshot for review when post-turn history refresh clears the live panel', async () => {
  const todos = [{ id: 'next', content: 'Next task', status: 'in_progress' as const }]

  const saved: SessionMessage[] = [
    ...history,
    {
      id: 4,
      role: 'assistant',
      content: '',
      timestamp: 4,
      tool_calls: [{ id: 'todo-call', type: 'function', function: { name: 'todo_list', arguments: '{}' } }]
    },
    {
      id: 5,
      role: 'tool',
      content: JSON.stringify({ todos, revision: 3 }),
      timestamp: 5,
      tool_call_id: 'todo-call',
      tool_name: 'todo_list'
    },
    { id: 6, role: 'assistant', content: FINAL, timestamp: 6 }
  ]

  render(<Harness />)
  act(() => restoreSessionTodosFromSnapshot(RUNTIME, { todos, revision: 3 }, false))
  expect($retainedTodosBySession.get()[RUNTIME]).toEqual(todos)
  vi.mocked(getLatestSessionMessages).mockResolvedValueOnce({ session_id: STORED, messages: saved })

  await act(async () => {
    await hydrateStoredSessionTranscript({
      attempts: 1,
      storedSessionId: STORED,
      runtimeSessionId: RUNTIME,
      storedProfile: 'default',
      updateSessionState: cache.updateSessionState
    })
  })
  expect($todosBySession.get()[RUNTIME]).toBeUndefined()
  expect($retainedTodosBySession.get()[RUNTIME]).toEqual(todos)

  // Deferred Desktop resumes initially return no todo_state. The subsequent
  // persisted transcript fetch must provide the same read-only list by itself.
  act(() => clearSessionTodos(RUNTIME))
  vi.mocked(getLatestSessionMessages).mockResolvedValueOnce({ session_id: STORED, messages: saved })
  await act(async () => {
    await hydrateStoredSessionTranscript({
      attempts: 1,
      storedSessionId: STORED,
      runtimeSessionId: RUNTIME,
      storedProfile: 'default',
      updateSessionState: cache.updateSessionState
    })
  })
  expect($retainedTodosBySession.get()[RUNTIME]).toEqual(todos)

  // A legacy unversioned empty result is still an explicit clear. An older
  // revisioned snapshot must not keep the previous button pinned.
  const cleared: SessionMessage[] = [
    ...saved,
    {
      id: 7,
      role: 'assistant',
      content: '',
      timestamp: 7,
      tool_calls: [{ id: 'clear-call', type: 'function', function: { name: 'todo_list', arguments: '{"todos":[]}' } }]
    },
    { id: 8, role: 'tool', content: '{"todos":[]}', timestamp: 8, tool_call_id: 'clear-call', tool_name: 'todo_list' }
  ]

  vi.mocked(getLatestSessionMessages).mockResolvedValueOnce({ session_id: STORED, messages: cleared })
  await act(async () => {
    await hydrateStoredSessionTranscript({
      attempts: 1,
      storedSessionId: STORED,
      runtimeSessionId: RUNTIME,
      storedProfile: 'default',
      updateSessionState: cache.updateSessionState
    })
  })
  expect($retainedTodosBySession.get()[RUNTIME]).toBeUndefined()
})

it.each([false, true])(
  'rejects a history read spanning a completed turn, then accepts fresh history (tile: %s)',
  async tile => {
    if (tile) {
      setActiveSessionId('other-runtime')
      setSelectedStoredSessionId('other-stored')
      $sessionTiles.set([{ runtimeId: RUNTIME, storedSessionId: STORED }])
    }

    render(<Harness tile={tile} />)
    act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
    const delayed = deferredHistory()
    vi.mocked(getLatestSessionMessages).mockReturnValueOnce(delayed.promise)
    const pending = refresh()
    await finishTurn()
    const completed = $sessionStates.get()[RUNTIME].messages
    expect(completed.map(chatMessageText)).toContain(FINAL)

    if (!tile) {
      expect(screen.getByTestId('runtime').textContent).toContain(FINAL)
    }

    await act(async () => {
      delayed.resolve({ session_id: STORED, messages: toolRound })
      await pending
    })
    expect.soft($sessionStates.get()[RUNTIME].messages).toBe(completed)

    if (!tile) {
      expect.soft(screen.getByTestId('runtime').textContent).toContain(FINAL)
    }

    vi.mocked(getLatestSessionMessages).mockResolvedValueOnce({ session_id: STORED, messages: completeHistory })
    await act(async () => {
      await refresh()
    })
    const texts = $sessionStates.get()[RUNTIME].messages.map(chatMessageText)
    expect(texts.filter(text => text.includes(FINAL))).toHaveLength(1)
    expect(texts).toContain('Earlier answer')
  }
)

it.each([false, true])('keeps a completed tool card when a text-only refresh lands (tile: %s)', async tile => {
  if (tile) {
    setActiveSessionId('other-runtime')
    setSelectedStoredSessionId('other-stored')
    $sessionTiles.set([{ runtimeId: RUNTIME, storedSessionId: STORED }])
  }

  render(<Harness tile={tile} />)
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
  await finishTurn({ row_ids: [3, 4, 5, 6], user_row_id: 3, final_assistant_row_id: 6, complete: true })
  const beforeRefresh = $sessionStates.get()[RUNTIME].messages.flatMap(message => message.parts)
  expect(beforeRefresh.some(part => part.type === 'tool-call')).toBe(true)

  vi.mocked(getLatestSessionMessages).mockResolvedValueOnce({
    session_id: STORED,
    messages: [...history, { id: 6, role: 'assistant', content: FINAL, timestamp: 6 }]
  })
  await act(async () => {
    await refresh()
  })

  const afterRefresh = $sessionStates.get()[RUNTIME].messages.flatMap(message => message.parts)
  expect(afterRefresh.filter(part => part.type === 'tool-call')).toMatchObject([
    { toolCallId: 'read-tool', result: 'example content' }
  ])
})

it('retries an interrupted reconnect read once on idle, even before that read returns', async () => {
  const stale = deferredHistory()
  const fresh = deferredHistory()
  vi.mocked(getLatestSessionMessages).mockReturnValueOnce(stale.promise).mockReturnValueOnce(fresh.promise)
  render(<Harness backgroundSync />)
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
  await finishTurn()
  expect.soft(getLatestSessionMessages).toHaveBeenCalledTimes(2)

  await act(async () => {
    fresh.resolve({ session_id: STORED, messages: completeHistory })
    await fresh.promise
    stale.resolve({ session_id: STORED, messages: toolRound })
    await stale.promise
  })
  expect.soft(screen.getByTestId('runtime').textContent).toContain(FINAL)
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000)
  })
  expect.soft(getLatestSessionMessages).toHaveBeenCalledTimes(2)
  const texts = $sessionStates.get()[RUNTIME].messages.map(chatMessageText)
  expect(texts.filter(text => text.includes(FINAL))).toHaveLength(1)
})

it.each([
  { tile: false, mutation: 'backfill' },
  { tile: true, mutation: 'backfill' },
  { tile: false, mutation: 'retention' },
  { tile: true, mutation: 'retention' }
])('accepts an external answer during prefix-only $mutation (tile: $tile)', async ({ tile, mutation }) => {
  if (tile) {
    setActiveSessionId('other-runtime')
    setSelectedStoredSessionId('other-stored')
    $sessionTiles.set([{ runtimeId: RUNTIME, storedSessionId: STORED }])
  }

  render(<Harness tile={tile} />)
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
  const delayed = deferredHistory()
  vi.mocked(getLatestSessionMessages).mockReturnValueOnce(delayed.promise)
  const pending = refresh()
  const older = toChatMessages([{ id: 0, role: 'assistant', content: 'Backfilled answer', timestamp: 0 }])
  act(() =>
    cache.updateSessionState(
      RUNTIME,
      state => ({
        ...state,
        // Retention releases a prefix with slice; backfill prepends older rows.
        // Neither changes the existing tail nor emits a busy edge.
        messages: mutation === 'backfill' ? mergeOlderTranscriptPage(state.messages, older) : state.messages.slice(1)
      }),
      STORED
    )
  )
  await act(async () => {
    delayed.resolve({
      session_id: STORED,
      messages: [...history, { id: 4, role: 'assistant', content: 'External answer', timestamp: 4 }]
    })
    await pending
  })
  const texts = $sessionStates.get()[RUNTIME].messages.map(chatMessageText)
  expect(texts).toContain('External answer')
  expect(texts).toContain('Earlier answer')

  if (mutation === 'backfill') {
    expect(texts).toContain('Backfilled answer')
  }

  expect(getLatestSessionMessages).toHaveBeenCalledTimes(1)
})

it('rejects a read when an existing row changes even if the last row is untouched', async () => {
  render(<Harness />)
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
  const delayed = deferredHistory()
  vi.mocked(getLatestSessionMessages).mockReturnValueOnce(delayed.promise)
  const pending = refresh()
  act(() =>
    cache.updateSessionState(
      RUNTIME,
      state => ({
        ...state,
        messages: state.messages.map((message, index) =>
          index === 1 ? { ...message, parts: [{ type: 'text' as const, text: 'Edited answer' }] } : message
        )
      }),
      STORED
    )
  )
  await act(async () => {
    delayed.resolve({ session_id: STORED, messages: history })
    await pending
  })
  expect($sessionStates.get()[RUNTIME].messages.map(chatMessageText)).toContain('Edited answer')
})

it('keeps the reply and retries when a complete turn fits between view animation frames', async () => {
  const stale = deferredHistory()
  vi.mocked(getLatestSessionMessages)
    .mockReturnValueOnce(stale.promise)
    .mockResolvedValue({ session_id: STORED, messages: completeHistory })
  render(<Harness backgroundSync />)
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
  const busyEdges: boolean[] = []
  const unsubscribe = $busy.listen(value => busyEdges.push(value))
  await act(async () => {
    stream.handleGatewayEvent({ session_id: RUNTIME, type: 'message.start', payload: {} })
    stream.handleGatewayEvent({ session_id: RUNTIME, type: 'message.delta', payload: { text: FINAL } })
    stream.handleGatewayEvent({ session_id: RUNTIME, type: 'message.complete', payload: { text: FINAL } })
  })
  unsubscribe()
  expect(busyEdges).not.toContain(true)
  expect(screen.getByTestId('runtime').textContent).toContain(FINAL)
  expect.soft(getLatestSessionMessages).toHaveBeenCalledTimes(2)
  await act(async () => {
    stale.resolve({ session_id: STORED, messages: toolRound })
    await stale.promise
  })
  expect(screen.getByTestId('runtime').textContent).toContain(FINAL)
  expect(getLatestSessionMessages).toHaveBeenCalledTimes(2)
})

it('rejects post-turn fallback history overtaken by another completed turn, including its todo restore', async () => {
  render(<Harness fallback />)
  act(() =>
    cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history.slice(-1)) }), STORED)
  )
  const stale = deferredHistory()
  vi.mocked(getLatestSessionMessages).mockReturnValueOnce(stale.promise)
  send('message.start')
  send('message.complete')
  expect(getLatestSessionMessages).toHaveBeenCalledTimes(1)
  await finishTurn()
  act(() => setSessionTodos(RUNTIME, [{ id: 'new-todo', content: 'New turn todo', status: 'pending' }]))
  await act(async () => {
    stale.resolve({ session_id: STORED, messages: toolRound })
    await stale.promise
  })
  expect.soft(screen.getByTestId('runtime').textContent).toContain(FINAL)
  expect($todosBySession.get()[RUNTIME]).toEqual([{ id: 'new-todo', content: 'New turn todo', status: 'pending' }])
})

it('still hydrates a missing completion after a transient read failure', async () => {
  render(<Harness fallback />)
  act(() =>
    cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history.slice(-1)) }), STORED)
  )
  vi.mocked(getLatestSessionMessages)
    .mockRejectedValueOnce(new Error('transient read failure'))
    .mockResolvedValueOnce({ session_id: STORED, messages: completeHistory })
  send('message.start')
  send('message.complete')
  await act(async () => {
    await vi.advanceTimersByTimeAsync(300)
  })
  expect(getLatestSessionMessages).toHaveBeenCalledTimes(2)
  expect(screen.getByTestId('runtime').textContent).toContain(FINAL)
})

it.each(['unmount', 'navigation'] as const)('retires an interrupted refresh on %s', async mode => {
  const stale = deferredHistory()
  vi.mocked(getLatestSessionMessages).mockReturnValueOnce(stale.promise)
  const view = render(<Harness backgroundSync />)
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
  send('message.start')

  if (mode === 'unmount') {
    view.unmount()
  } else {
    act(() => {
      setActiveSessionId('other-runtime')
      setSelectedStoredSessionId('other-stored')
    })
    view.rerender(<Harness backgroundSync tile />)
  }

  await finishTurn()
  await act(async () => {
    stale.resolve({ session_id: STORED, messages: toolRound })
    await stale.promise
  })

  if (mode === 'navigation') {
    act(() => {
      setActiveSessionId(RUNTIME)
      setSelectedStoredSessionId(STORED)
    })
    view.rerender(<Harness backgroundSync />)
    await finishTurn()
  }

  expect(getLatestSessionMessages).toHaveBeenCalledTimes(1)
})

it('does not let an older finalizer retire the newest refresh observation', async () => {
  const stale = deferredHistory()
  const replacement = deferredHistory()
  vi.mocked(getLatestSessionMessages)
    .mockReturnValueOnce(stale.promise)
    .mockReturnValueOnce(replacement.promise)
    .mockResolvedValue({ session_id: STORED, messages: completeHistory })
  const view = render(<Harness backgroundSync />)
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
  // Close/reopen creates a fresh scoped connect read while the old one waits.
  view.rerender(<Harness />)
  view.rerender(<Harness backgroundSync />)
  expect(getLatestSessionMessages).toHaveBeenCalledTimes(2)
  await act(async () => {
    stale.resolve({ session_id: STORED, messages: history })
    await stale.promise
  })
  await finishTurn()
  await act(async () => {
    replacement.resolve({ session_id: STORED, messages: toolRound })
    await replacement.promise
  })
  expect(getLatestSessionMessages).toHaveBeenCalledTimes(3)
  expect(screen.getByTestId('runtime').textContent).toContain(FINAL)
})

it('catches up a retained prefix from the terminal transition alone when completion was lost', async () => {
  render(<Harness fallback />)
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
  send('message.start')
  send('message.delta', { text: 'The completed' })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100)
  })
  // The only session.info an idle foreground session is guaranteed to emit.
  send('session.info', { running: false, stored_session_id: STORED })
  expect(vi.mocked(getLatestSessionMessages)).not.toHaveBeenCalled()
  vi.mocked(getLatestSessionMessages).mockResolvedValue({
    session_id: STORED,
    messages: [...history, { id: 4, role: 'assistant', content: FINAL, timestamp: 4 }]
  })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2500)
  })
  expect(getLatestSessionMessages).toHaveBeenCalledTimes(1)
  expect(screen.getByTestId('runtime').textContent).toContain(FINAL)
  send('session.info', { running: false, stored_session_id: STORED })
  expect(getLatestSessionMessages).toHaveBeenCalledTimes(1)
  send('message.complete', { text: FINAL })
  expect(
    $sessionStates.get()[RUNTIME].messages.filter(message => chatMessageText(message).includes(FINAL))
  ).toHaveLength(1)
})

it('cancels retained-prefix catch-up when the delayed completion arrives', async () => {
  render(<Harness fallback />)
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
  send('message.start')
  send('message.delta', { text: 'The completed' })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100)
  })
  send('session.info', { running: false, stored_session_id: STORED })
  send('message.complete', { text: FINAL })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2500)
  })
  send('session.info', { running: false, stored_session_id: STORED })
  expect(getLatestSessionMessages).not.toHaveBeenCalled()
  expect(screen.getByTestId('runtime').textContent).toContain(FINAL)
})

it('waits for reorder grace and keeps background prefix recovery unconsumed', async () => {
  render(<Harness fallback />)
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
  send('message.start')
  send('message.delta', { text: 'The completed' })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100)
  })
  send('session.info', { running: false, stored_session_id: STORED })
  const settledAt = $sessionStates.get()[RUNTIME].heartbeatSettledAt
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000)
  })
  send('session.info', { running: false, stored_session_id: STORED })
  expect(getLatestSessionMessages).not.toHaveBeenCalled()
  act(() => {
    cache.activeSessionIdRef.current = 'other-runtime'
  })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1500)
  })
  send('session.info', { running: false, stored_session_id: STORED })
  expect(getLatestSessionMessages).not.toHaveBeenCalled()
  expect($sessionStates.get()[RUNTIME].heartbeatSettledAt).toBe(settledAt)
})

it('recovers a prefix settled in the background on the first idle heartbeat after the user returns', async () => {
  render(<Harness fallback />)
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
  send('message.start')
  send('message.delta', { text: 'The completed' })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100)
  })
  send('session.info', { running: false, stored_session_id: STORED })
  act(() => {
    cache.activeSessionIdRef.current = 'other-runtime'
  })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2500)
  })
  expect(getLatestSessionMessages).not.toHaveBeenCalled()
  vi.mocked(getLatestSessionMessages).mockResolvedValue({
    session_id: STORED,
    messages: [...history, { id: 4, role: 'assistant', content: FINAL, timestamp: 4 }]
  })
  act(() => {
    cache.activeSessionIdRef.current = RUNTIME
  })
  await act(async () => {
    stream.handleGatewayEvent({
      session_id: RUNTIME,
      type: 'session.info',
      payload: { running: false, stored_session_id: STORED }
    })
  })
  expect(getLatestSessionMessages).toHaveBeenCalledTimes(1)
  expect(screen.getByTestId('runtime').textContent).toContain(FINAL)
})

it('does not fetch the previous prefix after a new turn starts', async () => {
  render(<Harness fallback />)
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
  send('message.start')
  send('message.delta', { text: 'The completed' })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100)
  })
  send('session.info', { running: false, stored_session_id: STORED })
  send('message.start')
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2500)
  })
  send('session.info', { running: true, stored_session_id: STORED })
  expect(getLatestSessionMessages).not.toHaveBeenCalled()
  expect($sessionStates.get()[RUNTIME].heartbeatSettledStreamId).toBeNull()
})

it('does not overwrite a new turn with a delayed prefix catch-up result', async () => {
  render(<Harness fallback />)
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
  send('message.start')
  send('message.delta', { text: 'The completed' })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100)
  })
  send('session.info', { running: false, stored_session_id: STORED })
  const delayed = deferredHistory()
  vi.mocked(getLatestSessionMessages).mockReturnValueOnce(delayed.promise)
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2500)
  })
  send('session.info', { running: false, stored_session_id: STORED })
  expect(getLatestSessionMessages).toHaveBeenCalledTimes(1)
  send('message.start')
  send('message.delta', { text: 'New turn output.' })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100)
  })
  const newer = $sessionStates.get()[RUNTIME].messages
  await act(async () => {
    delayed.resolve({ session_id: STORED, messages: completeHistory })
  })
  expect($sessionStates.get()[RUNTIME].messages).toBe(newer)
  expect(screen.getByTestId('runtime').textContent).toContain('New turn output.')
})

it('retains the only streamed prefix when catch-up precedes the durable commit', async () => {
  render(<Harness fallback />)
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
  send('message.start')
  send('message.delta', { text: 'The completed' })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100)
  })
  send('session.info', { running: false, stored_session_id: STORED })
  vi.mocked(getLatestSessionMessages).mockResolvedValueOnce({ session_id: STORED, messages: history })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2500)
    stream.handleGatewayEvent({
      session_id: RUNTIME,
      type: 'session.info',
      payload: { running: false, stored_session_id: STORED }
    })
  })
  expect(getLatestSessionMessages).toHaveBeenCalledTimes(1)
  expect(screen.getByTestId('runtime').textContent).toContain('The completed')
  send('message.complete', { text: FINAL })
  expect(
    $sessionStates.get()[RUNTIME].messages.filter(message => chatMessageText(message).includes(FINAL))
  ).toHaveLength(1)
})

it('replaces the retained prefix with the durable answer instead of rendering both', async () => {
  render(<Harness fallback />)
  // An older backfilled page sits in front of the refreshed window.
  const backfilled = [{ id: 0, role: 'user' as const, content: 'Earlier question', timestamp: 0 }, ...history]
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(backfilled) }), STORED))
  send('message.start')
  send('message.delta', { text: 'The completed' })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100)
  })
  send('session.info', { running: false, stored_session_id: STORED })
  vi.mocked(getLatestSessionMessages).mockResolvedValue({
    session_id: STORED,
    messages: [...history, { id: 4, role: 'assistant', content: FINAL, timestamp: 4 }]
  })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2500)
  })
  const replies = $sessionStates.get()[RUNTIME].messages.filter(message => message.role === 'assistant')
  expect(replies.filter(message => chatMessageText(message).includes(FINAL))).toHaveLength(1)
  expect(replies.some(message => chatMessageText(message) === 'The completed')).toBe(false)
})

it('keeps the catch-up marker when every history read fails and recovers on a later heartbeat', async () => {
  render(<Harness fallback />)
  act(() => cache.updateSessionState(RUNTIME, state => ({ ...state, messages: toChatMessages(history) }), STORED))
  send('message.start')
  send('message.delta', { text: 'The completed' })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100)
  })
  send('session.info', { running: false, stored_session_id: STORED })
  const settledAt = $sessionStates.get()[RUNTIME].heartbeatSettledAt
  vi.mocked(getLatestSessionMessages).mockRejectedValue(new Error('rest unavailable'))
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000)
  })
  expect(getLatestSessionMessages).toHaveBeenCalledTimes(3)
  expect($sessionStates.get()[RUNTIME].heartbeatSettledAt).toBe(settledAt)
  vi.mocked(getLatestSessionMessages).mockReset()
  vi.mocked(getLatestSessionMessages).mockResolvedValue({
    session_id: STORED,
    messages: [...history, { id: 4, role: 'assistant', content: FINAL, timestamp: 4 }]
  })
  await act(async () => {
    stream.handleGatewayEvent({
      session_id: RUNTIME,
      type: 'session.info',
      payload: { running: false, stored_session_id: STORED }
    })
  })
  expect(getLatestSessionMessages).toHaveBeenCalledTimes(1)
  expect(screen.getByTestId('runtime').textContent).toContain(FINAL)
  expect($sessionStates.get()[RUNTIME].heartbeatSettledAt).toBeNull()
})

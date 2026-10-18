import { JsonRpcGatewayClient } from '@hermes/shared'
import { useStore } from '@nanostores/react'
import { QueryClient } from '@tanstack/react-query'
import { act, cleanup, render, waitFor } from '@testing-library/react'
import { useEffect, useRef } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { HermesGateway } from '@/api/client'
import { hydrateStoredSessionTranscript } from '@/app/contrib/hooks/use-background-sync'
import { getLatestSessionMessages, getSession } from '@/hermes'
import { chatMessageText, toChatMessages } from '@/lib/chat-messages'
import { pendingSessionReplay, requestGatewayForProfile } from '@/store/gateway'
import { $activeGatewayProfile } from '@/store/profile'
import {
  $activeSessionId,
  $messages,
  $selectedStoredSessionId,
  setActiveSessionId,
  setAwaitingResponse,
  setBusy,
  setMessages,
  setSelectedStoredSessionId,
  setSessions
} from '@/store/session'
import { clearAllSessionStates } from '@/store/session-states'

import { deferred } from '../../../../test/deferred'
import { useMessageStream } from '../use-message-stream'
import { useSessionStateCache } from '../use-session-state-cache'

import { useSessionActions } from './index'

vi.mock('@/hermes', async original => ({
  ...(await original<Record<string, unknown>>()),
  getSession: vi.fn(),
  getLatestSessionMessages: vi.fn()
}))
vi.mock('@/store/profile', async original => ({
  ...(await original<Record<string, unknown>>()),
  ensureGatewayAgent: vi.fn(async () => undefined),
  ensureGatewayProfile: vi.fn(async () => undefined)
}))
vi.mock('@/store/gateway', async original => ({
  ...(await original<Record<string, unknown>>()),
  pendingSessionReplay: vi.fn(),
  requestGatewayForProfile: vi.fn()
}))

const SID = 'reconnect-transcript'
const EPOCH = 'owner-session-epoch'
const PROMPT = 'across a drop'
const PREFIX = 'reply'
const FINAL = 'reply survives the drop'
const clients: HermesGateway[] = []
const queryClients: QueryClient[] = []
const unexpectedTransport = vi.fn(() => { throw new Error('Unexpected live transport in inert reconnect test') })

beforeEach(() => {
  unexpectedTransport.mockClear()
  vi.stubGlobal('fetch', unexpectedTransport)
  vi.stubGlobal('WebSocket', unexpectedTransport)
  vi.spyOn(JsonRpcGatewayClient.prototype, 'connect').mockResolvedValue()
  clearAllSessionStates()
  setActiveSessionId(SID)
  setSelectedStoredSessionId(SID)
  setSessions([{ id: SID, message_count: 1, profile: 'default' }] as never)
  setMessages([])
})
afterEach(() => {
  cleanup()

  for (const client of clients.splice(0)) { client.close() }

  for (const queryClient of queryClients.splice(0)) { queryClient.clear() }
  clearAllSessionStates()
  setActiveSessionId(null)
  setSelectedStoredSessionId(null)
  setMessages([])
  setSessions([])
  $activeGatewayProfile.set('default')
  expect(unexpectedTransport).not.toHaveBeenCalled()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

type Cache = ReturnType<typeof useSessionStateCache>
type Ready = Cache & Pick<ReturnType<typeof useSessionActions>, 'resumeSession'>

function Harness({ client, ready }: { client: HermesGateway; ready: (api: Ready) => void }) {
  const busyRef = useRef(false)
  const creatingSessionRef = useRef(false)
  const queryClientRef = useRef<QueryClient | null>(null)

  if (!queryClientRef.current) {
    queryClientRef.current = new QueryClient()
    queryClients.push(queryClientRef.current)
  }

  const cache = useSessionStateCache({
    activeSessionId: useStore($activeSessionId),
    selectedStoredSessionId: useStore($selectedStoredSessionId),
    busyRef,
    setAwaitingResponse,
    setBusy,
    setMessages
  })

  const actions = useSessionActions({
    ...cache,
    activeSessionId: SID,
    selectedStoredSessionId: SID,
    busyRef,
    creatingSessionRef,
    getRouteToken: () => `/${SID}::`,
    getRoutedStoredSessionId: () => SID,
    navigate: vi.fn() as never,
    requestGateway: client.request.bind(client),
    routedSessionId: SID
  })

  const stream = useMessageStream({
    activeSessionIdRef: cache.activeSessionIdRef,
    sessionStateByRuntimeIdRef: cache.sessionStateByRuntimeIdRef,
    updateSessionState: cache.updateSessionState,
    queryClient: queryClientRef.current,
    refreshHermesConfig: async () => undefined,
    refreshSessions: async () => undefined,
    hydrateFromStoredSession: (attempts = 1, stored = SID, runtime = SID) => hydrateStoredSessionTranscript({
      attempts,
      storedSessionId: stored ?? SID,
      runtimeSessionId: runtime ?? SID,
      storedProfile: 'default',
      updateSessionState: cache.updateSessionState
    })
  })

  useEffect(() => client.onAny(stream.handleGatewayEvent), [client, stream.handleGatewayEvent])
  useEffect(() => ready({ ...cache, resumeSession: actions.resumeSession }), [actions.resumeSession, cache, ready])

  return null
}

it.each<{
  label: string
  warm: boolean
  profile: string
  nextExecution?: { epoch: number; generation: number }
}>([
  { label: 'warm default post-ACK read', warm: true, profile: 'default' },
  { label: 'cold default pre-terminal prefetch', warm: false, profile: 'default' },
  { label: 'warm named primary post-ACK read', warm: true, profile: 'work' },
  { label: 'cold named primary pre-terminal prefetch', warm: false, profile: 'work' },
  { label: 'cold pre-ACK newer generation', warm: false, profile: 'default', nextExecution: { epoch: 1, generation: 8 } },
  { label: 'cold pre-ACK new owner epoch', warm: false, profile: 'work', nextExecution: { epoch: 2, generation: 1 } }
])('retains the final transcript with $label when terminal wire frames precede the reconnect attach ACK', async ({ warm, profile, nextExecution }) => {
  $activeGatewayProfile.set(profile)
  setSessions([{ id: SID, message_count: 1, profile }] as never)
  const ack = deferred<unknown>()
  const user = { id: 17, row_id: 17, role: 'user' as const, content: PROMPT, timestamp: 1 }
  const final = { id: 18, row_id: 18, role: 'assistant' as const, content: FINAL, timestamp: 2 }

  const base = {
    session_id: SID,
    stored_session_id: SID,
    replay_epoch: EPOCH,
    authority_epoch: 1,
    execution_generation: 6,
    revision: 6,
    last_sequence: 106,
    running: false,
    messages: [user],
    message_count: 1,
    pending: [],
    prompts: [],
    info: { profile_name: profile }
  }

  let reconnecting = false

  const wire = vi.spyOn(JsonRpcGatewayClient.prototype, 'request').mockImplementation(async (method) => {
    if (method === 'session.events.since') {
      return { events: [], replay_epoch: EPOCH, latest_seq: 113, snapshot_required: false, truncated: false } as never
    }

    if (method === 'session.resume') { return reconnecting ? await ack.promise as never : base as never }
    throw new Error(`Unexpected fixture method: ${method}`)
  })

  const client = new HermesGateway()
  clients.push(client)
  await client.connect('ws://localhost/api/ws?native_dial=fixture&ticket=fixture')
  await client.request('session.resume', { session_id: SID })
  vi.mocked(getSession).mockResolvedValue({ id: SID, profile, message_count: 1 } as never)
  vi.mocked(getLatestSessionMessages).mockResolvedValue({ session_id: SID, messages: warm ? [user, final] : [user] } as never)
  vi.mocked(requestGatewayForProfile).mockImplementation((_profile, method, params) => client.request(method, params) as never)
  vi.mocked(pendingSessionReplay).mockReturnValue(undefined)
  let api: Ready | undefined
  render(<Harness client={client} ready={value => { api = value }} />)
  await waitFor(() => expect(api).toBeDefined())
  act(() => api!.updateSessionState(SID, state => ({ ...state, storedSessionId: SID, messages: toChatMessages([user]) }), SID))

  const emit = (type: string, seq: number, payload: Record<string, unknown>, stamp = { epoch: 1, generation: 7 }) => act(() => {
    // Native SessionEvents.publish envelopes omit profile and stamp execution
    // beside payload. Decode the real frame before client owner normalization.
    const event = { type, session_id: SID, seq, replay_epoch: EPOCH, authority_epoch: stamp.epoch, execution_generation: stamp.generation, payload }
    const channel = (client as unknown as { channel: { handleFrame(text: string): void } }).channel
    channel.handleFrame(JSON.stringify({ jsonrpc: '2.0', method: 'event', params: event }))
  })

  emit('message.start', 108, {})
  emit('message.delta', 110, { text: PREFIX })
  await waitFor(() => expect($messages.get().map(chatMessageText)).toEqual([PROMPT, PREFIX]))

  reconnecting = true
  await client.connect('ws://localhost/api/ws?native_dial=fixture&ticket=reconnect')
  await client.request('session.events.since', { session_id: SID, last_seen: 113, replay_epoch: EPOCH })

  // A missing warm-runtime binding selects the cold restore path. Its REST
  // prefetch starts before the terminal and legitimately lacks the final row.
  if (!warm) { api!.runtimeIdByStoredSessionIdRef.current.delete(SID) }
  let resume: Promise<unknown> | undefined
  act(() => { resume = api!.resumeSession(SID, true) })
  await waitFor(() => expect(wire.mock.calls.filter(([method]) => method === 'session.resume')).toHaveLength(2))

  if (!warm) { await waitFor(() => expect(getLatestSessionMessages).toHaveBeenCalled()) }

  emit('session.info', 119, {})
  emit('message.complete', 120, {
    text: FINAL,
    status: 'complete',
    persisted_turn: { complete: true, final_assistant_row_id: 18, user_row_id: 17 }
  })
  emit('session.info', 121, { stored_session_id: SID, running: false, revision: 7, execution_generation: 7 })
  // A cold resume binds its foreground runtime only after the ACK; terminal
  // delivery must already update that runtime's shared cache in either path.
  expect(api!.sessionStateByRuntimeIdRef.current.get(SID)?.messages.map(chatMessageText)).toEqual([PROMPT, FINAL])

  if (nextExecution) {
    emit('message.start', 122, {}, nextExecution)
    emit('message.delta', 123, { text: 'next execution' }, nextExecution)
    await waitFor(() => expect(api!.sessionStateByRuntimeIdRef.current.get(SID)?.messages.map(chatMessageText)).toEqual([PROMPT, FINAL, 'next execution']))
  }

  await act(async () => {
    ack.resolve({ ...base, execution_generation: 7, last_sequence: 113, running: true })
    await resume
  })
  const expectedMessages = nextExecution ? [PROMPT, FINAL, 'next execution'] : [PROMPT, FINAL]
  expect(getLatestSessionMessages).toHaveBeenCalled()
  expect(api!.sessionStateByRuntimeIdRef.current.get(SID)).toMatchObject({ busy: Boolean(nextExecution), turnLive: Boolean(nextExecution) })
  expect(api!.sessionStateByRuntimeIdRef.current.get(SID)?.messages.map(chatMessageText)).toEqual(expectedMessages)
  expect($messages.get().map(chatMessageText)).toEqual(expectedMessages)
})

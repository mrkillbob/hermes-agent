import { act, cleanup, render, waitFor } from '@testing-library/react'
import { useEffect, useRef } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { CanonicalDesktopProtocol } from '@/api/canonical-protocol'
import { getLatestSessionMessages, getSession } from '@/hermes'
import { chatMessageText } from '@/lib/chat-messages'
import { createClientSessionState } from '@/lib/chat-runtime'
import { requestGatewayForProfile } from '@/store/gateway'
import { setActiveSessionId, setMessages, setSelectedStoredSessionId, setSessions } from '@/store/session'

import { deferred } from '../../../../test/deferred'
import type { ClientSessionState } from '../../../types'

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
  pendingSessionReplay: vi.fn(() => undefined),
  requestGatewayForProfile: vi.fn()
}))

const unexpectedTransport = vi.fn(() => {
  throw new Error('Unexpected live transport in inert resume test')
})

beforeEach(() => {
  unexpectedTransport.mockClear()
  vi.stubGlobal('fetch', unexpectedTransport)
  vi.stubGlobal('WebSocket', unexpectedTransport)
})

afterEach(() => {
  cleanup()
  setActiveSessionId(null)
  setSelectedStoredSessionId(null)
  setMessages([])
  setSessions([])
  expect(unexpectedTransport).not.toHaveBeenCalled()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function Harness({
  states,
  warm,
  request,
  ready
}: {
  states: Map<string, ClientSessionState>
  warm: boolean
  request: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
  ready: (resume: ReturnType<typeof useSessionActions>['resumeSession']) => void
}) {
  const stateMap = useRef(states)
  const activeSessionIdRef = useRef<string | null>(warm ? 'stored' : null)
  const busyRef = useRef(warm)
  const creatingSessionRef = useRef(false)
  const runtimeMap = useRef(new Map(warm ? [['stored', 'stored']] : []))
  const selectedRef = useRef<string | null>('stored')

  const actions = useSessionActions({
    activeSessionId: warm ? 'stored' : null,
    activeSessionIdRef,
    busyRef,
    creatingSessionRef,
    ensureSessionState: () => createClientSessionState('stored'),
    getRouteToken: () => '/stored::',
    getRoutedStoredSessionId: () => 'stored',
    navigate: vi.fn() as never,
    requestGateway: request,
    resetViewSync: vi.fn(),
    routedSessionId: 'stored',
    runtimeIdByStoredSessionIdRef: runtimeMap,
    selectedStoredSessionId: 'stored',
    selectedStoredSessionIdRef: selectedRef,
    sessionStateByRuntimeIdRef: stateMap,
    syncSessionStateToView: (_id, state) => setMessages(state.messages),
    updateSessionState: (id, update) => {
      const next = update(states.get(id) ?? createClientSessionState('stored'))
      states.set(id, next)

      return next
    }
  })

  useEffect(() => ready(actions.resumeSession), [actions.resumeSession, ready])

  return null
}

it.each([
  { label: 'warm activation', warm: true },
  { label: 'cold resume', warm: false }
])('keeps a terminal live frame delivered before the $label ACK', async ({ warm }) => {
  // The native owner attaches the event transport before awaiting inventory for
  // its ACK. The running snapshot can therefore arrive after its terminal event.
  const snapshot = deferred<unknown>()
  const protocol = new CanonicalDesktopProtocol()

  const partial = createClientSessionState('stored', [
    { id: 'user', role: 'user', parts: [{ type: 'text', text: 'prompt' }] },
    { id: 'assistant-stream-rt', role: 'assistant', parts: [{ type: 'text', text: 'prefix' }], pending: true }
  ])

  Object.assign(partial, {
    busy: true,
    turnLive: true,
    awaitingResponse: true,
    turnStartedAt: 123,
    streamId: 'assistant-stream-rt'
  })
  const states = new Map(warm ? [['stored', partial]] : [])

  const persisted = [
    { id: 1, role: 'user', content: 'prompt', timestamp: 1 },
    { id: 2, role: 'assistant', content: 'prefix complete answer', timestamp: 2 }
  ]

  vi.mocked(getSession).mockResolvedValue({ id: 'stored', message_count: 2 } as never)
  vi.mocked(getLatestSessionMessages).mockResolvedValue({ session_id: 'stored', messages: persisted } as never)

  const request = vi.fn((method: string, params?: Record<string, unknown>) =>
    method === (warm ? 'session.activate' : 'session.resume')
      ? snapshot.promise.then(value => protocol.result(method, params ?? {}, value))
      : Promise.resolve({})
  )

  vi.mocked(requestGatewayForProfile).mockImplementation((_profile, method, params) => request(method, params) as never)
  let resume: ReturnType<typeof useSessionActions>['resumeSession'] | undefined
  render(
    <Harness
      ready={value => {
        resume = value
      }}
      request={request as never}
      states={states}
      warm={warm}
    />
  )
  await waitFor(() => expect(resume).toBeDefined())
  let pending: Promise<unknown>
  act(() => {
    pending = resume!('stored', true)
  })
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(warm ? 'session.activate' : 'session.resume', expect.anything())
  )

  // This is the cache transition made by message.complete on the newly bound
  // transport, before the older snapshot's response frame arrives.
  protocol.event({
    type: 'message.start',
    session_id: 'stored',
    profile: 'default',
    authority_epoch: 7,
    execution_generation: 3,
    payload: {}
  })
  protocol.event({
    type: 'message.complete',
    session_id: 'stored',
    profile: 'default',
    authority_epoch: 7,
    execution_generation: 3,
    payload: { text: 'prefix complete answer' }
  })
  states.set('stored', {
    ...partial,
    messages: [
      partial.messages[0],
      { ...partial.messages[1], parts: [{ type: 'text', text: 'prefix complete answer' }], pending: false }
    ],
    busy: false,
    awaitingResponse: false,
    turnLive: false,
    turnStartedAt: null,
    streamId: null,
    sawAssistantPayload: true,
    adoptedRunningTurn: false
  })
  await act(async () => {
    snapshot.resolve({
      session_id: 'stored',
      stored_session_id: 'stored',
      running: true,
      authority_epoch: 7,
      execution_generation: 3,
      messages: persisted.map(row => (row.role === 'assistant' ? { ...row, content: 'prefix' } : row)),
      message_count: 2,
      info: {}
    })
    await pending
  })
  expect(states.get('stored')).toMatchObject({
    busy: false,
    awaitingResponse: false,
    turnLive: false,
    turnStartedAt: null
  })
  expect(states.get('stored')?.messages.map(chatMessageText)).toEqual(['prompt', 'prefix complete answer'])
})

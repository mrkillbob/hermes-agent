// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ClientSessionState } from '@/app/types'
import { $sessionStates } from '@/store/session-states'

import type { LeaderStateClipMap } from '../model'

import { LeaderDialogueRuntime } from './leader-dialogue-runtime'

const { requestForSessionProfile, voiceConversation } = vi.hoisted(() => ({
  requestForSessionProfile: vi.fn(async () => ({ status: 'queued' })),
  voiceConversation: {
    end: vi.fn(),
    start: vi.fn(),
    status: 'idle' as const,
    stopTurn: vi.fn()
  }
}))

vi.mock('@/store/session-request-router', () => ({ requestForSessionProfile }))
vi.mock('@/app/chat/composer/hooks/use-voice-conversation', () => ({
  useVoiceConversation: () => voiceConversation
}))

const clips: LeaderStateClipMap = {
  acknowledging: 'leader:owl:acknowledging',
  idle: 'leader:owl:idle',
  listening: 'leader:owl:listening',
  talking: 'leader:owl:talking',
  thinking: 'leader:owl:thinking',
  unavailable: 'leader:owl:unavailable'
}

function sessionState(overrides: Partial<ClientSessionState> = {}): ClientSessionState {
  return {
    awaitingResponse: false,
    busy: false,
    cwd: '',
    messages: [],
    ...overrides
  } as ClientSessionState
}

afterEach(() => {
  cleanup()
  $sessionStates.set({})
  vi.clearAllMocks()
})

describe('LeaderDialogueRuntime', () => {
  it('submits and interrupts only through the exact profile owner while projecting observed session state', async () => {
    const onLeaderStateChange = vi.fn()
    $sessionStates.set({ 'runtime-owl': sessionState() })

    render(
      <LeaderDialogueRuntime
        clips={clips}
        leaderLabel="owl leader"
        onClose={vi.fn()}
        onLeaderStateChange={onLeaderStateChange}
        onOpenFullChat={vi.fn()}
        owner={{ connectionId: 'source-a', profile: 'owl' }}
        session={{ runtimeId: 'runtime-owl', storedId: 'stored-owl' }}
        voiceAvailable={false}
      />
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'Message owl leader' }), { target: { value: 'Keep scope' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() =>
      expect(requestForSessionProfile).toHaveBeenCalledWith(
        { connectionId: 'source-a', profile: 'owl' },
        expect.any(Function),
        'prompt.submit',
        { session_id: 'runtime-owl', text: 'Keep scope' }
      )
    )

    $sessionStates.set({ 'runtime-owl': sessionState({ awaitingResponse: true, busy: true }) })

    await waitFor(() => expect(onLeaderStateChange).toHaveBeenLastCalledWith('thinking'))
    fireEvent.click(screen.getByRole('button', { name: 'Interrupt response' }))

    await waitFor(() =>
      expect(requestForSessionProfile).toHaveBeenCalledWith(
        { connectionId: 'source-a', profile: 'owl' },
        expect.any(Function),
        'session.interrupt',
        { session_id: 'runtime-owl' }
      )
    )
  })
})

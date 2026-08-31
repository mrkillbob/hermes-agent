// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ClientSessionState } from '@/app/types'
import { $sessionStates } from '@/store/session-states'

import type { LeaderStateClipMap } from '../model'

import { LeaderDialogueRuntime } from './leader-dialogue-runtime'

const { requestForSessionProfile, transcribeAudio, transcribeAudioClientDirect, voiceConversation, voiceHook } =
  vi.hoisted(() => ({
    requestForSessionProfile: vi.fn(async () => ({ status: 'queued' })),
    transcribeAudio: vi.fn(),
    transcribeAudioClientDirect: vi.fn(),
    voiceConversation: {
      end: vi.fn(),
      start: vi.fn(),
      status: 'idle' as const,
      stopTurn: vi.fn()
    },
    voiceHook: {
      onTranscribeAudio: undefined as undefined | ((audio: Blob) => Promise<string>)
    }
  }))

vi.mock('@/store/session-request-router', () => ({ requestForSessionProfile }))
vi.mock('@/app/chat/composer/hooks/use-voice-conversation', () => ({
  useVoiceConversation: (options: { onTranscribeAudio?: (audio: Blob) => Promise<string> }) => {
    voiceHook.onTranscribeAudio = options.onTranscribeAudio

    return voiceConversation
  }
}))
vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof import('@/hermes')>()),
  transcribeAudio
}))
vi.mock('@/lib/voice-client-direct', () => ({ transcribeAudioClientDirect }))

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

function deferred<T>() {
  let reject!: (error: unknown) => void
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done, fail) => {
    reject = fail
    resolve = done
  })

  return { promise, reject, resolve }
}

afterEach(() => {
  cleanup()
  $sessionStates.set({})
  voiceHook.onTranscribeAudio = undefined
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

  it('pins a leader capture to its immutable owner and ignores a stale completion after an active connection switch', async () => {
    const direct = deferred<null | string>()
    transcribeAudioClientDirect.mockReturnValue(direct.promise)
    $sessionStates.set({ shared: sessionState() })

    const view = render(
      <LeaderDialogueRuntime
        clips={clips}
        leaderLabel="owl leader"
        onClose={vi.fn()}
        onLeaderStateChange={vi.fn()}
        onOpenFullChat={vi.fn()}
        owner={{ connectionId: 'source-a', profile: 'owl' }}
        session={{ runtimeId: 'shared', storedId: 'stored-owl' }}
        voiceAvailable
      />
    )

    const staleTranscribe = voiceHook.onTranscribeAudio!
    const pending = staleTranscribe(new Blob(['audio'], { type: 'audio/webm' }))

    expect(transcribeAudioClientDirect).toHaveBeenCalledWith(
      expect.any(Blob),
      { connectionId: 'source-a', profile: 'owl' },
      expect.any(AbortSignal)
    )

    // The same runtime id now belongs to a different selected exact route.
    // The old capture must never continue against the new active source.
    view.rerender(
      <LeaderDialogueRuntime
        clips={clips}
        leaderLabel="fox leader"
        onClose={vi.fn()}
        onLeaderStateChange={vi.fn()}
        onOpenFullChat={vi.fn()}
        owner={{ connectionId: 'source-b', profile: 'fox' }}
        session={{ runtimeId: 'shared', storedId: 'stored-fox' }}
        voiceAvailable
      />
    )
    direct.resolve(null)

    await expect(pending).rejects.toThrow(/leader voice route changed/i)
    expect(transcribeAudio).not.toHaveBeenCalled()

    fireEvent.change(screen.getByRole('textbox', { name: 'Message fox leader' }), {
      target: { value: 'Text remains usable' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() =>
      expect(requestForSessionProfile).toHaveBeenCalledWith(
        { connectionId: 'source-b', profile: 'fox' },
        expect.any(Function),
        'prompt.submit',
        { session_id: 'shared', text: 'Text remains usable' }
      )
    )
  })

  it('ignores a relay transcript that completes after its exact leader route has been replaced', async () => {
    const relay = deferred<{ transcript: string }>()
    transcribeAudioClientDirect.mockResolvedValue(null)
    transcribeAudio.mockReturnValue(relay.promise)
    $sessionStates.set({ shared: sessionState() })

    const view = render(
      <LeaderDialogueRuntime
        clips={clips}
        leaderLabel="owl leader"
        onClose={vi.fn()}
        onLeaderStateChange={vi.fn()}
        onOpenFullChat={vi.fn()}
        owner={{ connectionId: 'source-a', profile: 'owl' }}
        session={{ runtimeId: 'shared', storedId: 'stored-owl' }}
        voiceAvailable
      />
    )

    const pending = voiceHook.onTranscribeAudio!(new Blob(['audio'], { type: 'audio/webm' }))

    await waitFor(() =>
      expect(transcribeAudio).toHaveBeenCalledWith(expect.any(String), 'audio/webm', {
        connectionId: 'source-a',
        profile: 'owl'
      })
    )

    view.rerender(
      <LeaderDialogueRuntime
        clips={clips}
        leaderLabel="fox leader"
        onClose={vi.fn()}
        onLeaderStateChange={vi.fn()}
        onOpenFullChat={vi.fn()}
        owner={{ connectionId: 'source-b', profile: 'fox' }}
        session={{ runtimeId: 'shared', storedId: 'stored-fox' }}
        voiceAvailable
      />
    )
    relay.resolve({ transcript: 'old audio must not reach fox' })

    await expect(pending).rejects.toThrow(/leader voice route changed/i)
  })
})

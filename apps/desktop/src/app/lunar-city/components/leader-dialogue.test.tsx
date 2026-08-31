// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { type ChatMessage, textPart } from '@/lib/chat-messages'

import type { LeaderOwner, LeaderSession } from '../leader-sessions'

import { CameraControls } from './camera-controls'
import {
  LeaderDialogue,
  type LeaderDialogueProps,
  type LeaderDialogueSessionState,
  type LeaderVoiceBridge
} from './leader-dialogue'

const OWNER: LeaderOwner = { connectionId: 'a', profile: 'owl' }
const SESSION: LeaderSession = { runtimeId: 'runtime-owl', storedId: 'stored-owl' }

function message(
  id: string,
  role: ChatMessage['role'],
  text: string,
  overrides: Partial<ChatMessage> = {}
): ChatMessage {
  return { id, parts: [textPart(text)], role, ...overrides }
}

function sessionState(overrides: Partial<LeaderDialogueSessionState> = {}): LeaderDialogueSessionState {
  return {
    awaitingResponse: false,
    busy: false,
    interrupted: false,
    messages: [
      message('u1', 'user', 'Can you inspect the archive?'),
      message('a1', 'assistant', 'I am checking it now.')
    ],
    ...overrides
  }
}

function voice(overrides: Partial<LeaderVoiceBridge> = {}): LeaderVoiceBridge {
  return {
    active: false,
    available: true,
    end: vi.fn(),
    start: vi.fn(),
    status: 'idle',
    stopTurn: vi.fn(),
    ...overrides
  }
}

function props(overrides: Partial<LeaderDialogueProps> = {}): LeaderDialogueProps {
  return {
    leaderLabel: 'Owl leader',
    onClose: vi.fn(),
    onInterrupt: vi.fn(),
    onOpenFullChat: vi.fn(),
    onSubmit: vi.fn(),
    owner: OWNER,
    session: SESSION,
    sessionAvailable: true,
    sessionState: sessionState(),
    voice: voice(),
    ...overrides
  }
}

describe('LeaderDialogue', () => {
  it('shows the standard session transcript and submits text to the exact resolved owner without inventing completion', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    const view = render(<LeaderDialogue {...props({ onSubmit })} />)

    expect(screen.getByRole('dialog', { name: 'Owl leader conversation' })).not.toBeNull()
    expect(screen.getByRole('log', { name: 'Conversation transcript' }).textContent).toContain(
      'Can you inspect the archive?'
    )
    expect(screen.getByRole('log', { name: 'Conversation transcript' }).textContent).toContain('I am checking it now.')
    expect(screen.getByRole('status').textContent).toContain('Ready')

    fireEvent.change(screen.getByRole('textbox', { name: 'Message Owl leader' }), {
      target: { value: 'Please continue.' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('Please continue.', SESSION, OWNER))
    expect(screen.getByRole('status').textContent).toContain('Ready')

    view.rerender(
      <LeaderDialogue
        {...props({
          onSubmit,
          sessionState: sessionState({ awaitingResponse: true, busy: true })
        })}
      />
    )
    expect(screen.getByRole('status').textContent).toContain('Thinking')
  })

  it.each([
    ['listening', 'Listening'],
    ['transcribing', 'Transcribing'],
    ['thinking', 'Thinking'],
    ['speaking', 'Speaking']
  ] as const)('announces the existing voice bridge %s state in text', (status, label) => {
    render(<LeaderDialogue {...props({ voice: voice({ active: true, status }) })} />)

    expect(screen.getByRole('status').textContent).toContain(label)
  })

  it('announces authoritative interruption, unavailability, and error states without color-only meaning', () => {
    const view = render(<LeaderDialogue {...props({ sessionState: sessionState({ interrupted: true }) })} />)
    const status = screen.getByRole('status')

    expect(status.textContent).toContain('Interrupted')
    expect(status.getAttribute('data-state')).toBe('interrupted')

    view.rerender(<LeaderDialogue {...props({ sessionAvailable: false })} />)
    expect(status.textContent).toContain('Unavailable')
    expect(status.getAttribute('data-state')).toBe('unavailable')

    view.rerender(<LeaderDialogue {...props({ sessionError: 'Gateway rejected the turn' })} />)
    expect(status.textContent).toContain('Error: Gateway rejected the turn')
    expect(status.getAttribute('data-state')).toBe('error')
  })

  it.each([
    'Microphone permission denied',
    'Speech transcription failed',
    'Speech playback failed',
    'Voice transport disconnected'
  ])('keeps text usable when voice reports: %s', error => {
    render(<LeaderDialogue {...props({ voice: voice({ available: false, error }) })} />)

    expect(screen.getByRole('alert').textContent).toContain(error)
    const textbox = screen.getByRole('textbox', { name: 'Message Owl leader' }) as HTMLTextAreaElement

    expect(textbox.disabled).toBe(false)
    fireEvent.change(textbox, { target: { value: 'Text still works' } })
    expect((screen.getByRole('button', { name: 'Send message' }) as HTMLButtonElement).disabled).toBe(false)
    expect((screen.getByRole('button', { name: 'Voice unavailable' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('uses only the injected existing voice and real-turn interruption callbacks', async () => {
    const bridge = voice({ active: false })
    const onInterrupt = vi.fn().mockResolvedValue(undefined)
    const view = render(<LeaderDialogue {...props({ onInterrupt, voice: bridge })} />)

    expect(bridge.start).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Start voice' }))
    expect(bridge.start).toHaveBeenCalledTimes(1)

    view.rerender(
      <LeaderDialogue {...props({ onInterrupt, voice: { ...bridge, active: true, status: 'listening' } })} />
    )
    fireEvent.click(screen.getByRole('button', { name: 'Finish speaking' }))
    expect(bridge.stopTurn).toHaveBeenCalledTimes(1)

    view.rerender(
      <LeaderDialogue {...props({ onInterrupt, voice: { ...bridge, active: true, status: 'speaking' } })} />
    )
    fireEvent.click(screen.getByRole('button', { name: 'Interrupt response' }))
    await waitFor(() => expect(onInterrupt).toHaveBeenCalledWith(SESSION, OWNER))

    fireEvent.click(screen.getByRole('button', { name: 'End voice' }))
    expect(bridge.end).toHaveBeenCalledTimes(1)
  })

  it('ends the existing voice bridge when the leader session changes or the overlay unmounts', () => {
    const owlVoice = voice({ active: true, status: 'listening' })
    const foxVoice = voice({ active: true, status: 'listening' })
    const view = render(<LeaderDialogue {...props({ voice: owlVoice })} />)

    view.rerender(
      <LeaderDialogue
        {...props({
          leaderLabel: 'Fox leader',
          owner: { connectionId: 'a', profile: 'fox' },
          session: { runtimeId: 'runtime-fox', storedId: 'stored-fox' },
          voice: foxVoice
        })}
      />
    )

    expect(owlVoice.end).toHaveBeenCalledTimes(1)
    expect(foxVoice.end).not.toHaveBeenCalled()

    view.unmount()
    expect(foxVoice.end).toHaveBeenCalledTimes(1)
  })

  it('opens the same durable standard session on its exact owner route', () => {
    const onOpenFullChat = vi.fn()
    render(<LeaderDialogue {...props({ onOpenFullChat })} />)

    fireEvent.click(screen.getByRole('button', { name: 'Open Full Chat' }))

    expect(onOpenFullChat).toHaveBeenCalledWith('stored-owl', OWNER)
  })

  it('keeps camera-control children operable while the non-modal dialogue remains open', () => {
    const dispatch = vi.fn()
    render(
      <LeaderDialogue
        {...props({
          cameraControls: (
            <CameraControls dispatch={dispatch} state={{ focusedEntityKey: undefined, following: false }} />
          )
        })}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Rotate Left' }))
    fireEvent.click(screen.getByRole('button', { name: 'Zoom In' }))
    fireEvent.click(screen.getByRole('button', { name: 'Return to City' }))

    expect(dispatch).toHaveBeenCalledWith({ deltaAlpha: -0.22, deltaBeta: 0, kind: 'orbit' })
    expect(dispatch).toHaveBeenCalledWith({ delta: -6, kind: 'zoom' })
    expect(dispatch).toHaveBeenCalledWith({ kind: 'return-to-city' })
    expect(screen.getByRole('dialog', { name: 'Owl leader conversation' })).not.toBeNull()
  })

  it('contains dialogue pointer activation instead of forwarding it to the world surface', () => {
    const onOpenFullChat = vi.fn()
    const onWorldClick = vi.fn()

    render(
      <div onClick={onWorldClick}>
        <LeaderDialogue {...props({ onOpenFullChat })} />
      </div>
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open Full Chat' }))

    expect(onOpenFullChat).toHaveBeenCalledTimes(1)
    expect(onWorldClick).not.toHaveBeenCalled()
  })

  it('submits from the keyboard, closes on Escape, and returns focus without starting audio', async () => {
    const onClose = vi.fn()
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    const bridge = voice()

    function Fixture() {
      const [open, setOpen] = useState(false)

      return (
        <>
          <button onClick={() => setOpen(true)} type="button">
            Talk to Owl leader
          </button>
          {open ? (
            <LeaderDialogue
              {...props({
                onClose: () => {
                  onClose()
                  setOpen(false)
                },
                onSubmit,
                voice: bridge
              })}
            />
          ) : null}
        </>
      )
    }

    render(<Fixture />)
    const launcher = screen.getByRole('button', { name: 'Talk to Owl leader' })
    launcher.focus()
    fireEvent.click(launcher)

    const textbox = screen.getByRole('textbox', { name: 'Message Owl leader' })
    fireEvent.change(textbox, { target: { value: 'Keyboard hello' } })
    fireEvent.keyDown(textbox, { key: 'Enter' })
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('Keyboard hello', SESSION, OWNER))

    expect(bridge.start).not.toHaveBeenCalled()
    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Owl leader conversation' }), { key: 'Escape' })

    expect(onClose).toHaveBeenCalledTimes(1)
    expect(globalThis.document.activeElement).toBe(launcher)
  })

  it('keeps the draft and text fallback after a standard submit transport rejection', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('Submit transport failed'))
    render(<LeaderDialogue {...props({ onSubmit })} />)
    const textbox = screen.getByRole('textbox', { name: 'Message Owl leader' })

    fireEvent.change(textbox, { target: { value: 'Do not lose this' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect((await screen.findByRole('alert')).textContent).toContain('Submit transport failed')
    expect((textbox as HTMLTextAreaElement).value).toBe('Do not lose this')
    expect((textbox as HTMLTextAreaElement).disabled).toBe(false)
    expect((screen.getByRole('button', { name: 'Send message' }) as HTMLButtonElement).disabled).toBe(false)
  })
})

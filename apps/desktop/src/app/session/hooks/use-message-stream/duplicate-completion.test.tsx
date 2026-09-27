import type { GatewayEventName } from '@hermes/shared'
import { act, cleanup, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { appendMidTurnUserMessage } from '@/app/session/hooks/use-prompt-actions/rewind'
import { type ChatMessage, chatMessageText, textPart } from '@/lib/chat-messages'

import { renderMessageStream } from './test-harness'

const SID = 'duplicate-completion'

function mount() {
  const hydrate = vi.fn(async () => undefined)
  const stream = renderMessageStream(SID, { hydrateFromStoredSession: hydrate })

  const send = (type: GatewayEventName, payload: Record<string, unknown> = {}) =>
    act(() => stream.handleEvent({ type, payload, session_id: SID }))

  return { stream, send, hydrate }
}

afterEach(cleanup)

it('settles identical tool-interim completion once while retaining every completed call', async () => {
  for (const reusedId of [false, true]) {
    const { stream, send, hydrate } = mount()
    await send('message.start')
    await send('message.delta', { text: 'same reply' })

    if (reusedId) {
      await send('tool.start', { name: 'terminal', tool_id: 'call', args: { command: 'pwd' } })
      await send('tool.complete', { name: 'terminal', tool_id: 'call', result: 'first result' })
    }

    await send('message.interim', { text: 'same reply', already_streamed: true })
    const keptId = stream.state().messages.at(-1)!.id
    await send('tool.start', { name: 'terminal', tool_id: 'call', args: { command: 'date' } })
    await send('tool.complete', { name: 'terminal', tool_id: 'call', result: 'second result' })
    await send('message.complete', { text: 'same reply' })

    const messages = stream.state().messages
    const tools = messages.flatMap(message => message.parts).filter(part => part.type === 'tool-call')
    expect(messages).toHaveLength(1)
    expect(messages[0]).toMatchObject({ id: keptId, interim: false, pending: false })
    expect(chatMessageText(messages[0])).toBe('same reply')
    expect(tools.map(tool => tool.result)).toEqual(reusedId ? ['first result', 'second result'] : ['second result'])
    expect(new Set(tools.map(tool => tool.toolCallId)).size).toBe(tools.length)
    expect(stream.state().streamId).toBeNull()
    expect(hydrate).not.toHaveBeenCalled()
    cleanup()
  }
})

it('settles a live stream into the exact durable row published during reconnect', async () => {
  const { stream, send } = mount()
  await send('message.start')
  await send('message.delta', { text: 'answer survived reconnect' })
  await waitFor(() => expect(stream.state().messages.at(-1)).toBeDefined())

  const live = stream.state().messages.at(-1)!

  const durable: ChatMessage = {
    id: '1790503219.6128294-17-assistant',
    role: 'assistant',
    rowId: 17,
    parts: [{ type: 'text', text: 'answer survived reconnect' }]
  }

  stream.states.set(SID, { ...stream.state(), messages: [...stream.state().messages, durable] })

  await send('message.complete', {
    text: 'answer survived reconnect',
    persisted_turn: { complete: true, final_assistant_row_id: 17, row_ids: [17] }
  })

  const messages = stream.state().messages
  expect(messages.filter(message => chatMessageText(message).includes('answer survived reconnect'))).toHaveLength(1)
  expect(messages.find(message => message.id === live.id)).toBeUndefined()
  expect(messages.find(message => message.rowId === 17)).toMatchObject({ id: durable.id, durableComplete: true })
})

it('keeps distinct segments, user boundaries, and failures on their own side of completion', async () => {
  const cases = [
    { name: 'distinct interim', interim: 'Checking.' },
    { name: 'longer final', final: 'same reply with more detail' },
    { name: 'distinct live text', live: 'Different streamed text.' },
    { name: 'visible correction', user: true },
    { name: 'hidden directive', user: true, hidden: true },
    { name: 'intervening assistant', assistant: true },
    { name: 'new turn', restart: true },
    { name: 'failed completion', error: 'tool failed' }
  ]

  for (const fixture of cases) {
    const { stream, send } = mount()
    await send('message.start')
    await send('message.interim', { text: fixture.interim ?? 'same reply', already_streamed: true })
    const earlierId = stream.state().messages[0].id

    if (fixture.user) {
      stream.states.set(
        SID,
        appendMidTurnUserMessage(stream.state(), {
          id: 'correction',
          role: 'user',
          parts: [textPart('Use the other file.')],
          hidden: fixture.hidden
        })
      )
    }

    if (fixture.assistant) {
      await send('message.interim', { text: 'Another observation.', already_streamed: true })
    }

    if (fixture.restart) {
      await send('message.start')
    }

    await send('tool.start', { name: 'terminal', tool_id: 'after-boundary', args: { command: 'pwd' } })

    if (fixture.live) {
      await send('message.delta', { text: fixture.live })
    }

    const liveId = stream.state().streamId
    await send('message.complete', {
      text: fixture.final ?? 'same reply',
      ...(fixture.error ? { error: fixture.error, status: 'error' } : {})
    })

    const messages = stream.state().messages
    expect(messages.at(-1)?.id, fixture.name).toBe(liveId)
    expect(chatMessageText(messages.find(message => message.id === earlierId)!), fixture.name).toBe(
      fixture.interim ?? 'same reply'
    )
    expect(
      messages.at(-1)?.parts.some(part => part.type === 'tool-call'),
      fixture.name
    ).toBe(true)

    if (fixture.error) {
      expect(messages.at(-1)?.error).toBe(fixture.error)
    }

    cleanup()
  }
})

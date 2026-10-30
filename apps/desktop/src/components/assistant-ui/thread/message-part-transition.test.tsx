// Controlled-retention consumer contract, plus real producer/runtime coverage.
// The retained consumer does not reproduce the hosted SDK scheduling sequence.
import {
  AssistantRuntimeProvider,
  MessagePrimitive,
  PartByIndexProvider,
  type ReasoningMessagePartProps,
  type TextMessagePartProps,
  TextMessagePartProvider,
  type ThreadMessage,
  ThreadPrimitive,
  useAuiState
} from '@assistant-ui/react'
import { cleanup, render, waitFor } from '@testing-library/react'
import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { useRuntimeMessageRepository } from '@/app/chat/runtime-repository'
import { ErrorBoundary } from '@/components/error-boundary'
import { registry } from '@/contrib/registry'
import { type ChatMessage, mergeFinalAssistantText, toChatMessages } from '@/lib/chat-messages'
import { useIncrementalExternalStoreRuntime } from '@/lib/incremental-external-store-runtime'
import { TRANSCRIPT_DIRECTIVE_AREA, type TranscriptDirectiveContribution } from '@/lib/transcript-directives'
import { $textDirection, setTextDirection } from '@/store/text-direction'

import { stubThreadEnvironment, stubThreadViewportSize } from '../test-utils'

import { MESSAGE_PARTS_COMPONENTS } from './message-parts'

import { Thread } from '.'

beforeEach(() => {
  stubThreadEnvironment()
  stubThreadViewportSize()
})

it('survives the same history fold with browser task publication outside React act', async () => {
  const answer = 'The browser-task work is complete.'

  const hydrated = toChatMessages([
    { role: 'user', content: 'Finish the work', timestamp: 1, row_id: 1 },
    {
      role: 'assistant',
      content: answer,
      timestamp: 2,
      row_id: 2,
      tool_calls: [{ id: 'todo-2', function: { name: 'todo_list', arguments: '{"todos":[]}' } }]
    },
    { role: 'tool', content: '{"ok":true}', tool_call_id: 'todo-2', timestamp: 3, row_id: 3 },
    { role: 'assistant', content: answer, timestamp: 4, row_id: 4 }
  ])

  const reply = hydrated.find(message => message.role === 'assistant')!
  const tool = reply.parts.find(part => part.type === 'tool-call')!
  const errors: Error[] = []
  let observedPhase = ''

  const onPhase = (phase: string) => {
    observedPhase = phase
  }

  const container = window.document.createElement('div')
  window.document.body.append(container)
  const root = createRoot(container)
  // act is intentionally absent: Tap and React each get their ordinary task
  // publication. No scheduler or SDK/component implementation is mocked.
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', false)

  const publish = async (parts: ChatMessage['parts'], busy: boolean) => {
    const messages = hydrated.map(message => (message === reply ? { ...message, parts, pending: busy } : message))
    root.render(
      <ErrorBoundary
        fallback={({ error }) => <div role="alert">{error.message}</div>}
        onError={error => errors.push(error)}
      >
        <Transcript busy={busy} messages={messages} onPhase={onPhase} />
      </ErrorBoundary>
    )
    // Acknowledge the actual SDK thread publication, not elapsed wall time.
    await waitFor(() => expect(observedPhase).toBe(expectedPhase(messages, busy)))
  }

  try {
    await publish([], true)
    await publish([{ type: 'text', text: answer }], true)
    await publish([{ type: 'text', text: answer }, tool], true)
    expect(container.textContent).toContain(answer)
    await publish(reply.parts, false)
    expect(errors).toEqual([])
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(container.textContent).toContain(answer)
  } finally {
    root.unmount()
    container.remove()
  }
})

afterEach(() => {
  cleanup()
  $textDirection.set('auto')
  vi.unstubAllGlobals()
})

it('renders successive supplied text/status snapshots, empty continuations and forced direction', async () => {
  const dispose = registry.register({
    id: 'test:text-snapshot-status',
    area: TRANSCRIPT_DIRECTIVE_AREA,
    source: 'plugin:test',
    data: {
      name: 'snapshotstatus',
      render: ({ streaming }) => <span data-testid="snapshot-status">{String(streaming)}</span>
    } satisfies TranscriptDirectiveContribution
  })

  const Text = MESSAGE_PARTS_COMPONENTS.Text
  const messages: ChatMessage[] = [{ id: 'user', role: 'user', parts: [{ type: 'text', text: 'Finish' }] }]

  const view = (text: string, running: boolean) => (
    <Transcript busy={false} messages={messages}>
      <TextMessagePartProvider text="ambient scope must not replace supplied prose">
        <Text status={running ? { type: 'running' } : { type: 'complete' }} text={text} type="text" />
      </TextMessagePartProvider>
    </Transcript>
  )

  setTextDirection('rtl')
  const { container, rerender } = render(view('First words\n\n::snapshotstatus', true))

  try {
    await waitFor(() => expect(container.querySelector('[data-testid="snapshot-status"]')?.textContent).toBe('true'))
    expect(container.textContent).toContain('First words')
    expect(container.querySelector('[dir="rtl"]')).not.toBeNull()
    rerender(view('Final words\n\n::snapshotstatus', false))
    await waitFor(() => expect(container.querySelector('[data-testid="snapshot-status"]')?.textContent).toBe('false'))
    expect(container.textContent).toContain('Final words')
    expect(container.textContent).not.toContain('First words')
    rerender(view('   ', true))
    await waitFor(() => expect(container.textContent).toBe(''))
  } finally {
    dispose()
  }
})

it('replaces disjoint live identities and part ordering with the persisted projection', async () => {
  // The native capture replaced both user and assistant IDs and moved the
  // repeated answer after its tool. Keep that shape hermetic here; the direct
  // actual-capture probe and its receipt remain private diagnostic evidence.
  const answer = 'The persisted response is complete.'

  const hydrated = toChatMessages([
    { role: 'user', content: 'Finish', timestamp: 1, row_id: 1 },
    {
      role: 'assistant',
      content: answer,
      timestamp: 2,
      row_id: 2,
      tool_calls: [{ id: 'identity-todo', function: { name: 'todo_list', arguments: '{"todos":[]}' } }]
    },
    { role: 'tool', content: '{"ok":true}', tool_call_id: 'identity-todo', timestamp: 3, row_id: 3 },
    { role: 'assistant', content: answer, timestamp: 4, row_id: 4 }
  ])

  const reply = hydrated[1]!
  expect(reply.parts.map(part => part.type)).toEqual(['tool-call', 'text'])

  const live = hydrated.map((message, index) => ({
    ...message,
    id: `live-${index}`,
    parts: message === reply ? [{ type: 'text' as const, text: answer }, reply.parts[0]!] : message.parts
  }))

  const errors: Error[] = []
  let observedPhase = ''

  const onPhase = (phase: string) => {
    observedPhase = phase
  }

  const container = window.document.createElement('div')
  window.document.body.append(container)
  const root = createRoot(container)
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', false)

  const publish = async (messages: ChatMessage[]) => {
    root.render(
      <ErrorBoundary
        fallback={({ error }) => <div role="alert">{error.message}</div>}
        onError={error => errors.push(error)}
      >
        <Transcript busy={false} messages={messages} onPhase={onPhase} />
      </ErrorBoundary>
    )
    await waitFor(() => expect(observedPhase).toBe(expectedPhase(messages, false)))
  }

  try {
    await publish(live)
    expect(container.textContent).toContain(answer)
    await publish(hydrated)
    expect(errors).toEqual([])
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(container.textContent).toContain(answer)
  } finally {
    root.unmount()
    container.remove()
  }
})

function partPhase(part: ChatMessage['parts'][number]) {
  return {
    type: part.type,
    ...('text' in part ? { text: part.text } : {}),
    ...('toolCallId' in part ? { toolCallId: part.toolCallId } : {})
  }
}

function expectedPhase(messages: ChatMessage[], running: boolean) {
  return JSON.stringify({
    running,
    messages: messages.map(message => ({
      id: message.id,
      role: message.role,
      status: message.role === 'assistant' ? (message.pending ? 'running' : 'complete') : undefined,
      content: message.parts.map(partPhase)
    }))
  })
}

function RuntimePhaseObserver({ onPhase }: { onPhase: (phase: string) => void }) {
  const phase = useAuiState(state =>
    JSON.stringify({
      running: state.thread.isRunning,
      messages: state.thread.messages.map(message => ({
        id: message.id,
        role: message.role,
        status: message.role === 'assistant' ? message.status.type : undefined,
        content: message.content.map(partPhase)
      }))
    })
  )

  useEffect(() => onPhase(phase), [onPhase, phase])

  return null
}

function Transcript({
  messages,
  busy,
  children = <Thread />,
  onPhase
}: {
  messages: ChatMessage[]
  busy: boolean
  children?: ReactNode
  onPhase?: (phase: string) => void
}) {
  const messageRepository = useRuntimeMessageRepository(messages)

  const runtime = useIncrementalExternalStoreRuntime<ThreadMessage>({
    messageRepository,
    isRunning: busy,
    setMessages: () => {},
    onNew: async () => {},
    onCancel: async () => {}
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {onPhase && <RuntimePhaseObserver onPhase={onPhase} />}
      {children}
    </AssistantRuntimeProvider>
  )
}

type PartSnapshot = TextMessagePartProps | ReasoningMessagePartProps

const LeaseContext = createContext<{
  snapshot: PartSnapshot | null
  dispatch: (props: PartSnapshot) => void
}>({ snapshot: null, dispatch: () => {} })

function RecordTextDispatch(props: PartSnapshot) {
  const { dispatch } = useContext(LeaseContext)
  useEffect(() => dispatch(props), [dispatch, props])

  return null
}

const recordComponents = { Reasoning: RecordTextDispatch, Text: RecordTextDispatch }

function ScopeType() {
  const type = useAuiState(state => state.part.type)

  return <span data-testid="leased-scope-type">{type}</span>
}

function LeaseMessage() {
  const { snapshot } = useContext(LeaseContext)
  const Reasoning = MESSAGE_PARTS_COMPONENTS.Reasoning
  const Text = MESSAGE_PARTS_COMPONENTS.Text

  return (
    <>
      <MessagePrimitive.Parts components={recordComponents} />
      <PartByIndexProvider index={0}>
        <ScopeType />
        {snapshot?.type === 'reasoning' ? <Reasoning {...snapshot} /> : snapshot && <Text {...snapshot} />}
      </PartByIndexProvider>
    </>
  )
}

const leaseMessageComponents = { Message: LeaseMessage }

function LeasedTranscript({ messages }: { messages: ChatMessage[] }) {
  const [snapshot, setSnapshot] = useState<PartSnapshot | null>(null)
  const dispatch = useCallback((props: PartSnapshot) => setSnapshot(current => current ?? props), [])

  return (
    <Transcript busy={false} messages={messages}>
      <LeaseContext.Provider value={{ snapshot, dispatch }}>
        <ThreadPrimitive.MessageByIndex components={leaseMessageComponents} index={1} />
      </LeaseContext.Provider>
    </Transcript>
  )
}

it('controlled consumer lease: a previously dispatched Text snapshot survives a valid part-scope advance', async () => {
  // Diagnostic boundary contract only: holding this consumer explicitly models
  // the disputed stale-child interval. It does NOT reproduce SDK scheduling or
  // prove that this interval occurred in the hosted failure.
  const answer = 'The leased response is complete.'

  const hydrated = toChatMessages([
    { role: 'user', content: 'Finish', timestamp: 1, row_id: 1 },
    {
      role: 'assistant',
      content: answer,
      timestamp: 2,
      row_id: 2,
      tool_calls: [{ id: 'lease-todo', function: { name: 'todo_list', arguments: '{"todos":[]}' } }]
    },
    { role: 'tool', content: '{"ok":true}', tool_call_id: 'lease-todo', timestamp: 3, row_id: 3 },
    { role: 'assistant', content: answer, timestamp: 4, row_id: 4 }
  ])

  const reply = hydrated[1]!
  expect(reply.parts.map(part => part.type)).toEqual(['tool-call', 'text'])

  const live = hydrated.map(message =>
    message === reply
      ? {
          ...message,
          parts: [{ type: 'text' as const, text: answer }, reply.parts[0]!]
        }
      : message
  )

  const errors: Error[] = []

  const view = (messages: ChatMessage[]) => (
    <ErrorBoundary
      fallback={({ error }) => <div role="alert">{error.message}</div>}
      onError={error => errors.push(error)}
    >
      <LeasedTranscript messages={messages} />
    </ErrorBoundary>
  )

  const { container, rerender } = render(view(live))
  await waitFor(() => expect(container.textContent).toContain(answer))
  rerender(view(hydrated))
  await waitFor(() => {
    expect(errors).toEqual([])
    expect(container.querySelector('[data-testid="leased-scope-type"]')?.textContent).toBe('tool-call')
    expect(container.textContent).toContain(answer)
  })
})

it('controlled reasoning lease survives an authoritative final replacing its covered reasoning', async () => {
  const thought = 'The task is ready.'
  const parts: ChatMessage['parts'] = [{ type: 'reasoning', text: thought }]

  const live: ChatMessage[] = [
    { id: 'user', role: 'user', parts: [{ type: 'text', text: 'Finish' }] },
    { id: 'assistant', role: 'assistant', pending: true, parts }
  ]

  // This actual completion producer removes reasoning that the authoritative
  // answer fully covers. Hold the old dispatched consumer, not an invalid row.
  const finalParts = mergeFinalAssistantText(parts, `${thought} All done.`)
  expect(finalParts.map(part => part.type)).toEqual(['text'])

  const settled = live.map(message =>
    message.role === 'assistant' ? { ...message, pending: false, parts: finalParts } : message
  )

  const errors: Error[] = []

  const view = (messages: ChatMessage[]) => (
    <ErrorBoundary
      fallback={({ error }) => <div role="alert">{error.message}</div>}
      onError={error => errors.push(error)}
    >
      <LeasedTranscript messages={messages} />
    </ErrorBoundary>
  )

  const { container, rerender } = render(view(live))
  await waitFor(() => expect(container.textContent).toContain(thought))
  rerender(view(settled))
  await waitFor(() => {
    expect(errors).toEqual([])
    expect(container.querySelector('[data-testid="leased-scope-type"]')?.textContent).toBe('text')
    expect(container.textContent).toContain(thought)
  })
})

it.each([false, true])(
  'keeps the transcript mounted when persisted repeated prose moves a tool into a former text slot (streamed repeat: %s)',
  async streamedRepeat => {
    const answer = 'The requested work is complete.'

    // Hydration intentionally keeps the final occurrence when a provider repeats
    // its pre-tool prose in the stop row. Exercise that real producer, not an
    // arbitrary content reorder or a mocked SDK subscription.
    const hydrated = toChatMessages([
      { role: 'user', content: 'Finish the work', timestamp: 1, row_id: 1 },
      {
        role: 'assistant',
        content: answer,
        timestamp: 2,
        row_id: 2,
        tool_calls: [{ id: 'todo-1', function: { name: 'todo_list', arguments: '{"todos":[]}' } }]
      },
      { role: 'tool', content: '{"ok":true}', tool_call_id: 'todo-1', timestamp: 3, row_id: 3 },
      { role: 'assistant', content: answer, timestamp: 4, row_id: 4 }
    ])

    const reply = hydrated.find(message => message.role === 'assistant')!
    expect(reply.parts.map(part => part.type)).toEqual(['tool-call', 'text'])
    const tool = reply.parts.find(part => part.type === 'tool-call')!

    const live = hydrated.map(message =>
      message === reply
        ? {
            ...message,
            pending: true,
            durableComplete: false,
            parts: [
              { type: 'text' as const, text: answer },
              tool,
              ...(streamedRepeat ? [{ type: 'text' as const, text: answer }] : [])
            ]
          }
        : message
    )

    const errors: Error[] = []

    const view = (messages: ChatMessage[], busy: boolean) => (
      <ErrorBoundary
        fallback={({ error }) => <div role="alert">{error.message}</div>}
        onError={error => errors.push(error)}
      >
        <Transcript busy={busy} messages={messages} />
      </ErrorBoundary>
    )

    const { container, rerender } = render(view(live, true))
    await waitFor(() => expect(container.textContent).toContain(answer))
    rerender(view(hydrated, false))
    await waitFor(() => {
      expect(errors).toEqual([])
      expect(container.querySelector('[role="alert"]')).toBeNull()
      expect(container.textContent).toContain(answer)
    })
  }
)

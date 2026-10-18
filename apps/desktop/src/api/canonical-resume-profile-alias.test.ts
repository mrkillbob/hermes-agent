import { type GatewayEvent, JsonRpcGatewayClient } from '@hermes/shared'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { deferred } from '../test/deferred'

import { HermesGateway } from './client'

const clients: HermesGateway[] = []
const unexpectedTransport = vi.fn(() => { throw new Error('Unexpected live transport') })

beforeEach(() => {
  unexpectedTransport.mockClear()
  vi.stubGlobal('fetch', unexpectedTransport)
  vi.stubGlobal('WebSocket', unexpectedTransport)
  vi.spyOn(JsonRpcGatewayClient.prototype, 'connect').mockResolvedValue()
})
afterEach(() => {
  for (const client of clients.splice(0)) { client.close() }
  expect(unexpectedTransport).not.toHaveBeenCalled()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const snapshot = (profile: string, epoch = 7, generation = 3, running = true) => ({
  session_id: 'same', stored_session_id: 'same', authority_epoch: epoch,
  execution_generation: generation, revision: 4, running, messages: [],
  replay_epoch: `epoch-${profile}-${epoch}`, pending: [], prompts: [],
  info: { profile_name: profile, profile_id: `/profiles/${profile}` }
})

async function clientFixture() {
  const client = new HermesGateway()
  clients.push(client)
  await client.connect('ws://localhost/api/ws?native_dial=fixture&ticket=fixture')
  const seen: GatewayEvent[] = []
  client.onAny(event => seen.push(event))

  const emit = (event: Record<string, unknown>) =>
    (client as unknown as { handleEvent(event: GatewayEvent): void }).handleEvent(event as unknown as GatewayEvent)

  return { client, emit, seen }
}

it.each(['session.resume', 'session.activate'])('keeps a pre-ACK terminal when %s resolves the implicit launch-profile alias', async method => {
  const ack = deferred<unknown>()
  const wire = vi.spyOn(JsonRpcGatewayClient.prototype, 'request').mockImplementation(async () => await ack.promise as never)
  const { client, emit, seen } = await clientFixture()
  const resumed = client.request<Record<string, any>>(method, { session_id: 'same' })
  await vi.waitFor(() => expect(wire).toHaveBeenCalled())
  expect(wire.mock.calls[0][1]).not.toHaveProperty('profile')
  emit({ type: 'message.start', session_id: 'same', authority_epoch: 7, execution_generation: 3, payload: {} })
  emit({ type: 'message.complete', session_id: 'same', authority_epoch: 7, execution_generation: 3, payload: { text: 'done' } })
  expect(seen.at(-1)?.profile).toBe('default')
  ack.resolve({ ...snapshot('work'), pending: [{ admission_id: 'queued', text: 'later', status: 'queued' }],
    prompts: [{ kind: 'approval', prompt_id: 'prompt', execution_generation: 3, choices: ['once'] }] })
  const result = await resumed
  expect(result.running).toBe(false)
  expect(result.info.running).toBe(false)
  expect(result.pending[0].admission_id).toBe('queued')
  expect(result.prompts[0].prompt_id).toBe('prompt')
})

it('does not merge independent named-profile islands or settle a newer execution through the launch alias', async () => {
  const ack = deferred<unknown>()
  let reply = snapshot('work')

  const wire = vi.spyOn(JsonRpcGatewayClient.prototype, 'request').mockImplementation(async (_method, params) =>
    params?.profile === undefined ? await ack.promise as never : reply as never)

  const { client, emit } = await clientFixture()
  const initial = client.request('session.resume', { session_id: 'same' })
  await vi.waitFor(() => expect(wire).toHaveBeenCalled())
  emit({ type: 'message.complete', session_id: 'same', authority_epoch: 7, execution_generation: 3, payload: {} })
  ack.resolve(reply)
  await initial
  reply = snapshot('other')
  expect(await client.request('session.resume', { session_id: 'same', profile: 'other' })).toHaveProperty('running', true)
  reply = snapshot('work', 7, 4)
  expect(await client.request('session.resume', { session_id: 'same', profile: 'work' })).toHaveProperty('running', true)
  reply = snapshot('work', 8, 1)
  expect(await client.request('session.resume', { session_id: 'same', profile: 'work' })).toHaveProperty('running', true)
})

it.each([{ epoch: 7, generation: 4 }, { epoch: 8, generation: 1 }])(
  'preserves newer named authority while an older implicit alias ACK settles ($epoch/$generation)', async ({ epoch, generation }) => {
    const implicit = deferred<unknown>()
    let reply = snapshot('work', 7, 1, false)

    const wire = vi.spyOn(JsonRpcGatewayClient.prototype, 'request').mockImplementation(async (_method, params) =>
      params?.profile === undefined ? await implicit.promise as never : reply as never)

    const { client, emit } = await clientFixture()
    const initial = client.request('session.resume', { session_id: 'same' })
    await vi.waitFor(() => expect(wire).toHaveBeenCalled())
    emit({ type: 'message.complete', session_id: 'same', authority_epoch: 7, execution_generation: 3, payload: {} })
    await client.request('session.resume', { session_id: 'same', profile: 'work' })
    reply = snapshot('work', epoch, generation, false)
    await client.request('session.resume', { session_id: 'same', profile: 'work' })
    implicit.resolve(snapshot('work'))
    await initial
    reply = snapshot('work', epoch, generation, true)
    expect(await client.request('session.resume', { session_id: 'same', profile: 'work' })).toHaveProperty('running', false)
  }
)

it('keeps a default primary terminal without requiring a named alias', async () => {
  const ack = deferred<unknown>()
  const wire = vi.spyOn(JsonRpcGatewayClient.prototype, 'request').mockImplementation(async () => await ack.promise as never)
  const { client, emit } = await clientFixture()
  const result = client.request('session.resume', { session_id: 'same' })
  await vi.waitFor(() => expect(wire).toHaveBeenCalled())
  emit({ type: 'message.complete', session_id: 'same', authority_epoch: 7, execution_generation: 3, payload: {} })
  ack.resolve(snapshot('default'))
  expect(await result).toHaveProperty('running', false)
})

it.each(['implicit', 'named'])('retains epochs retired by the %s authority when binding the launch alias', async retiredBy => {
  const ack = deferred<unknown>()
  let reply = snapshot('work', 6, 1, false)

  const wire = vi.spyOn(JsonRpcGatewayClient.prototype, 'request').mockImplementation(async (_method, params) =>
    params?.profile === undefined ? await ack.promise as never : reply as never)

  const { client, emit } = await clientFixture()
  const initial = client.request('session.resume', { session_id: 'same' })
  await vi.waitFor(() => expect(wire).toHaveBeenCalled())

  if (retiredBy === 'implicit') {
    emit({ type: 'message.start', session_id: 'same', authority_epoch: 6, execution_generation: 1, payload: {} })
  }

  emit({ type: 'message.start', session_id: 'same', authority_epoch: 7, execution_generation: 3, payload: {} })
  emit({ type: 'message.complete', session_id: 'same', authority_epoch: 7, execution_generation: 3, payload: {} })

  if (retiredBy === 'named') {
    await client.request('session.resume', { session_id: 'same', profile: 'work' })
    reply = snapshot('work', 7, 2, false)
    await client.request('session.resume', { session_id: 'same', profile: 'work' })
  }

  ack.resolve(snapshot('work'))
  await initial
  reply = snapshot('work', 6, 1)
  await client.request('session.resume', { session_id: 'same', profile: 'work' })
  reply = snapshot('work')
  expect(await client.request('session.resume', { session_id: 'same', profile: 'work' })).toHaveProperty('running', false)
})

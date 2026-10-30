import { expect, it } from 'vitest'

import { CanonicalDesktopProtocol } from './canonical-protocol'

it.each(['message.complete', 'message.error', 'session.info'])(
  '%s settles only the matching execution in a delayed resume snapshot',
  type => {
    const protocol = new CanonicalDesktopProtocol()
    const params = { session_id: 'chat', profile: 'work' }

    const snapshot = {
      session_id: 'chat',
      authority_epoch: 7,
      execution_generation: 3,
      running: true,
      revision: 4,
      messages: [],
      prompts: [],
      pending: [{ admission_id: 'next', text: 'queued follow-up', status: 'queued' }]
    }

    protocol.event({ type: 'message.start', ...params, authority_epoch: 7, execution_generation: 3, payload: {} })
    protocol.event({
      type,
      ...params,
      authority_epoch: 7,
      execution_generation: 3,
      payload: type === 'session.info' ? { running: false } : {}
    })

    const result = protocol.result('session.resume', params, snapshot)
    expect(result.running).toBe(false)
    expect(result.info.running).toBe(false)
    expect(result.pending).toBe(snapshot.pending)
    expect(snapshot.running).toBe(true)

    // Equal IDs in other profile islands and newer owner executions still run.
    expect(protocol.result('session.resume', { ...params, profile: 'other' }, snapshot).running).toBe(true)
    expect(protocol.result('session.resume', params, { ...snapshot, execution_generation: 4 }).running).toBe(true)
    expect(protocol.result('session.resume', params, { ...snapshot, authority_epoch: 8 }).running).toBe(true)
    protocol.event({ type: 'message.complete', ...params, authority_epoch: 7, execution_generation: 99, payload: {} })
    expect(protocol.result('session.resume', params, { ...snapshot, authority_epoch: 8 }).running).toBe(true)
  }
)

it('keeps a running snapshot when no matching versioned terminal was observed', () => {
  const protocol = new CanonicalDesktopProtocol()
  const params = { session_id: 'chat', profile: 'work' }

  const snapshot = {
    session_id: 'chat',
    authority_epoch: 7,
    execution_generation: 3,
    running: true,
    messages: [],
    prompts: []
  }

  protocol.event({ type: 'message.complete', ...params, payload: {} })
  expect(protocol.result('session.activate', params, snapshot).running).toBe(true)
})

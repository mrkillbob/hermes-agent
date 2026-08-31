// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { EntityIdentity, SourceHealth } from '../model'

import { EntityInspector, type EntityInspectorData } from './entity-inspector'

const IDENTITY: EntityIdentity = {
  board: 'primary',
  connectionId: 'connection-a',
  kind: 'kanban',
  profile: 'worker',
  runId: 'run-7',
  taskId: 'task-7',
  workerId: 'worker-7'
}

const SOURCE: SourceHealth = {
  authority: 'partial',
  error: 'Diagnostic tail is delayed',
  observedAt: Date.UTC(2026, 7, 30, 12, 30),
  source: 'connection-a/kanban/primary'
}

function data(overrides: Partial<EntityInspectorData> = {}): EntityInspectorData {
  return {
    attachments: [{ filename: 'receipt.json', id: 'attachment-1', size: 42 }],
    blocker: { detail: 'Waiting for exact-head review', kind: 'typed-review-blocker' },
    comments: [{ author: 'operator', body: 'Keep evidence separate.', createdAt: 20, id: 'comment-1' }],
    diagnostics: [{ detail: 'The review receipt is stale.', id: 'diagnostic-1', severity: 'warning' }],
    events: [{ at: 22, id: 'event-1', kind: 'task.blocked', summary: 'Blocked after review' }],
    identity: IDENTITY,
    logTail: { content: 'pytest: 2 failed', exists: true, truncated: true },
    owningSessionId: 'session-1',
    run: { id: 'run-7', outcome: 'blocked', status: 'failed' },
    source: SOURCE,
    subagent: {
      costUsd: 0.37,
      durationSeconds: 98,
      filesRead: ['src/a.ts'],
      filesWritten: ['src/b.ts'],
      goal: 'Review the task',
      id: 'child-7',
      state: 'failed',
      streamTail: ['Running tests', 'Found stale receipt']
    },
    task: { id: 'task-7', state: 'blocked', title: 'Repair review evidence' },
    ...overrides
  }
}

describe('EntityInspector', () => {
  it('renders exact typed identity, source, last-observed time, authority, and only available fields', () => {
    const view = render(<EntityInspector data={data({ attachments: undefined, comments: undefined })} />)

    expect(screen.getByRole('complementary', { name: 'Lunar City entity inspector' })).not.toBeNull()
    expect(screen.getByRole('region', { name: 'Identity' }).textContent).toContain('connection-a')
    expect(screen.getByRole('region', { name: 'Identity' }).textContent).toContain('task-7')
    expect(screen.getByRole('region', { name: 'Source evidence' }).textContent).toContain('connection-a/kanban/primary')
    expect(screen.getByRole('region', { name: 'Source evidence' }).textContent).toContain('Partial')
    expect(screen.getByText('Last observed').nextElementSibling?.querySelector('time')?.getAttribute('dateTime')).toBe(
      '2026-08-30T12:30:00.000Z'
    )
    expect(screen.queryByRole('region', { name: 'Attachments' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Comments' })).toBeNull()

    view.rerender(<EntityInspector data={data()} />)
    expect(screen.getByRole('region', { name: 'Attachments' })).not.toBeNull()
    expect(screen.getByRole('region', { name: 'Comments' })).not.toBeNull()
  })

  it('keeps task, run, diagnostics, comments, events, log, attachments, subagent, files, and blocker evidence separate', () => {
    render(<EntityInspector data={data()} />)

    for (const name of [
      'Task',
      'Run',
      'Diagnostics',
      'Comments',
      'Events',
      'Log tail',
      'Attachments',
      'Subagent',
      'Files',
      'Blocker'
    ]) {
      expect(screen.getByRole('region', { name })).not.toBeNull()
    }

    expect(screen.getByRole('region', { name: 'Task' }).textContent).not.toContain('pytest: 2 failed')
    expect(screen.getByRole('region', { name: 'Diagnostics' }).textContent).not.toContain('operator')
    expect(screen.getByRole('region', { name: 'Run' }).textContent).not.toContain('typed-review-blocker')
    expect(screen.getByRole('region', { name: 'Subagent' }).textContent).toContain('$0.37')
    expect(screen.getByRole('region', { name: 'Files' }).textContent).toContain('src/b.ts')
  })

  it('offers keyboard and screen-reader actions independent of canvas selection', () => {
    const onInspectEvidence = vi.fn()
    const onOpenSession = vi.fn()

    render(<EntityInspector data={data()} onInspectEvidence={onInspectEvidence} onOpenSession={onOpenSession} />)

    fireEvent.click(screen.getByRole('button', { name: 'Open owning session session-1' }))
    fireEvent.keyDown(screen.getByRole('button', { name: 'Inspect diagnostics for task-7' }), { key: 'Enter' })
    fireEvent.click(screen.getByRole('button', { name: 'Inspect diagnostics for task-7' }))

    expect(onOpenSession).toHaveBeenCalledWith('session-1')
    expect(onInspectEvidence).toHaveBeenCalledWith('diagnostics', IDENTITY)
  })
})

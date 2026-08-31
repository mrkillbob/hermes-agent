// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { CommandPlan } from '../command-broker'
import { entityKey } from '../identity'
import type { EntityIdentity } from '../model'

import { CommandConfirmation } from './command-confirmation'

const IDENTITY: EntityIdentity = {
  board: 'primary',
  connectionId: 'connection-a',
  kind: 'kanban',
  profile: 'worker',
  runId: 'run-7',
  taskId: 'task-7',
  workerId: 'worker-7'
}

function plan(overrides: Partial<CommandPlan> = {}): CommandPlan {
  return {
    confirmation: true,
    consequence: 'Reassigning stops the current worker and gives the task to reviewer.',
    context: {
      canonicalProjectId: 'project-1',
      currentState: 'running',
      repositoryId: 'repo/hermes',
      source: { authority: 'authoritative', observedAt: 2_000, source: 'connection-a/kanban/primary' }
    },
    entityKey: entityKey(IDENTITY),
    identity: IDENTITY,
    method: 'kanban.task.reassign',
    operation: 'reassign-task',
    owner: { connectionId: 'connection-a', profile: 'worker' },
    params: {
      assignee: 'reviewer',
      board: 'primary',
      run_id: 'run-7',
      task_id: 'task-7',
      worker_id: 'worker-7'
    },
    plannedAt: 2_000,
    readback: { id: 'task-7', kind: 'kanban-task' },
    ...overrides
  }
}

describe('CommandConfirmation', () => {
  it('renders a real accessible dialog with the complete exact identity, state, operation, and consequence', () => {
    render(<CommandConfirmation onCancel={vi.fn()} onConfirm={vi.fn()} open plan={plan()} />)

    const dialog = screen.getByRole('dialog', { name: 'Confirm Lunar City command' })

    expect(dialog.textContent).toContain('connection-a')
    expect(dialog.textContent).toContain('worker')
    expect(dialog.textContent).toContain('project-1')
    expect(dialog.textContent).toContain('repo/hermes')
    expect(dialog.textContent).toContain('primary')
    expect(dialog.textContent).toContain('task-7')
    expect(dialog.textContent).toContain('run-7')
    expect(dialog.textContent).toContain('worker-7')
    expect(dialog.textContent).toContain('running')
    expect(dialog.textContent).toContain('Reassign task')
    expect(dialog.textContent).toContain('Reassigning stops the current worker')
  })

  it('focuses Cancel first, traps focus, and maps Escape to the safe cancel path', async () => {
    const onCancel = vi.fn()
    const outside = window.document.createElement('button')
    outside.textContent = 'Outside'
    window.document.body.append(outside)

    render(<CommandConfirmation onCancel={onCancel} onConfirm={vi.fn()} open plan={plan()} />)

    const dialog = screen.getByRole('dialog', { name: 'Confirm Lunar City command' })
    const cancel = screen.getByRole('button', { name: 'Cancel command' })

    await waitFor(() => expect(window.document.activeElement).toBe(cancel))
    outside.focus()
    await waitFor(() => expect(dialog.contains(window.document.activeElement)).toBe(true))

    fireEvent.keyDown(window.document, { key: 'Escape' })
    await waitFor(() => expect(onCancel).toHaveBeenCalledTimes(1))
    outside.remove()
  })

  it('confirms once only when the plan remains exact and enabled', () => {
    const onConfirm = vi.fn()
    const view = render(<CommandConfirmation onCancel={vi.fn()} onConfirm={onConfirm} open plan={plan()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Confirm Reassign task' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(onConfirm).toHaveBeenCalledWith(plan())

    view.rerender(
      <CommandConfirmation
        disabledReason="The owning connection changed. Refresh before retrying."
        onCancel={vi.fn()}
        onConfirm={onConfirm}
        open
        plan={plan()}
      />
    )

    const disabled = screen.getByRole('button', { name: 'Confirm Reassign task' }) as HTMLButtonElement

    expect(disabled.disabled).toBe(true)
    expect(screen.getByRole('status').textContent).toContain('owning connection changed')
    fireEvent.click(disabled)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('cannot confirm an incomplete identity or a direct no-confirmation plan', () => {
    const onConfirm = vi.fn()

    const incomplete = {
      ...plan(),
      identity: { ...IDENTITY, taskId: '' },
      params: { ...plan().params, task_id: '' }
    } as CommandPlan

    const view = render(<CommandConfirmation onCancel={vi.fn()} onConfirm={onConfirm} open plan={incomplete} />)

    expect((screen.getByRole('button', { name: 'Confirm Reassign task' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByRole('status').textContent).toContain('incomplete exact identity')

    view.rerender(
      <CommandConfirmation onCancel={vi.fn()} onConfirm={onConfirm} open plan={plan({ confirmation: false })} />
    )
    expect((screen.getByRole('button', { name: 'Confirm Reassign task' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByRole('status').textContent).toContain('does not require disruptive confirmation')
  })

  it('cannot confirm when the readback identity or allowlisted method differs from the visible target', () => {
    const onConfirm = vi.fn()

    const view = render(
      <CommandConfirmation
        onCancel={vi.fn()}
        onConfirm={onConfirm}
        open
        plan={plan({ readback: { id: 'foreign-task', kind: 'kanban-task' } })}
      />
    )

    expect((screen.getByRole('button', { name: 'Confirm Reassign task' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByRole('status').textContent).toContain('readback target')

    view.rerender(
      <CommandConfirmation onCancel={vi.fn()} onConfirm={onConfirm} open plan={plan({ method: 'shell.exec' })} />
    )
    expect((screen.getByRole('button', { name: 'Confirm Reassign task' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByRole('status').textContent).toContain('method is not allowlisted')
  })
})

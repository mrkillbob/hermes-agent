import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfirmDialog } from '@/components/ui/confirm-dialog'

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: {
      common: { cancel: 'Cancel', confirm: 'Confirm', delete: 'Delete', done: 'Done', loading: 'Working' },
      errors: { genericFailure: 'Something failed' }
    }
  })
}))

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

function pendingConfirmation() {
  let resolve!: () => void
  let reject!: (error: Error) => void
  const promise = new Promise<void>((accept, refuse) => {
    resolve = accept
    reject = refuse
  })

  return { promise, reject, resolve }
}

it('cancels an already armed close timer on unmount', async () => {
  const onClose = vi.fn()
  const onConfirm = vi.fn()
  const view = render(<ConfirmDialog confirmLabel="Delete" onClose={onClose} onConfirm={onConfirm} open title="Delete session" />)

  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete' })) })
  expect(onConfirm).toHaveBeenCalledOnce()
  view.unmount()
  act(() => vi.advanceTimersByTime(1000))
  expect(onClose).not.toHaveBeenCalled()
})

it('shows the successful confirmation beat before closing exactly once', async () => {
  const onClose = vi.fn()
  render(<ConfirmDialog confirmLabel="Delete" onClose={onClose} onConfirm={vi.fn()} open title="Delete session" />)

  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete' })) })
  expect((screen.getByRole('button', { name: 'Done' }) as HTMLButtonElement).disabled).toBe(true)
  act(() => vi.advanceTimersByTime(599))
  expect(onClose).not.toHaveBeenCalled()
  act(() => vi.advanceTimersByTime(1))
  expect(onClose).toHaveBeenCalledOnce()
})

it('dismisses a mounted successful confirmation immediately without a second close', async () => {
  const onClose = vi.fn()
  render(<ConfirmDialog confirmLabel="Delete" dismissOnConfirm onClose={onClose} onConfirm={vi.fn()} open title="Delete session" />)

  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete' })) })
  expect(onClose).toHaveBeenCalledOnce()
  act(() => vi.advanceTimersByTime(1000))
  expect(onClose).toHaveBeenCalledOnce()
})

describe.each([false, true])('dismissOnConfirm=%s', dismissOnConfirm => {
  it('shows a current confirmation failure and keeps the dialog usable', async () => {
    const onClose = vi.fn()
    const onConfirm = vi.fn().mockRejectedValue(new Error('Current confirmation failed'))
    render(<ConfirmDialog confirmLabel="Delete" dismissOnConfirm={dismissOnConfirm}
      onClose={onClose} onConfirm={onConfirm} open title="Delete session" />)

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete' })) })
    expect(screen.getByText('Current confirmation failed')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Delete' }) as HTMLButtonElement).disabled).toBe(false)
    act(() => vi.advanceTimersByTime(1000))
    expect(onClose).not.toHaveBeenCalled()
  })

  it('ignores a pending confirmation that succeeds after its owner unmounts', async () => {
    const pending = pendingConfirmation()
    const onClose = vi.fn()
    const onConfirm = vi.fn(() => pending.promise)
    const view = render(<ConfirmDialog confirmLabel="Delete" dismissOnConfirm={dismissOnConfirm}
      onClose={onClose} onConfirm={onConfirm} open title="Delete session" />)

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(onConfirm).toHaveBeenCalledOnce()
    view.unmount()
    await act(async () => { pending.resolve() })
    act(() => vi.advanceTimersByTime(1000))
    expect(onClose).not.toHaveBeenCalled()
  })

  it.each(['resolve', 'reject'] as const)('keeps a reopened dialog untouched by an old %s', async outcome => {
    const pending = pendingConfirmation()
    const onClose = vi.fn()
    const props = { confirmLabel: 'Delete', dismissOnConfirm, onClose, onConfirm: () => pending.promise, open: true, title: 'Delete session' }
    const view = render(<ConfirmDialog {...props} />)

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    view.rerender(<ConfirmDialog {...props} open={false} />)
    view.rerender(<ConfirmDialog {...props} />)
    await act(async () => {
      if (outcome === 'reject') {
        pending.reject(new Error('Previous confirmation failed'))
      } else {
        pending.resolve()
      }
    })
    act(() => vi.advanceTimersByTime(1000))
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.queryByText('Previous confirmation failed')).toBeNull()
    expect((screen.getByRole('button', { name: 'Delete' }) as HTMLButtonElement).disabled).toBe(false)
  })
})

// The confirm action itself can unmount the dialog's owner (disbanding a group
// room closes the room view). The await in run() then resolves after cleanup
// already ran, so a timer armed at that point had nobody left to clear it.
it('a confirm that unmounts the dialog before it resolves never arms the close timer', async () => {
  vi.useFakeTimers()
  const onClose = vi.fn()

  let finish: () => void = () => {}
  const onConfirm = vi.fn(() => new Promise<void>(resolve => { finish = resolve }))

  render(<ConfirmDialog confirmLabel="Delete" onClose={onClose} onConfirm={onConfirm} open title="Delete session" />)
  fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
  expect(onConfirm).toHaveBeenCalled()

  cleanup()
  finish()
  await Promise.resolve()
  await Promise.resolve()
  vi.advanceTimersByTime(1000)

  expect(onClose).not.toHaveBeenCalled()
  vi.useRealTimers()
})

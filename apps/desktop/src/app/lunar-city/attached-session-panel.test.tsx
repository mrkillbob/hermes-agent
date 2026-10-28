// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n/context'

import { AttachedSessionInventory } from './attached-session-panel'
import type { SessionInventoryRead } from './attached-session-read'
import { readCurrentAttachedSessions } from './attached-session-source'
vi.mock('@/components/ui/button', () => ({ Button: (props: React.ComponentProps<'button'>) => <button {...props} /> }))
vi.mock('./attached-session-source', () => ({ readCurrentAttachedSessions: vi.fn() }))
const read = vi.mocked(readCurrentAttachedSessions)
const owner = { connectionId: 'a', profile: 'default' }
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})
describe('attached inventory panel', () => {
  it('uses the selected locale for its control and unavailable result without exposing machine codes', async () => {
    read.mockResolvedValue({ status: 'unavailable', reason: 'route-changed', entries: [] })
    render(
      <I18nProvider configClient={null} initialLocale="es">
        <AttachedSessionInventory owner={owner} />
      </I18nProvider>
    )
    expect(read).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Leer sesiones conectadas' }))
    await screen.findByText('Inventario no disponible. No se abrió ninguna sesión.')
    expect(screen.queryByText(/route-changed/)).toBeNull()
  })

  it('does not read on mount; reads only on the explicit gesture and never offers session activation', async () => {
    read.mockResolvedValue({
      status: 'read',
      entries: [{ ...owner, sessionId: 'stored-only', activity: 'unknown' }],
      observedAt: 1,
      activity: 'unavailable',
      window: 'up-to-100'
    })
    render(<AttachedSessionInventory owner={owner} />)
    expect(read).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Read attached sessions' }))
    await screen.findByText('stored-only')
    expect(read).toHaveBeenCalledTimes(1)
    expect(read.mock.calls[0][0]).toEqual(owner)
    expect(screen.getAllByRole('button')).toHaveLength(1)
    expect(screen.getByText(/Activity unknown/)).toBeTruthy()
  })
  it('aborts and discards the old owner response when the source changes', async () => {
    let complete!: (value: SessionInventoryRead) => void
    read.mockReturnValueOnce(
      new Promise(resolve => {
        complete = resolve
      })
    )
    const { rerender } = render(<AttachedSessionInventory owner={owner} />)
    fireEvent.click(screen.getByRole('button', { name: 'Read attached sessions' }))
    const signal = read.mock.calls[0][1]!
    rerender(<AttachedSessionInventory owner={{ ...owner, connectionId: 'b' }} />)
    expect(signal.aborted).toBe(true)
    complete({
      status: 'read',
      entries: [{ ...owner, sessionId: 'old-private-row', activity: 'unknown' }],
      observedAt: 1,
      activity: 'unavailable',
      window: 'up-to-100'
    })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Read attached sessions' }).hasAttribute('disabled')).toBe(false)
    )
    expect(screen.queryByText('old-private-row')).toBeNull()
    expect(read).toHaveBeenCalledTimes(1)
  })
})

// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { EntityKey, LunarCitySnapshot, LunarEntity } from '../model'

import { EntityList, orderedEntities } from './entity-list'

function key(value: string): EntityKey {
  return value as EntityKey
}

function entity(
  id: string,
  destination: LunarEntity['destination'],
  overrides: Partial<LunarEntity> = {}
): LunarEntity {
  return {
    authority: 'authoritative',
    animation: 'work',
    destination,
    identity: {
      connectionId: 'local',
      kind: 'session',
      profile: 'default',
      sessionId: id
    },
    key: key(`session:${destination}:${id}`),
    observedAt: 1_700_000_000_000,
    ...overrides
  }
}

function snapshot(entities: readonly LunarEntity[]): LunarCitySnapshot {
  return {
    entities: new Map(entities.map(value => [value.key, value])),
    observedAt: 1_700_000_000_000,
    revision: 1,
    sources: []
  }
}

describe('EntityList', () => {
  it('orders entities by manifest district and stable typed entity key', () => {
    const projectB = entity('zeta', 'project')
    const garden = entity('alpha', 'garden')
    const projectA = entity('alpha', 'project')

    expect(orderedEntities(snapshot([projectB, garden, projectA])).map(value => value.key)).toEqual([
      projectA.key,
      projectB.key,
      garden.key
    ])
  })

  it('uses native buttons with exact entity identity and readable state, destination, and authority', () => {
    const pip = entity('pip', 'lab', { animation: 'work' })
    const onSelect = vi.fn()

    render(<EntityList onSelect={onSelect} selectedEntityKey={pip.key} snapshot={snapshot([pip])} />)

    const button = screen.getByRole('button', { name: /Pip.*Working.*Research Lab.*Authoritative/i })
    expect(button.getAttribute('aria-pressed')).toBe('true')

    fireEvent.click(button)
    expect(onSelect).toHaveBeenCalledWith(pip)
  })

  it('does not rely on color and exposes stale or unknown state as text', () => {
    const unavailable = entity('worker-2', 'unknown', { animation: 'unavailable', authority: 'stale' })

    render(<EntityList onSelect={vi.fn()} snapshot={snapshot([unavailable])} />)

    expect(screen.getByRole('button', { name: /Worker 2.*Unavailable.*Unknown destination.*Stale/i })).toBeTruthy()
    expect(screen.getByText(/stale/i)).toBeTruthy()
  })
})

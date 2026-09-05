import type { LunarEntity } from './model'

export type CityStatus = 'blocked' | 'celebrating' | 'idle' | 'sleeping' | 'unavailable' | 'waiting' | 'working'

export interface CityStatusResult {
  active: boolean
  badge?: '!' | '?' | '✓' | '⚒'
  status: CityStatus
  urgent: boolean
}

export const CITY_STALE_AFTER_MS = 3 * 24 * 60 * 60 * 1000

const LABELS: Readonly<Record<CityStatus, string>> = {
  blocked: 'Blocked',
  celebrating: 'Shipped',
  idle: 'Idle',
  sleeping: 'Dormant',
  unavailable: 'Unavailable',
  waiting: 'Waiting on you',
  working: 'Working'
}

export function cityStatusLabel(status: CityStatus): string {
  return LABELS[status]
}

const BADGES: Readonly<Record<CityStatus, CityStatusResult['badge']>> = {
  blocked: '!',
  celebrating: '✓',
  idle: undefined,
  sleeping: undefined,
  unavailable: undefined,
  waiting: '?',
  working: '⚒'
}

export function cityStatusBadge(status: CityStatus): CityStatusResult['badge'] {
  return BADGES[status]
}

function hasAnimation(entity: LunarEntity, ...values: readonly string[]): boolean {
  return values.includes(entity.animation.toLowerCase())
}

export function resolveCityStatus(entity: LunarEntity, now = Date.now()): CityStatusResult {
  if (entity.authority !== 'authoritative') {
    return { active: false, status: 'unavailable', urgent: false }
  }

  const sourceState = entity.sourceState?.toLowerCase()
  const signals = entity.signals

  const blocked =
    signals?.blocked === true ||
    sourceState === 'blocked' ||
    sourceState === 'failed' ||
    hasAnimation(entity, 'blocked', 'failed')

  const working =
    signals?.working === true || sourceState === 'running' || sourceState === 'working' || hasAnimation(entity, 'work')

  const celebrating =
    signals?.celebrating === true ||
    sourceState === 'completed' ||
    sourceState === 'done' ||
    sourceState === 'merged' ||
    hasAnimation(entity, 'done')

  const waiting =
    signals?.waiting === true ||
    ['waiting', 'waiting_for_resource', 'resource_wait', 'review'].includes(sourceState ?? '') ||
    hasAnimation(entity, 'wait')

  if (blocked) {return { active: true, badge: '!', status: 'blocked', urgent: true }}

  if (working) {return { active: true, badge: '⚒', status: 'working', urgent: false }}

  if (celebrating) {return { active: true, badge: '✓', status: 'celebrating', urgent: false }}

  if (waiting) {return { active: true, badge: '?', status: 'waiting', urgent: true }}

  const lastActivityAt = signals?.lastActivityAt ?? entity.observedAt
  const sleeping = Number.isFinite(lastActivityAt) && now - lastActivityAt >= CITY_STALE_AFTER_MS

  return { active: false, status: sleeping ? 'sleeping' : 'idle', urgent: false }
}

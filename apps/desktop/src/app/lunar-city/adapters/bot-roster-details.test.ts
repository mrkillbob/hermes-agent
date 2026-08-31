import { describe, expect, it, vi } from 'vitest'

import type { DesktopAgentRoster } from '@/global'

import type { LunarEntity } from '../model'

import { enrichBotRosterEntities } from './bot-roster-details'
import { normalizeRoster } from './fleet'

function roster(profiles: readonly string[], connectionId = 'local'): DesktopAgentRoster {
  return {
    agents: profiles.map(profile => ({
      connectionId,
      connectionKind: 'local',
      connectionLabel: connectionId === 'local' ? 'This device' : 'Hermes Desktop',
      handle: `@${profile}-${connectionId}`,
      profile
    })),
    sources: [
      {
        connectionId,
        kind: 'local',
        label: connectionId === 'local' ? 'This device' : 'Hermes Desktop',
        reachable: connectionId === 'local'
      }
    ]
  }
}

function entities(source: DesktopAgentRoster): readonly LunarEntity[] {
  return normalizeRoster(source, { observedAt: 42 }).entities
}

describe('complete Hermes Bots roster details', () => {
  it('retains all 91 fleet profiles from the 113-item live UI while carrying its 19 groups as memberships', async () => {
    const localProfiles = Array.from({ length: 90 }, (_, index) => `worker-${index}`)
    const local = roster(localProfiles)
    const unavailable = roster(['test-contract-steward'], 'desktop-source')
    const source: DesktopAgentRoster = {
      agents: [...local.agents, ...unavailable.agents],
      sources: [...local.sources, ...unavailable.sources]
    }
    const liveGroupRooms = Object.fromEntries(
      Array.from({ length: 19 }, (_, index) => [
        `id:group-${index}`,
        {
          members: [{ connectionId: 'local', name: 'worker-0' }],
          name: `Live group ${index}`,
          roomId: `group-${index}`
        }
      ])
    )
    const request = vi.fn(async (connectionId: string, profile: string) => ({
      profiles:
        connectionId === 'local'
          ? localProfiles.map((name, index) => ({
              name,
              ...(index === 0 ? { ui_meta: { 'hermes-bots-groups': { rooms: liveGroupRooms, version: 3 } } } : {})
            }))
          : [{ name: profile }]
    }))

    const result = await enrichBotRosterEntities(source, entities(source), request)

    expect(result).toHaveLength(91)
    expect(new Set(result.map(entity => entity.key)).size).toBe(91)
    expect(result.find(entity => entity.identity.profile === 'worker-0')?.presentation?.groups).toHaveLength(19)
    expect(request).toHaveBeenCalledTimes(2)
    expect(request).toHaveBeenCalledWith('desktop-source', 'test-contract-steward')
    expect(request).toHaveBeenCalledWith('local', 'worker-0')
    expect(result.find(entity => entity.identity.connectionId === 'desktop-source')).toMatchObject({
      authority: 'stale',
      destination: 'unavailable',
      presentation: { profileHandle: '@test-contract-steward-desktop-source', sourceLabel: 'Hermes Desktop' }
    })
  })

  it('uses configured title precedence, retains every direct and projected group, and maps the primary group explicitly', async () => {
    const source = roster(['scientific-validator'])
    const request = vi.fn(async () => ({
      profiles: [
        {
          display_name: 'Display fallback',
          name: 'scientific-validator',
          title: 'Core title',
          ui_meta: {
            'hermes-bots': {
              group: 'Research Lab',
              groups: ['Engineering Guild', 'Research Lab', 'Research Review Board'],
              title: 'Scientific Validator'
            },
            'hermes-bots-groups': {
              rooms: {
                'id:engineering': {
                  members: [{ connectionId: 'local', name: 'scientific-validator' }],
                  name: 'Engineering Guild',
                  roomId: 'engineering'
                }
              },
              version: 3
            }
          }
        }
      ]
    }))

    const [entity] = await enrichBotRosterEntities(source, entities(source), request)

    expect(entity?.destination).toBe('project')
    expect(entity?.presentation).toMatchObject({
      configuredTitle: 'Scientific Validator',
      groups: [
        { id: 'engineering', name: 'Engineering Guild' },
        { id: 'research-lab', name: 'Research Lab' },
        { id: 'research-review-board', name: 'Research Review Board' }
      ],
      placement: { overflow: false, primaryGroupId: 'engineering' },
      profileHandle: '@scientific-validator-local',
      sourceLabel: 'This device'
    })
    expect(entity?.position).toBeDefined()
  })

  it('keeps same-named profiles on separate sources distinct and never uses another source metadata', async () => {
    const left = roster(['steward'], 'local')
    const right = roster(['steward'], 'desktop-source')
    const source = { agents: [...left.agents, ...right.agents], sources: [...left.sources, ...right.sources] }
    const request = vi.fn(async (connectionId: string) => ({
      profiles: [
        {
          name: 'steward',
          ui_meta: { 'hermes-bots': { title: connectionId === 'local' ? 'Ready Steward' : 'Offline Steward' } }
        }
      ]
    }))

    const result = await enrichBotRosterEntities(source, entities(source), request)

    expect(result.map(entity => entity.presentation?.configuredTitle)).toEqual(['Offline Steward', 'Ready Steward'])
    expect(new Set(result.map(entity => entity.key)).size).toBe(2)
  })

  it('shape-guards malformed metadata and metadata failure without dropping or authorizing a fleet row', async () => {
    const source = roster(['alpha', 'beta'])
    const malformed = await enrichBotRosterEntities(source, entities(source), async () => ({
      profiles: [
        {
          name: 'alpha',
          ui_meta: {
            'hermes-bots': {
              groups: ['Research Lab', null, 7, 'x'.repeat(500)],
              title: { untrusted: true }
            },
            'hermes-bots-groups': { rooms: 'not-an-object' }
          }
        }
      ]
    }))
    const failed = await enrichBotRosterEntities(source, entities(source), async () => {
      throw new Error('offline')
    })

    expect(malformed).toHaveLength(2)
    expect(malformed[0]?.presentation?.configuredTitle).toBeUndefined()
    expect(malformed[0]?.presentation?.groups).toEqual([{ id: 'research-lab', name: 'Research Lab' }])
    expect(failed).toHaveLength(2)
    expect(failed.every(entity => entity.authority === 'authoritative')).toBe(true)
    expect(failed.every(entity => entity.destination === 'garden')).toBe(true)
    expect(failed.every(entity => entity.presentation?.metadata.state === 'unavailable')).toBe(true)
    expect(failed.find(entity => entity.identity.profile === 'beta')?.presentation?.groups).toEqual([])
  })

  it('assigns collision-safe stable slots independent of roster ordering and flags bounded overflow for aggregate LOD', async () => {
    const profiles = Array.from({ length: 40 }, (_, index) => `engineer-${index}`)
    const source = roster(profiles)
    const richRows = profiles.map(name => ({
      name,
      ui_meta: { 'hermes-bots': { groups: ['Engineering Guild'] } }
    }))
    const forward = await enrichBotRosterEntities(source, entities(source), async () => ({ profiles: richRows }))
    const reversedRoster = { ...source, agents: [...source.agents].reverse() }
    const reverse = await enrichBotRosterEntities(reversedRoster, entities(reversedRoster), async () => ({
      profiles: [...richRows].reverse()
    }))
    const forwardByKey = new Map(forward.map(entity => [entity.key, entity]))

    expect(new Set(forward.map(entity => JSON.stringify(entity.position))).size).toBe(40)
    expect(
      reverse.every(
        entity => JSON.stringify(entity.position) === JSON.stringify(forwardByKey.get(entity.key)?.position)
      )
    ).toBe(true)
    expect(forward.filter(entity => entity.presentation?.placement.overflow)).toHaveLength(16)
    expect(forward.filter(entity => entity.presentation?.placement.lodHint === 1)).toHaveLength(16)
  })

  it('keeps every bounded placement unique across capped-ring and former 256-slot collision boundaries', async () => {
    const profiles = Array.from({ length: 320 }, (_, index) => `worker-${index.toString().padStart(3, '0')}`)
    const source = roster(profiles)
    const result = await enrichBotRosterEntities(source, entities(source), async () => ({
      profiles: profiles.map(name => ({ name }))
    }))
    const bySlot = new Map(result.map(entity => [entity.presentation!.placement.slot, entity.position]))

    expect(new Set(result.map(entity => entity.presentation?.placement.slot)).size).toBe(320)
    expect(new Set(result.map(entity => JSON.stringify(entity.position))).size).toBe(320)
    expect(bySlot.get(228)).not.toEqual(bySlot.get(240))
    expect(bySlot.get(255)).not.toEqual(bySlot.get(256))
    expect(bySlot.get(319)).toBeDefined()
  })

  it('does not bind unscoped projected members to another exact connection', async () => {
    const source = roster(['steward'], 'desktop-source')

    const [entity] = await enrichBotRosterEntities(source, entities(source), async () => ({
      profiles: [
        {
          name: 'steward',
          ui_meta: {
            'hermes-bots-groups': {
              rooms: {
                'id:local-only': {
                  members: [{ connectionId: 'local', name: 'steward' }],
                  name: 'Research Lab',
                  roomId: 'local-only'
                }
              },
              version: 3
            }
          }
        }
      ]
    }))

    expect(entity?.presentation?.groups).toEqual([])
    expect(entity?.destination).toBe('unavailable')
  })
})

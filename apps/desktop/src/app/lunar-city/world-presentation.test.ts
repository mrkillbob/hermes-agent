import { describe, expect, it } from 'vitest'

import { resolveWorldAnimation } from './world-animation'
import type { WorldEvent } from './world-events'
import { resolveWorldPresentation } from './world-presentation'

const event: WorldEvent = {
  actionKinds: ['inspect'],
  facts: {},
  id: 'blocked',
  kind: 'task.blocked',
  occurredAt: 1,
  receivedAt: 1,
  scope: 'task',
  severity: 'warning',
  source: 'kanban',
  sourceRef: { taskId: 'task-1' },
  title: 'Blocked',
  transition: true
}

describe('display-only presentation animation tags', () => {
  it('preserves narrative display semantics without asset availability input', () => {
    const tags = resolveWorldPresentation(event, []).animationTags
    expect(tags).toEqual(['task.blocked', 'crisis.fire', 'repair', 'extinguish'])
  })

  it('preserves display tags on additional responder and celebration actors', () => {
    expect(resolveWorldPresentation(event, []).npcActivities[1].animationTags).toEqual(['repair', 'extinguish'])
    expect(resolveWorldPresentation({ ...event, kind: 'pr.merged_stable' }, []).npcActivities[1].animationTags)
      .toEqual(['celebration.citywide', 'dance'])
  })

  it('uses explicit alert semantics for an unknown event without a synthetic clip fallback tag', () => {
    const presentation = resolveWorldPresentation({ ...event, kind: 'unknown.event' }, [])

    expect(presentation.animationTags).toEqual(['alert'])
    expect(presentation.sceneTag).toBe('alert.unclassified')
  })

  it('copies metadata so mutations cannot change future scene resolution', () => {
    const presentation = resolveWorldPresentation(event, [])
    presentation.animationTags.push('local-only')
    presentation.npcActivities[0].animationTags.push('actor-only')

    expect(resolveWorldPresentation(event, []).animationTags).toEqual([
      'task.blocked', 'crisis.fire', 'repair', 'extinguish'
    ])
    expect(presentation.animationTags).not.toContain('actor-only')
  })

  it('keeps clip selection independent of display tags', () => {
    const presentation = resolveWorldPresentation(event, [])
    const responder = presentation.npcActivities[1]

    expect(resolveWorldAnimation(responder, presentation).clip).toBe('repair')
    expect(resolveWorldAnimation({ ...responder, animationTags: [] }, presentation).clip).toBe('repair')
    expect(resolveWorldAnimation({ ...responder, animationTags: ['unknown.display'] }, presentation).clip)
      .toBe('repair')
  })
})

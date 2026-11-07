import { describe, expect, it } from 'vitest'

import { resolveWorldAnimation } from './world-animation'
import type {
  WorldCondition,
  WorldEvent,
  WorldSeverity
} from './world-events'
import { FALLBACK_SCENE, resolveWorldPresentation } from './world-presentation'

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

describe('SCENES coverage for producer-side event kinds', () => {
  // Producer-side event kinds from world-events.ts that should each have a dedicated SCENES entry
  // This enumeration includes:
  // - kanban spec worldKinds (task.blocked, task.block_loop, worker.crashed, worker.gave_up, worker.timed_out,
  //   task.completed, task.recovered, task.removed, task.in_review, task.running, task.ready, task.waiting)
  // - condition kinds (worker.stale from classifyTaskCondition)
  // - agent notice kinds (agent.active, agent.idle, agent.warning, agent.error, agent.success, agent.info)
  // - credits kinds (credits.depleted, credits.low, credits.reset)
  const worldKinds = [
    'task.blocked',
    'task.block_loop',
    'task.completed',
    'task.recovered',
    'task.removed',
    'task.in_review',
    'task.running',
    'task.ready',
    'task.waiting',
    'worker.crashed',
    'worker.gave_up',
    'worker.timed_out',
    'worker.stale',
    'agent.active',
    'agent.idle',
    'agent.warning',
    'agent.error',
    'agent.success',
    'agent.info',
    'credits.depleted',
    'credits.low',
    'credits.reset'
  ]

  // PR/release/gateway/approval kinds from normalizePullRequestEvent, normalizeReleaseEvent, etc.
  const prReleaseKinds = [
    'pr.approved',
    'pr.review_findings',
    'pr.merge_conflict',
    'pr.merged_draft',
    'pr.merged_stable',
    'pr.closed',
    'pr.review_requested',
    'release.succeeded',
    'release.started',
    'release.failed',
    'gateway.disconnected',
    'gateway.connected',
    'gateway.degraded',
    'auth.failed',
    'approval.required',
    'approval.granted',
    'approval.rejected'
  ]

  // FALLBACK_SCENE kinds by design (unclassified_alert and dynamic system.*)
  const fallbackKinds = [
    'system.unclassified_alert'
  ]

  it('resolves every producer-side kind to a dedicated SCENES entry (not FALLBACK_SCENE)', () => {
    const allKinds = [...worldKinds, ...prReleaseKinds]

    for (const kind of allKinds) {
      const presentation = resolveWorldPresentation({ ...event, kind } as WorldEvent, [])
      expect(presentation.sceneTag).not.toBe(FALLBACK_SCENE.sceneTag)
    }
  })

  it('explicitly allows FALLBACK_SCENE for unclassified system alerts', () => {
    // These are by design - future kinds that use system.* namespace resolve to FALLBACK_SCENE
    const presentation = resolveWorldPresentation({
      ...event,
      kind: 'system.unclassified_alert',
      scope: 'city',
      severity: 'warning' as WorldSeverity
    }, [])

    expect(presentation.sceneTag).toBe('alert.unclassified')
    expect(presentation.cosmetic.intensity).toBe(1)
  })
})

describe('new SCENES entries for missing kinds', () => {
  it('renders worker.stale with crisis.worker.stale scene', () => {
    const presentation = resolveWorldPresentation({
      ...event,
      kind: 'worker.stale',
      scope: 'worker',
      severity: 'warning'
    }, [])

    expect(presentation.sceneTag).toBe('crisis.worker.stale')
    expect(presentation.animationTags).toContain('worker.stale')
    expect(presentation.animationTags).toContain('heartbeat.stalled')
    expect(presentation.animationTags).toContain('inspect')
    expect(presentation.animationTags).toContain('waiting')
    expect(presentation.cosmetic.intensity).toBe(1)
    expect(presentation.cosmetic.soundTag).toBe('alert.warning')
    expect(presentation.cosmetic.cameraBeat).toBe('worker_discovery')
    expect(presentation.npcActivities[0].state).toBe('waiting')
  })

  it('renders pr.closed with activity.pr.closed scene', () => {
    const presentation = resolveWorldPresentation({
      ...event,
      kind: 'pr.closed',
      scope: 'district',
      severity: 'info'
    }, [])

    expect(presentation.sceneTag).toBe('activity.pr.closed')
    expect(presentation.animationTags).toContain('pr.closed')
    expect(presentation.animationTags).toContain('review.closed')
    expect(presentation.cosmetic.intensity).toBe(1)
    expect(presentation.cosmetic.soundTag).toBe('alert.info')
    expect(presentation.npcActivities[0].state).toBe('walking')
  })

  it('renders pr.review_requested with activity.pr.review_requested scene', () => {
    const presentation = resolveWorldPresentation({
      ...event,
      kind: 'pr.review_requested',
      scope: 'district',
      severity: 'info'
    }, [])

    expect(presentation.sceneTag).toBe('activity.pr.review_requested')
    expect(presentation.animationTags).toContain('pr.review_requested')
    expect(presentation.animationTags).toContain('inspect')
    expect(presentation.cosmetic.intensity).toBe(1)
    expect(presentation.cosmetic.soundTag).toBe('alert.info')
    expect(presentation.npcActivities[0].state).toBe('inspecting')
  })

  it('renders agent.success with milestone.agent.success scene', () => {
    const presentation = resolveWorldPresentation({
      ...event,
      kind: 'agent.success',
      scope: 'building',
      severity: 'success'
    }, [])

    expect(presentation.sceneTag).toBe('milestone.agent.success')
    expect(presentation.animationTags).toContain('agent.success')
    expect(presentation.animationTags).toContain('celebration.local')
    expect(presentation.cosmetic.intensity).toBe(1)
    expect(presentation.npcActivities[0].state).toBe('celebrating')
  })

  it('renders agent.info with activity.agent.info scene', () => {
    const presentation = resolveWorldPresentation({
      ...event,
      kind: 'agent.info',
      scope: 'building',
      severity: 'info'
    }, [])

    expect(presentation.sceneTag).toBe('activity.agent.info')
    expect(presentation.animationTags).toContain('agent.info')
    expect(presentation.animationTags).toContain('resting')
    expect(presentation.cosmetic.intensity).toBe(0)
    expect(presentation.npcActivities[0].state).toBe('resting')
  })
})

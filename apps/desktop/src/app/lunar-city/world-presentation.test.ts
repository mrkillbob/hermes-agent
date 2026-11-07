import type { AgentNoticePayload } from '@/store/agent-notices'

import type { CompletionEvent } from '../../plugins/kanban/completion-notify'
import type { KanbanTask } from '../../plugins/kanban/types'

import { describe, expect, it } from 'vitest'

import { resolveWorldAnimation } from './world-animation'
import type {
  ApprovalWorldEventInput,
  CreditWorldEventInput,
  GatewayWorldEventInput,
  ProfileLifecycleWorldEventInput,
  PullRequestWorldEventInput,
  ReleaseWorldEventInput,
  WorldCondition,
  WorldEvent,
  WorldSeverity
} from './world-events'
import {
  classifyTaskCondition,
  normalizeAgentNotice,
  normalizeApprovalEvent,
  normalizeCreditEvent,
  normalizeGatewayEvent,
  normalizeKanbanEvent,
  normalizeProfileLifecycleEvent,
  normalizePullRequestEvent,
  normalizeReleaseEvent
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
  // Producer-side enumeration derived by invoking each producer with every discriminant.
  // Keep these fixtures aligned with the producer input unions in world-events.ts.
  const kanbanKinds = [
    'blocked', 'block_loop_detected', 'crashed', 'gave_up', 'timed_out', 'completed', 'done', 'unblocked',
    'archived', 'deleted', 'review', 'running', 'ready', 'scheduled', 'todo', 'triage', 'future_kind'
  ].map(kind => normalizeKanbanEvent('test', {
    id: kind, kind, task_id: `task-${kind}`, payload: {}
  } as CompletionEvent).kind)
  const agentKinds = ['success', 'error', 'warn', 'info', ''].map(level => normalizeAgentNotice({
    id: `notice-${level}`, level, text: 'notice'
  } as AgentNoticePayload)?.kind).filter((kind): kind is string => Boolean(kind))
  const prKinds = (['approved', 'checks_failed', 'closed', 'conflict', 'draft_merged', 'merged', 'review_findings', 'review_requested'] as PullRequestWorldEventInput['status'][])
    .map(status => normalizePullRequestEvent({ id: status, number: 1, repository: 'test/repo', status, title: status }).kind)
  const releaseKinds = (['failed', 'started', 'succeeded'] as ReleaseWorldEventInput['status'][])
    .map(status => normalizeReleaseEvent({ id: status, name: status, status }).kind)
  const gatewayKinds = (['auth_failed', 'connected', 'degraded', 'disconnected', 'failed'] as GatewayWorldEventInput['status'][])
    .map(status => normalizeGatewayEvent({ id: status, profile: 'test', status }).kind)
  const approvalKinds = (['granted', 'rejected', 'required'] as ApprovalWorldEventInput['status'][])
    .map(status => normalizeApprovalEvent({ actionId: status, id: status, sessionId: 'test', status, title: status }).kind)
  const creditKinds = (['depleted', 'low', 'reset'] as CreditWorldEventInput['status'][])
    .map(status => normalizeCreditEvent({ id: status, remainingPercent: 10, status }).kind)
  const profileKinds = (['active', 'blocked', 'crashed', 'idle', 'working'] as ProfileLifecycleWorldEventInput['status'][])
    .map(status => normalizeProfileLifecycleEvent({ id: status, profile: status, role: 'worker', status }).kind)
  const conditionKinds = (['stale', 'fresh'] as const)
    .map((heartbeat, index) => classifyTaskCondition({
      heartbeat_at: index === 0 ? 0 : Date.now(), id: `condition-${heartbeat}`, status: 'running', title: heartbeat
    } as KanbanTask, Date.now() + 180_000)?.kind)
    .filter((kind): kind is string => Boolean(kind))
  const producerKinds = [
    ...kanbanKinds, ...agentKinds, ...prKinds, ...releaseKinds, ...gatewayKinds,
    ...approvalKinds, ...creditKinds, ...profileKinds, ...conditionKinds
  ]

  // FALLBACK_SCENE is intentional for unclassified system alerts and dynamic system.* kinds.
  const fallbackKinds = ['system.unclassified_alert', 'system.future_alert']

  it('resolves every producer-side kind to a dedicated SCENES entry (not FALLBACK_SCENE)', () => {
    for (const kind of producerKinds) {
      const presentation = resolveWorldPresentation({ ...event, kind } as WorldEvent, [])
      expect(presentation.sceneTag, `producer kind ${kind}`).not.toBe(FALLBACK_SCENE.sceneTag)
    }
  })

  it('explicitly allows FALLBACK_SCENE for unclassified system alerts', () => {
    for (const kind of fallbackKinds) {
      const presentation = resolveWorldPresentation({
        ...event, kind, scope: 'city', severity: 'warning' as WorldSeverity
      }, [])
      expect(presentation.sceneTag).toBe('alert.unclassified')
      expect(presentation.cosmetic.intensity).toBe(1)
    }
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

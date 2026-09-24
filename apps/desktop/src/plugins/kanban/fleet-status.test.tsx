import { describe, expect, it } from 'vitest'

import { fleetStatusbarCopy, formatThroughput, groupFleetNodes, metricIsFresh } from './fleet-status'

describe('federated fleet status metrics', () => {
  it('aggregates profile measurements without double-counting tokens', () => {
    const nodes = groupFleetNodes([
      {
        active_load: 1,
        capability: { platform: 'darwin' },
        last_output_duration_ms: 4000,
        last_output_tokens: 240,
        last_output_tps: 60,
        metrics_updated_at: 100,
        node_id: 'mac',
        online: true,
        profile: 'coding',
        expires_at: 200,
        last_seen: 100
      },
      {
        active_load: 0,
        capability: { platform: 'darwin' },
        last_output_duration_ms: 2000,
        last_output_tokens: 40,
        last_output_tps: 20,
        metrics_updated_at: 101,
        node_id: 'mac',
        online: true,
        profile: 'review',
        expires_at: 200,
        last_seen: 101
      }
    ])

    expect(nodes).toHaveLength(1)
    expect(nodes[0].outputTokens).toBe(280)
    expect(nodes[0].outputDurationMs).toBe(6000)
    expect(nodes[0].outputTps).toBeCloseTo(46.67, 2)
  })

  it('formats unavailable and measured throughput distinctly', () => {
    expect(formatThroughput(undefined)).toBe('—')
    expect(formatThroughput(0)).toBe('—')
    expect(formatThroughput(46.67)).toBe('47 t/s')
  })

  it('marks measurements stale after the bounded freshness window', () => {
    expect(metricIsFresh(100, 160)).toBe(true)
    expect(metricIsFresh(100, 221)).toBe(false)
    expect(metricIsFresh(null, 160)).toBe(false)
  })

  it('includes active cards and only fresh computer measurements in the statusbar copy', () => {
    const now = Math.floor(Date.now() / 1000)
    expect(fleetStatusbarCopy({
      enabled: true,
      reachable: true,
      runners: [{
        active_load: 1,
        capability: { platform: 'darwin' },
        last_output_duration_ms: 4000,
        last_output_tokens: 240,
        last_output_tps: 60,
        metrics_updated_at: now,
        node_id: 'mac',
        online: true,
        profile: 'coding',
        expires_at: 200,
        last_seen: now
      }],
      tasks: [{
        task_id: 'task-1',
        title: 'Build',
        status: 'running',
        node_id: 'mac',
        runner_profile: 'coding',
        attempt: 1,
        updated_at: now
      }]
    })).toBe('Kanban 1 · mac 60 t/s')
  })
})

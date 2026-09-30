import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import * as fleetApi from './api'
import { FleetStatusbar, fleetStatusbarCopy, FleetStatusPanel, formatThroughput, groupFleetNodes, metricIsFresh } from './fleet-status'

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
    ], 101)

    expect(nodes).toHaveLength(1)
    expect(nodes[0].outputTokens).toBe(280)
    expect(nodes[0].outputDurationMs).toBe(6000)
    expect(nodes[0].outputTps).toBeCloseTo(46.67, 2)
  })

  it('does not combine stale profile measurements with fresh ones', () => {
    const nodes = groupFleetNodes([
      {
        active_load: 0, capability: { platform: 'darwin' }, last_output_duration_ms: 4000,
        last_output_tokens: 240, last_output_tps: 60, metrics_updated_at: 0, node_id: 'mac',
        online: true, profile: 'stale', expires_at: 300, last_seen: 100
      },
      {
        active_load: 0, capability: { platform: 'darwin' }, last_output_duration_ms: 2000,
        last_output_tokens: 40, last_output_tps: 20, metrics_updated_at: 201, node_id: 'mac',
        online: true, profile: 'fresh', expires_at: 300, last_seen: 201
      }
    ], 201)

    expect(nodes[0].outputTokens).toBe(40)
    expect(nodes[0].outputTps).toBe(20)
  })

  it('reports coordinator availability distinctly in the bottom bar', () => {
    expect(fleetStatusbarCopy({ enabled: true, reachable: false, error: 'offline', runners: [], tasks: [] }))
      .toBe('Kanban · unavailable')
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

  it('renders the bottom-bar contribution with a card title for quick inspection', () => {
    render(<FleetStatusbar status={{ enabled: true, reachable: true, runners: [], tasks: [{
      task_id: 'task-1', title: 'Build', status: 'running', node_id: 'windows',
      runner_profile: 'coding', attempt: 1, updated_at: 100
    }] }} />)

    const button = screen.getByRole('button', { name: 'Federated Kanban runner status' })
    expect(button.textContent).toContain('Kanban 1')
    expect(button.getAttribute('title')).toBeNull()
  })
})

it('shows a sanitized unavailable state on rejection and recovers when refreshed', async () => {
  const query = vi.spyOn(fleetApi, 'fetchFleetStatus')
    .mockRejectedValueOnce(new Error('timeout Authorization: Bearer private-test-marker'))
    .mockResolvedValue({ enabled: true, reachable: true, runners: [], tasks: [] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}><FleetStatusPanel /></QueryClientProvider>)
  expect(await screen.findByText('Coordinator unavailable')).toBeTruthy()
  expect(screen.getByText('Could not reach the coordinator. Refresh to try again.')).toBeTruthy()
  expect(screen.queryByText('Connecting to the shared coordinator…')).toBeNull()
  expect(document.body.textContent).not.toContain('private-test-marker')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh runner pool' }))
  await waitFor(() => expect(screen.getByText('0/0 computers online')).toBeTruthy())
  expect(query).toHaveBeenCalledTimes(2)
  client.clear()
  cleanup()
  query.mockRestore()
})

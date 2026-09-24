import { Button, cn, Codicon, host, useQuery } from '@hermes/plugin-sdk'
import { useMemo } from 'react'

import { fetchFleetStatus, FLEET_STATUS_KEY } from './api'
import type { FleetRunnerStatus, FleetStatusResponse, FleetTaskStatus } from './types'

interface FleetNode {
  activeLoad: number
  models: string[]
  nodeId: string
  online: boolean
  platform: string
  profiles: number
  projects: string[]
  outputDurationMs: number
  outputTokens: number
  outputTps: number | null
  metricsUpdatedAt: number | null
}

const METRIC_MAX_AGE_SECONDS = 120

// The two production supervisors use stable node IDs. Ignore retired smoke
// runners in the shared registry so the pool summary reflects actual machines.
const PRODUCTION_NODE_IDS = new Set(['mac', 'windows'])

export function metricIsFresh(updatedAt: number | null | undefined, nowSeconds = Date.now() / 1000): boolean {
  return typeof updatedAt === 'number' && Number.isFinite(updatedAt) && nowSeconds >= updatedAt && nowSeconds - updatedAt <= METRIC_MAX_AGE_SECONDS
}

export function formatThroughput(tps: number | null | undefined): string {
  return typeof tps === 'number' && Number.isFinite(tps) && tps > 0 ? `${Math.round(tps)} t/s` : '—'
}

export function fleetStatusbarCopy(status: FleetStatusResponse): string {
  if (!status.reachable) {
    return status.error ? 'Kanban · unavailable' : 'Kanban · connecting…'
  }
  const active = status.tasks.filter(task => task.status === 'pending' || task.status === 'running').length
  const nodes = groupFleetNodes(status.runners)
  const throughput = nodes
    .filter(node => metricIsFresh(node.metricsUpdatedAt))
    .map(node => `${node.nodeId} ${formatThroughput(node.outputTps)}`)
  return [`Kanban ${active}`, ...throughput].join(' · ')
}

export function groupFleetNodes(runners: FleetRunnerStatus[], nowSeconds = Date.now() / 1000): FleetNode[] {
  const nodes = new Map<string, FleetNode>()

  for (const runner of runners) {
    if (!PRODUCTION_NODE_IDS.has(runner.node_id)) { continue }

    const existing = nodes.get(runner.node_id) ?? {
      activeLoad: 0,
      models: [],
      nodeId: runner.node_id,
      online: false,
      platform: runner.capability.platform ?? 'unknown',
      profiles: 0,
      projects: [],
      outputDurationMs: 0,
      outputTokens: 0,
      outputTps: null,
      metricsUpdatedAt: null
    }

    existing.activeLoad += runner.active_load
    existing.online ||= runner.online
    existing.profiles += 1
    existing.models.push(...(runner.capability.models ?? []))
    existing.projects.push(...(runner.capability.projects ?? []))
    if (
      typeof runner.last_output_tokens === 'number' && runner.last_output_tokens >= 0 &&
      typeof runner.last_output_duration_ms === 'number' && runner.last_output_duration_ms >= 0 &&
      metricIsFresh(runner.metrics_updated_at, nowSeconds)
    ) {
      existing.outputTokens += runner.last_output_tokens
      existing.outputDurationMs += runner.last_output_duration_ms
      existing.metricsUpdatedAt = Math.max(existing.metricsUpdatedAt ?? 0, runner.metrics_updated_at ?? 0)
    }
    nodes.set(runner.node_id, existing)
  }

  return [...nodes.values()]
    .map(node => ({
      ...node,
      models: [...new Set(node.models)].sort(),
      projects: [...new Set(node.projects)].sort(),
      outputTps: node.outputDurationMs > 0 ? node.outputTokens / (node.outputDurationMs / 1000) : null
    }))
    .sort((a, b) => Number(b.online) - Number(a.online) || a.nodeId.localeCompare(b.nodeId))
}

// Kept as a private alias for callers that only need the existing node copy.
const groupNodes = groupFleetNodes

function taskLabel(task: FleetTaskStatus): string {
  const owner = task.node_id ? `${task.node_id}${task.runner_profile ? ` · ${task.runner_profile}` : ''}` : 'waiting'

  return `${task.title} · ${owner}`
}

function taskThroughputLabel(task: FleetTaskStatus): string {
  if (typeof task.output_tps === 'number' && task.output_tps > 0) {
    return formatThroughput(task.output_tps)
  }
  return task.status === 'running' ? 'measuring…' : '—'
}

function fleetCopy(status: FleetStatusResponse | undefined): string {
  if (!status?.reachable) {
    return status?.error ? 'Coordinator unavailable' : 'Checking coordinator…'
  }

  const nodes = groupNodes(status.runners)
  const online = nodes.filter(node => node.online).length

  return `${online}/${nodes.length} computers online`
}

export function FleetStatusPanel() {
  const { data, isFetching, refetch } = useQuery({
    queryKey: FLEET_STATUS_KEY,
    queryFn: fetchFleetStatus,
    refetchInterval: 5_000,
    retry: false,
    staleTime: 2_000
  })

  const nodes = useMemo(() => groupFleetNodes(data?.runners ?? []), [data?.runners])
  const activeTasks = useMemo(
    () => (data?.tasks ?? []).filter(task => task.status === 'pending' || task.status === 'running'),
    [data?.tasks]
  )

  if (data && !data.enabled) {
    return null
  }

  return (
    <section
      aria-label="Federated runner pool"
      className="mx-4 mb-2 flex shrink-0 flex-col gap-2 rounded-lg border border-(--ui-stroke-secondary) bg-(--ui-bg-quinary) px-3 py-2"
    >
      <div className="flex items-center gap-2 text-[0.6875rem]">
        <Codicon name="server-environment" size="0.8rem" />
        <span className="font-semibold text-foreground">Federated runner pool</span>
        <span
          className={cn(
            'rounded-full px-1.5 py-px text-[0.625rem] tabular-nums',
            data?.reachable ? 'bg-emerald-500/15 text-emerald-400' : 'bg-amber-500/15 text-amber-400'
          )}
        >
          {fleetCopy(data)}
        </span>
        <Button
          aria-label="Refresh runner pool"
          className="ml-auto"
          disabled={isFetching}
          onClick={() => void refetch()}
          size="icon-xs"
          variant="ghost"
        >
          <Codicon name="refresh" size="0.75rem" />
        </Button>
      </div>

      {!data?.reachable ? (
        <p className="text-[0.6875rem] text-(--ui-text-tertiary)">
          {data?.error ?? 'Connecting to the shared coordinator…'}
        </p>
      ) : (
        <>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {nodes.map(node => (
              <div
                className="flex min-w-0 items-center gap-2 rounded-md border border-(--ui-stroke-tertiary) bg-(--ui-bg-elevated) px-2 py-1.5"
                key={node.nodeId}
              >
                <span
                  className={cn(
                    'size-2 shrink-0 rounded-full',
                    node.online ? 'bg-emerald-400' : 'bg-(--ui-text-quaternary)'
                  )}
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[0.6875rem] font-medium text-foreground">{node.nodeId}</div>
                  <div className="truncate text-[0.625rem] text-(--ui-text-tertiary)">
                    {node.online ? node.platform : 'offline'} · {node.profiles} profiles · {node.projects.length}{' '}
                    projects
                    {node.activeLoad > 0 ? ` · ${node.activeLoad} active` : ''} ·{' '}
                    {metricIsFresh(node.metricsUpdatedAt) ? formatThroughput(node.outputTps) : node.activeLoad > 0 ? 'measuring…' : '—'}
                  </div>
                </div>
                <span className="max-w-48 truncate text-right text-[0.625rem] text-(--ui-text-quaternary)">
                  {node.models.join(', ') || 'model discovery pending'}
                </span>
              </div>
            ))}
          </div>

          {activeTasks.length > 0 && (
            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-[0.625rem] text-(--ui-text-tertiary)">
              <span className="font-semibold text-(--ui-text-secondary)">Shared queue</span>
              {activeTasks.slice(0, 4).map(task => (
                <span className="max-w-72 truncate" key={task.task_id} title={taskLabel(task)}>
                  {task.status === 'running' ? '●' : '○'} {taskLabel(task)} · {taskThroughputLabel(task)}
                </span>
              ))}
              {activeTasks.length > 4 && <span>+{activeTasks.length - 4} more</span>}
            </div>
          )}
        </>
      )}
    </section>
  )
}

export function FleetStatusbar({ status }: { status: FleetStatusResponse }) {
  const activeTasks = status.tasks.filter(task => task.status === 'pending' || task.status === 'running')
  const title = activeTasks.length
    ? activeTasks.map(task => `${task.title} · ${task.node_id ?? 'waiting'}`).join('\n')
    : 'No active federated Kanban cards'

  return (
    <button
      aria-label="Federated Kanban runner status"
      className="inline-flex h-full items-center gap-1 rounded-none px-1.5 text-[0.6875rem] tabular-nums text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground"
      onClick={() => host.navigate('/kanban')}
      title={title}
      type="button"
    >
      <Codicon name="project" size="0.7rem" />
      <span>{fleetStatusbarCopy(status)}</span>
    </button>
  )
}

import type { SessionOwnerRoute } from '@/store/session-request-router'

import type { CommandEvidenceKind } from '../command-broker'
import type { EntityIdentity, SourceHealth } from '../model'

export interface InspectorTaskEvidence {
  id: string
  state: string
  title: string
}

export interface InspectorRunEvidence {
  id: string
  outcome?: string
  status: string
}

export interface InspectorDiagnosticEvidence {
  detail: string
  id: string
  severity: string
}

export interface InspectorCommentEvidence {
  author: string
  body: string
  createdAt: number
  id: string
}

export interface InspectorEventEvidence {
  at: number
  id: string
  kind: string
  summary: string
}

export interface InspectorLogEvidence {
  content: string
  exists: boolean
  truncated: boolean
}

export interface InspectorAttachmentEvidence {
  filename: string
  id: string
  size?: number
}

export interface InspectorSubagentEvidence {
  costUsd?: number
  durationSeconds?: number
  filesRead: readonly string[]
  filesWritten: readonly string[]
  goal: string
  id: string
  state: string
  streamTail: readonly string[]
}

export interface InspectorBlockerEvidence {
  detail: string
  kind: string
}

/** Complete UI route for opening the exact owning standard session. */
export interface InspectorSessionTarget extends SessionOwnerRoute {
  runtimeSessionId?: string
  sessionId: string
  storedSessionId?: string
}

export interface EntityInspectorData {
  attachments?: readonly InspectorAttachmentEvidence[]
  blocker?: InspectorBlockerEvidence
  comments?: readonly InspectorCommentEvidence[]
  diagnostics?: readonly InspectorDiagnosticEvidence[]
  events?: readonly InspectorEventEvidence[]
  identity: EntityIdentity
  logTail?: InspectorLogEvidence
  owningSession?: InspectorSessionTarget
  run?: InspectorRunEvidence
  source: SourceHealth
  subagent?: InspectorSubagentEvidence
  task?: InspectorTaskEvidence
}

export interface EntityInspectorProps {
  data: EntityInspectorData
  onInspectEvidence?: (kind: CommandEvidenceKind, identity: EntityIdentity) => void
  onOpenSession?: (target: InspectorSessionTarget) => void
}

function label(value: string): string {
  return `${value.slice(0, 1).toUpperCase()}${value.slice(1)}`
}

function IdentityRows({ identity }: { identity: EntityIdentity }) {
  const rows: readonly [string, string | undefined][] = [
    ['Kind', identity.kind],
    ['Connection', identity.connectionId],
    ['Profile', identity.profile],
    ['Session', identity.kind === 'session' || identity.kind === 'subagent' ? identity.sessionId : undefined],
    ['Subagent', identity.kind === 'subagent' ? identity.subagentId : undefined],
    ['Board', identity.kind === 'kanban' ? identity.board : undefined],
    ['Task', identity.kind === 'kanban' ? identity.taskId : undefined],
    ['Run', identity.kind === 'kanban' ? identity.runId : undefined],
    ['Worker', identity.kind === 'kanban' ? identity.workerId : undefined]
  ]

  return (
    <dl>
      {rows.flatMap(([name, value]) =>
        value ? [<dt key={`${name}-term`}>{name}</dt>, <dd key={`${name}-value`}>{value}</dd>] : []
      )}
    </dl>
  )
}

function Region({ children, name }: { children: React.ReactNode; name: string }) {
  return (
    <section aria-label={name} role="region">
      <h3>{name}</h3>
      {children}
    </section>
  )
}

function exactOwningSession(
  identity: EntityIdentity,
  target: InspectorSessionTarget | undefined
): InspectorSessionTarget | undefined {
  if (
    !target?.connectionId.trim() ||
    !target.profile.trim() ||
    !target.sessionId.trim() ||
    target.connectionId !== identity.connectionId ||
    target.profile !== identity.profile ||
    (target.storedSessionId !== undefined && !target.storedSessionId.trim()) ||
    (target.runtimeSessionId !== undefined && !target.runtimeSessionId.trim())
  ) {
    return undefined
  }

  if ((identity.kind === 'session' || identity.kind === 'subagent') && target.sessionId !== identity.sessionId) {
    return undefined
  }

  return target
}

export function EntityInspector({ data, onInspectEvidence, onOpenSession }: EntityInspectorProps) {
  const targetLabel = data.task?.id ?? data.identity.kind
  const owningSession = exactOwningSession(data.identity, data.owningSession)

  return (
    <aside aria-label="Lunar City entity inspector">
      <h2>Entity inspector</h2>

      <Region name="Identity">
        <IdentityRows identity={data.identity} />
      </Region>

      <Region name="Source evidence">
        <dl>
          <dt>Source</dt>
          <dd>{data.source.source}</dd>
          <dt>Authority</dt>
          <dd>{label(data.source.authority)}</dd>
          <dt>Last observed</dt>
          <dd>
            <time dateTime={new Date(data.source.observedAt).toISOString()}>
              {new Date(data.source.observedAt).toLocaleString()}
            </time>
          </dd>
          {data.source.error ? (
            <>
              <dt>Source error</dt>
              <dd>{data.source.error}</dd>
            </>
          ) : null}
        </dl>
      </Region>

      {data.task ? (
        <Region name="Task">
          <dl>
            <dt>ID</dt>
            <dd>{data.task.id}</dd>
            <dt>Title</dt>
            <dd>{data.task.title}</dd>
            <dt>State</dt>
            <dd>{data.task.state}</dd>
          </dl>
        </Region>
      ) : null}

      {data.run ? (
        <Region name="Run">
          <dl>
            <dt>ID</dt>
            <dd>{data.run.id}</dd>
            <dt>Status</dt>
            <dd>{data.run.status}</dd>
            {data.run.outcome ? (
              <>
                <dt>Outcome</dt>
                <dd>{data.run.outcome}</dd>
              </>
            ) : null}
          </dl>
        </Region>
      ) : null}

      {data.diagnostics?.length ? (
        <Region name="Diagnostics">
          <ul>
            {data.diagnostics.map(item => (
              <li key={item.id}>
                <strong>{label(item.severity)}</strong>: {item.detail}
              </li>
            ))}
          </ul>
          {onInspectEvidence ? (
            <button
              aria-label={`Inspect diagnostics for ${targetLabel}`}
              onClick={() => onInspectEvidence('diagnostics', data.identity)}
              type="button"
            >
              Inspect diagnostics
            </button>
          ) : null}
        </Region>
      ) : null}

      {data.comments?.length ? (
        <Region name="Comments">
          <ul>
            {data.comments.map(item => (
              <li key={item.id}>
                <strong>{item.author}</strong>: {item.body}
              </li>
            ))}
          </ul>
        </Region>
      ) : null}

      {data.events?.length ? (
        <Region name="Events">
          <ul>
            {data.events.map(item => (
              <li key={item.id}>
                <strong>{item.kind}</strong>: {item.summary}
              </li>
            ))}
          </ul>
        </Region>
      ) : null}

      {data.logTail ? (
        <Region name="Log tail">
          <p>{data.logTail.exists ? data.logTail.content : 'No log is available.'}</p>
          {data.logTail.truncated ? <p>Tail is truncated.</p> : null}
        </Region>
      ) : null}

      {data.attachments?.length ? (
        <Region name="Attachments">
          <ul>
            {data.attachments.map(item => (
              <li key={item.id}>
                {item.filename}
                {item.size === undefined ? '' : ` (${item.size} bytes)`}
              </li>
            ))}
          </ul>
        </Region>
      ) : null}

      {data.subagent ? (
        <>
          <Region name="Subagent">
            <dl>
              <dt>ID</dt>
              <dd>{data.subagent.id}</dd>
              <dt>Goal</dt>
              <dd>{data.subagent.goal}</dd>
              <dt>State</dt>
              <dd>{data.subagent.state}</dd>
              {data.subagent.costUsd === undefined ? null : (
                <>
                  <dt>Cost</dt>
                  <dd>${data.subagent.costUsd.toFixed(2)}</dd>
                </>
              )}
              {data.subagent.durationSeconds === undefined ? null : (
                <>
                  <dt>Duration</dt>
                  <dd>{data.subagent.durationSeconds} seconds</dd>
                </>
              )}
            </dl>
            {data.subagent.streamTail.length ? <p>{data.subagent.streamTail.join('\n')}</p> : null}
          </Region>
          {data.subagent.filesRead.length || data.subagent.filesWritten.length ? (
            <Region name="Files">
              <h4>Read</h4>
              <ul>
                {data.subagent.filesRead.map(file => (
                  <li key={`read-${file}`}>{file}</li>
                ))}
              </ul>
              <h4>Written</h4>
              <ul>
                {data.subagent.filesWritten.map(file => (
                  <li key={`written-${file}`}>{file}</li>
                ))}
              </ul>
            </Region>
          ) : null}
        </>
      ) : null}

      {data.blocker ? (
        <Region name="Blocker">
          <p>
            <strong>{data.blocker.kind}</strong>: {data.blocker.detail}
          </p>
        </Region>
      ) : null}

      {owningSession && onOpenSession ? (
        <button
          aria-label={`Open session ${owningSession.sessionId} on connection ${owningSession.connectionId} with profile ${owningSession.profile}`}
          onClick={() => onOpenSession(owningSession)}
          type="button"
        >
          Open owning session
        </button>
      ) : null}
    </aside>
  )
}

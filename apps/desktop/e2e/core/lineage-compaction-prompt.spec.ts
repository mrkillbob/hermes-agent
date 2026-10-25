/**
 * An acknowledged redirect remains a single human prompt across two real
 * in-place compactions. Todo state survives as tool data; it must never become
 * a synthetic user prompt or select a scripted provider turn (#121088, PR402).
 * The threshold is calibrated from a real request before the scenario starts.
 */
import fs from 'node:fs'
import path from 'node:path'
import { DatabaseSync } from 'node:sqlite'

import { expect, type Page, test } from '@playwright/test'

import {
  coreAppEnv,
  createCoreSandbox,
  currentSessionId,
  launchCoreApp,
  recordWebSockets,
  renderedTranscript,
  send,
  waitForInteractive,
  writeProviderHome
} from './harness'
import { installDuplicateSampler } from './oracle'
import { gate, startScriptedProvider } from './provider'

const nonce = Math.random()
  .toString(36)
  .slice(2, 8)
  .replace(/[^a-z0-9]/g, 'x')
  .padEnd(4, 'q')

const U = (n: number) => `U${n}-${nonce}`
const A = (n: number) => `A${n}-${nonce}`
const TODO_PROVENANCE = 'hermes_compaction_todo_state_v1'

interface Todo {
  id: string
  content: string
  status: string
  parent?: string
}

function todos(marker: number): Todo[] {
  return [
    { id: '1', content: `${U(marker)} ${marker === 5 ? 'draft' : 'ship'} the report`, status: 'in_progress' },
    { id: '2', content: 'preserve the finished subtask', status: marker === 5 ? 'completed' : 'cancelled', parent: '1' }
  ]
}

function todoState(
  content: string
): { provenance?: string; todos?: Todo[]; revision?: number; reference?: string } | undefined {
  try {
    return JSON.parse(content)
  } catch {
    return undefined
  }
}

function hasTodoState(content: string, marker: string): boolean {
  const data = todoState(content)

  return data?.provenance === TODO_PROVENANCE && data.todos?.some(todo => todo.content.includes(marker)) === true
}

// Token budget (rough estimator ≈ chars/4) above the calibrated base request:
//   history 2×24k chars ≈ 12k  < X=18k (turn-start preflight must NOT fire)
//   history + round ≈ 24k     >= X     (compaction #1 after the U3 tool round)
//   summary + tail(round) ≈ 13.5k < X  (no re-fire after #1 / at the redirect)
//   2 rounds ≈ 25.5k          >= X     (compaction #2 after the U4 tool round)
const HISTORY_CHARS = 24_000
const ROUND_CHARS = 48_000
const HEADROOM_TOKENS = 18_000

function filler(tag: string, chars: number): string {
  const out: string[] = []
  let len = 0

  for (let i = 0; len < chars; i++) {
    const word = `${tag}${i}`
    out.push(word)
    len += word.length + 1
  }

  return out.join(' ')
}

const compressionYaml = (thresholdTokens: number) =>
  [
    'compression:',
    '  in_place: true',
    `  threshold_tokens: ${thresholdTokens}`,
    '  tail_mode: legacy',
    '  protect_first_n: 0',
    '  protect_last_n: 2',
    // todo_list is deferred behind tool_search by default; the scripted model calls it directly.
    'tools:',
    '  tool_search:',
    '    defer: []',
    ''
  ].join('\n')

function viewport(page: Page) {
  return page.locator('[data-slot="aui_thread-viewport"]').filter({ visible: true }).first()
}

async function settled(page: Page) {
  await expect
    .poll(() => page.locator('[data-slot="composer-root"] button[aria-label="Stop"]').count(), {
      timeout: 90_000,
      message: 'turn never settled'
    })
    .toBe(0)
}

async function openSession(page: Page, id: string) {
  await page.evaluate(sid => {
    window.location.hash = sid ? `#/${encodeURIComponent(sid)}` : '#/'
  }, id)
  await expect.poll(() => currentSessionId(page)).toBe(id)
}

interface Row {
  active: number
  content: string
  id: number
  role: string
}

function sessionRows(hermesHome: string, sid: string): Row[] {
  const db = new DatabaseSync(path.join(hermesHome, 'state.db'), { readOnly: true })

  try {
    return db
      .prepare(
        "SELECT id, role, COALESCE(active, 1) AS active, COALESCE(content, '') AS content FROM messages WHERE session_id = ? ORDER BY id"
      )
      .all(sid) as unknown as Row[]
  } finally {
    db.close()
  }
}

/** U4 as more than one user bubble, or a U4 user bubble after the final reply A6. */
function dupShape(bubbles: { role: string; text: string }[]) {
  const lastA6 = bubbles.findLastIndex(b => b.role === 'assistant' && b.text.includes(A(6)))
  const afterReply = lastA6 >= 0 && bubbles.slice(lastA6 + 1).some(b => b.role === 'user' && b.text.includes(U(4)))
  const userCopies = bubbles.filter(b => b.role === 'user' && b.text.includes(U(4))).length

  return {
    dup: afterReply || userCopies > 1,
    afterReply,
    userCopies,
    tail: bubbles.slice(-4).map(b => `${b.role}: ${b.text.slice(0, 40)}`)
  }
}

async function samplerHits(page: Page, marker: string): Promise<{ bubbles: string[]; count: number }[]> {
  return page.evaluate(
    m =>
      ((window as any).__coreSampler?.violations ?? [])
        .filter((v: any) => v.marker === m)
        .map((v: any) => ({ bubbles: v.bubbles, count: v.count })),
    marker
  )
}

test('an acknowledged redirect prompt renders once after in-place compaction inside its turn (#121088)', async () => {
  test.setTimeout(180_000)
  const provider = await startScriptedProvider()
  const sandbox = createCoreSandbox('lcp')
  writeProviderHome(sandbox.hermesHome, provider.url, compressionYaml(250_000))
  const { app, page } = await launchCoreApp(coreAppEnv(sandbox))
  const diagnostic: string[] = []
  page.on('console', msg => {
    if (msg.text().startsWith('TASK26_RECONCILE')) diagnostic.push(msg.text())
  })
  const ws = recordWebSockets(page)
  const hold = gate()

  try {
    await waitForInteractive(app, page)
    await installDuplicateSampler(page)

    // ── Calibration session: measure the real base request, then write the threshold.
    provider.script(U(0), [{ text: [`${A(0)} `, 'calibrated'] }])
    await send(page, `${U(0)} calibrate`, 'Enter', ws)
    await expect(viewport(page)).toContainText(A(0), { timeout: 60_000 })
    await settled(page)
    const calibration = provider.completions.find(c => c.marker === U(0))
    expect(calibration, 'calibration request reached the provider').toBeTruthy()
    const base = Math.ceil(JSON.stringify({ m: calibration!.body.messages, t: calibration!.body.tools }).length / 4)
    writeProviderHome(sandbox.hermesHome, provider.url, compressionYaml(base + HEADROOM_TOKENS))

    // ── Scenario session (fresh agent reads the calibrated config).
    await openSession(page, '')
    await expect(viewport(page)).not.toContainText(A(0))

    // Summary requests are keyed by their first marker (U1 / U2 here), so these
    // text-only replies double as the compaction summaries.
    provider.script(U(1), [{ text: [`${A(1)} `, 'short ', 'reply'] }])
    provider.script(U(2), [{ text: [`${A(2)} `, 'short ', 'reply'] }])

    for (const n of [1, 2]) {
      await send(page, `${U(n)} ${filler(`f${n}w`, HISTORY_CHARS)}`, 'Enter', ws)
      await expect(viewport(page)).toContainText(A(n), { timeout: 60_000 })
      await settled(page)
    }

    const sid = await currentSessionId(page)
    expect(sid, 'scenario session id').not.toBe('')

    provider.script(U(3), [
      {
        text: [`${A(3)}i `, filler('p', ROUND_CHARS)],
        toolCalls: [
          {
            name: 'todo_list',
            args: { todos: todos(5) }
          }
        ]
      },
      { text: [`${A(5)} `, 'slow ', 'after ', 'compaction'], holdAfterFirstChunk: hold }
    ])
    provider.script(U(4), [
      {
        text: [`${A(4)}i `, filler('q', ROUND_CHARS)],
        toolCalls: [
          {
            name: 'todo_list',
            args: { todos: todos(6) }
          }
        ]
      },
      { text: [`${A(6)} `, 'after ', 'second ', 'compaction'] }
    ])

    await send(page, `${U(3)} plan it`, 'Enter', ws)
    await expect(viewport(page)).toContainText(A(5), { timeout: 60_000 })

    // Busy submit: the redirect correction, an optimistic row with no row-id receipt.
    await send(page, `${U(4)} change course`, 'Enter', ws)
    await expect
      .poll(() => provider.completions.some(c => c.marker === U(4) && c.sentText.startsWith(A(6)) && c.finished), {
        timeout: 90_000,
        message: 'redirect continuation with preserved todo data never finished'
      })
      .toBe(true)
    hold.open()
    await settled(page)
    await expect(viewport(page)).toContainText(A(6), { timeout: 30_000 })

    // Two in-place compactions commit inside the turns without adding human rows.
    const agentLog = fs.readFileSync(path.join(sandbox.hermesHome, 'logs', 'agent.log'), 'utf8')

    const committed = agentLog
      .split('\n')
      .filter(l => l.includes(sid) && l.includes('"commit_status":"committed"') && l.includes('in_place_committed'))

    expect(committed.length, 'two in-place compactions committed for the scenario session').toBe(2)

    const summaries = provider.completions.filter(c =>
      JSON.stringify(c.body.messages ?? '').includes('context checkpoint')
    )

    expect(
      summaries.map(c => c.finished && !c.aborted),
      'two summary requests answered'
    ).toEqual([true, true])
    for (const [turn, reply, marker] of [
      [3, 5, 5],
      [4, 6, 6]
    ]) {
      const continuation = provider.completions.find(c => c.marker === U(turn) && c.sentText.startsWith(A(reply)))
      expect(continuation, 'the real human turn owns its post-compaction continuation').toBeTruthy()
      const messages = continuation!.body.messages as {
        role: string
        content?: string
        tool_call_id?: string
        tool_calls?: { id: string; function: { name: string; arguments: string } }[]
      }[]
      const states = messages.filter(m => m.role === 'tool' && hasTodoState(m.content ?? '', U(marker)))
      expect(states).toHaveLength(1)
      expect(todoState(states[0].content ?? '')).toMatchObject({
        todos: todos(marker),
        revision: turn - 2,
        reference: expect.stringContaining('not a new user instruction')
      })
      const paired = messages[messages.indexOf(states[0]) - 1]
      expect(paired.role).toBe('assistant')
      expect(todoState(states[0].content ?? '')?.todos).toEqual(todos(marker))
      expect(todoState(states[0].content ?? '')?.reference).toContain('latest human request')
      const call = paired.tool_calls
      expect(call).toHaveLength(1)
      expect(call![0]).toMatchObject({ id: states[0].tool_call_id, function: { name: 'todo_list' } })
      expect(JSON.parse(call![0].function.arguments)).toEqual({})
      expect(messages.some(m => m.role === 'user' && (m.content ?? '').includes(U(marker)))).toBe(false)
    }

    const active = sessionRows(sandbox.hermesHome, sid).filter(r => r.active === 1)
    const users = active.filter(r => r.role === 'user')
    const committedU4 = users.filter(r => r.content === `${U(4)} change course`)
    expect(committedU4.length, 'the correction is committed exactly once in active history').toBe(1)
    const s2 = active.find(r => r.role === 'tool' && hasTodoState(r.content, U(6)))
    expect(s2, 'complete todo state S2 is active tool data').toBeTruthy()
    expect(todoState(s2!.content)).toMatchObject({ todos: todos(6), revision: 2 })
    const a6 = active.find(r => r.role === 'assistant' && r.content.startsWith(A(6)))
    expect(a6, 'final reply A6 is stored').toBeTruthy()
    expect(committedU4[0].id < s2!.id && s2!.id < a6!.id, 'stored order: U4 < S2 < A6').toBe(true)
    const all = sessionRows(sandbox.hermesHome, sid)

    expect(
      all.some(r => r.role === 'tool' && hasTodoState(r.content, U(5))),
      'tool state S1 from compaction #1 remains recoverable'
    ).toBe(true)

    // Observation 1: the settled live view. A turn-settle refresh may already
    // have reconciled the stored history (timing-dependent), so it is part of
    // the symptom window rather than an ordinary expect.
    const live = dupShape((await renderedTranscript(page)).bubbles)

    // ── Refresh through preserveLocalPendingTurnMessages: switch away and back.
    await openSession(page, '')
    await expect(viewport(page)).not.toContainText(A(6))
    // A separate stored-only system notice supplies a fetched-history witness.
    // Existing reply text and every human row/receipt remain unchanged.
    const refreshWitness = `persisted-refresh-${nonce}`
    const db = new DatabaseSync(path.join(sandbox.hermesHome, 'state.db'))

    try {
      const inserted = db
        .prepare("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'system', ?, ?)")
        .run(sid, refreshWitness, Date.now() / 1000)
      expect(Number(inserted.changes)).toBe(1)
    } finally {
      db.close()
    }

    await openSession(page, sid)
    await expect(viewport(page)).toContainText(refreshWitness)
    await expect(viewport(page)).toContainText(A(6))

    // Observation 2: the switch-back view; observation 3: every sampled frame
    // since the redirect (the in-page sampler records transient duplicates).
    const switched = dupShape((await renderedTranscript(page)).bubbles)
    const hits = await samplerHits(page, U(4))
    const symptom =
      live.dup ||
      switched.dup ||
      live.userCopies !== 1 ||
      switched.userCopies !== 1 ||
      hits.some(h => h.bubbles.length > 1)
    // ── Reload (no local optimistic state): stored history alone renders U4 once.
    await page.reload()
    await waitForInteractive(app, page)
    await expect(viewport(page)).toContainText(A(6), { timeout: 60_000 })
    const reloaded = await renderedTranscript(page)
    expect(
      reloaded.bubbles.filter(b => b.role === 'user' && b.text.includes(U(4))).length,
      'after reload, stored history renders U4 once'
    ).toBe(1)
    expect(
      reloaded.bubbles
        .slice(reloaded.bubbles.findLastIndex(b => b.text.includes(A(6))) + 1)
        .some(b => b.role === 'user'),
      'after reload, no user bubble after the final reply'
    ).toBe(false)

    expect(
      symptom,
      `the redirect must render once before its reply: ${JSON.stringify({ live, switched, sampler: hits.slice(0, 2) })}`
    ).toBe(false)
  } finally {
    if (test.info().status !== test.info().expectedStatus) {
      const diagnosticDb = new DatabaseSync(path.join(sandbox.hermesHome, 'state.db'), { readOnly: true })
      try {
        await test.info().attach('compaction-user-rows', {
          contentType: 'application/json',
          body: JSON.stringify(
            diagnosticDb
              .prepare(
                "SELECT id,role,active,compacted,display_order,message_uid,timestamp,content FROM messages WHERE role = 'user' AND content LIKE ?"
              )
              .all(`%${U(4)}%`)
          )
        })
        const lines = fs.readFileSync(path.join(sandbox.hermesHome, 'logs', 'agent.log'), 'utf8').split('\n')
        await test
          .info()
          .attach('tail-accounting', {
            contentType: 'text/plain',
            body: lines.filter(l => l.includes('TASK26')).join('\n')
          })
      } finally {
        diagnosticDb.close()
      }

      await test
        .info()
        .attach('reconcile-diagnostics', { contentType: 'application/json', body: JSON.stringify(diagnostic) })
      await test.info().attach('compaction-routing', {
        contentType: 'application/json',
        body: JSON.stringify(
          provider.completions.map(c => ({
            marker: c.marker,
            step: c.step,
            finished: c.finished,
            aborted: c.aborted,
            roles: (c.body.messages as { role: string }[]).map(m => m.role)
          }))
        )
      })
    }
    hold.open()
    await app.close().catch(() => undefined)
    await provider.close()
    sandbox.cleanup()
  }
})

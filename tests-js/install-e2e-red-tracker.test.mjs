import { expect, test } from 'vitest'
import { planTracker, withPendingNote } from '../tests/install/e2e-assets/red-tracker.mjs'

const run = (conclusion) => ({ conclusion, html_url: 'https://example.test/runs/1', head_sha: 'abcdef0123456789', created_at: '2026-09-26T19:24:13Z' })
const leg = (name, conclusion, step) => ({ name, conclusion, html_url: `https://example.test/job/${name.length}`, steps: step ? [{ name: 'checkout', conclusion: 'success' }, { name: step, conclusion }] : [] })

test('one tracker: a red run opens it, the next red run rewrites it, a green run closes it', () => {
  const jobs = [
    leg('windows: installer-script -> hermes-update (v2026.9.24 -> HEAD) / e2e', 'cancelled', 'Install v2026.9.24 (installer-script)'),
    leg('windows: installer-script+desktop -> hermes-update (HEAD -> NEXT) / e2e', 'cancelled', 'Install HEAD (installer-script+desktop)'),
    leg('linux: installer-script -> hermes-update (HEAD -> NEXT) / install & update', 'success'),
    leg('linux: installer-script -> installer-script (HEAD -> NEXT) / install & update', 'skipped'),
  ]

  const opened = planTracker(run('cancelled'), jobs, null)
  expect(opened.action).toBe('open')
  expect(opened.title).toBe('Install & Update E2E matrix is red (2 legs)')
  // Both Windows legs hung in the same driver step: one class, not two.
  expect(opened.body).toContain('**2 × cancelled in step `Install <ref>`**')
  expect(opened.body).not.toContain('linux:')

  expect(planTracker(run('failure'), jobs, { number: 7 }).action).toBe('update')
  expect(planTracker(run('success'), [jobs[2]], { number: 7 }).action).toBe('close')
  expect(planTracker(run('success'), [jobs[2]], null).action).toBe('none')
})

test('each red leg says how often it was red in the previous scheduled runs it ran in', () => {
  const steady = 'windows: a -> hermes-update (v1 -> HEAD) / e2e'
  const flaky = 'windows: b -> hermes-update (v1 -> HEAD) / e2e'
  const fresh = 'windows: c -> hermes-update (v1 -> HEAD) / e2e'
  const renamed = 'windows: d -> hermes-update (v2 -> HEAD) / e2e'
  const jobs = [steady, flaky, fresh, renamed].map((name) => leg(name, 'failure', 'Update A'))
  const history = [
    new Map([[steady, 'failure'], [flaky, 'failure'], [fresh, 'success']]),
    new Map([[steady, 'failure'], [flaky, 'success'], [fresh, 'success']]),
    new Map([[steady, 'failure'], [flaky, 'failure'], [fresh, 'skipped']]),
  ]

  const { body } = planTracker(run('failure'), jobs, null, { history })
  expect(body).toContain(`[${steady}](https://example.test/job/${steady.length}) — red in all 3 previous scheduled run(s)`)
  expect(body).toContain('— intermittent: also red in 2 of the 3 previous scheduled run(s) it ran in')
  // Green where it ran (skipped once) is "new"; a leg absent from every previous run is not called green.
  expect(body).toContain('— new: green in the 2 previous scheduled run(s) it ran in')
  expect(body).toContain('— new: it did not run in the previous 3 scheduled run(s)')
  expect(body).toContain('Over the previous 3 scheduled run(s): 1 leg(s) red every time they ran, 1 intermittent, 2 new.')

  // No history (first run, or the lookup is skipped): the rows are unchanged.
  expect(planTracker(run('failure'), jobs, null).body).not.toContain('previous scheduled run')
})

test('a run that has not finished only annotates an open tracker, and its report replaces the note', () => {
  const pending = { status: 'queued', conclusion: null, html_url: 'https://example.test/runs/2', created_at: '2026-09-27T01:00:00Z', head_sha: 'f'.repeat(16) }
  expect(planTracker(pending, [], null).action).toBe('none')

  const noted = planTracker(pending, [], { number: 7, body: 'The scheduled run X finished **failure**.' })
  expect(noted.action).toBe('note')
  expect(noted.body).toContain('A newer scheduled run is still queued (https://example.test/runs/2')
  expect(noted.body).toContain('The scheduled run X finished **failure**.')

  // A second note replaces the first instead of stacking.
  const again = withPendingNote(noted.body, { ...pending, status: 'in_progress' })
  expect(again.match(/e2e-pending:start/g)).toHaveLength(1)
  expect(again).toContain('still running')

  // The completed run rewrites the body wholesale.
  const done = planTracker(run('failure'), [leg('windows: x', 'failure', 'Update A')], { number: 7, body: again })
  expect(done.action).toBe('update')
  expect(done.body).not.toContain('e2e-pending')
})

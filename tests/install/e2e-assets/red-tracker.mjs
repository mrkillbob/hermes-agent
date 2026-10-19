#!/usr/bin/env node
/**
 * Keep ONE tracking issue in step with the scheduled install/update matrix.
 *
 * Run by .github/workflows/install-e2e-red.yml after every scheduled
 * "Install & Update E2E" run. A red run (any leg failed, was cancelled, or timed
 * out) opens the issue labelled `install-e2e-red`, or rewrites the body of the
 * one already open, so the issue always names the CURRENT red legs and the run
 * that produced them. A green run closes it with a pointer to that run. Never a
 * second issue and never a comment per red run: subscribers hear about the open
 * and the close, and the body is the live state in between.
 *
 * Each red leg carries how many of the previous scheduled runs it was also red
 * in, so a leg that fails every run reads differently from one that flakes. A
 * newer run that has been requested but not finished (the matrix can sit behind
 * runner capacity for hours) is noted on the open issue, so a stale body says so.
 *
 *   node tests/install/e2e-assets/red-tracker.mjs --run-id <id> [--dry-run]
 *
 * Needs GH_TOKEN (issues: write, actions: read) and GITHUB_REPOSITORY.
 */
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

export const LABEL = 'install-e2e-red';
const RED = new Set(['failure', 'cancelled', 'timed_out', 'startup_failure']);
const MAX_ROWS = 60;
const HISTORY_RUNS = 5;
const PENDING_START = '<!-- e2e-pending:start -->';
const PENDING_END = '<!-- e2e-pending:end -->';

/**
 * How often a red leg was red in the previous scheduled runs (newest first,
 * the current run excluded).
 *
 * @param {string} name
 * @param {Set<string>[]} history
 */
function legHistory(name, history) {
  if (history.length === 0) return '';
  const count = history.filter((reds) => reds.has(name)).length;
  if (count === 0) return ` — new: green in the previous ${history.length} scheduled run(s)`;
  if (count === history.length) return ` — red in all ${count} previous scheduled run(s)`;
  return ` — intermittent: also red in ${count} of the previous ${history.length} scheduled run(s)`;
}

/**
 * The note for a newer scheduled run that has not finished, spliced into an
 * open tracker body (replacing any earlier note). A completed run rewrites the
 * whole body, which drops the note.
 *
 * @param {string} body
 * @param {{html_url: string, status: string | null, created_at: string}} run
 */
export function withPendingNote(body, run) {
  const note = `${PENDING_START}\n> A newer scheduled run is still ${run.status === 'queued' ? 'queued' : 'running'} (${run.html_url}, requested ${run.created_at}). The legs below are from the previous run and may be stale; this note disappears when that run reports.\n${PENDING_END}\n\n`;
  const start = body.indexOf(PENDING_START);
  const end = body.indexOf(PENDING_END);
  const rest = start !== -1 && end > start ? body.slice(end + PENDING_END.length).replace(/^\s+/, '') : body;
  return note + rest;
}

/**
 * Decide what the tracker does for one completed run. Pure, so the policy is
 * testable without GitHub.
 *
 * @param {{conclusion: string | null, html_url: string, head_sha: string, created_at: string}} run
 * @param {{name: string, conclusion: string | null, html_url: string, steps?: {name: string, conclusion: string | null}[]}[]} jobs
 * @param {{number: number, body?: string} | null} openIssue
 * @param {{history?: Set<string>[]}} [context] red leg names of the previous scheduled runs, newest first
 * @returns {{action: 'open' | 'update' | 'close' | 'note' | 'none', title?: string, body?: string}}
 */
export function planTracker(run, jobs, openIssue, context = {}) {
  if (run.status && run.status !== 'completed') {
    // Requested or still running: nothing is known yet, but an open tracker's body is about to be stale.
    return openIssue ? { action: 'note', body: withPendingNote(openIssue.body || '', run) } : { action: 'none' };
  }
  const history = context.history || [];
  const red = jobs.filter((j) => RED.has(String(j.conclusion)));
  const runRed = RED.has(String(run.conclusion)) || red.length > 0;
  if (!runRed) {
    if (run.conclusion === 'success' && openIssue) {
      return { action: 'close', body: `The scheduled matrix is green again: ${run.html_url} (\`${run.head_sha.slice(0, 10)}\`). Closing; the next red run reopens a fresh tracker.` };
    }
    return { action: 'none' };
  }
  // Group by (conclusion, step) so 34 legs cancelled in the same step read as
  // one failure class, not 34 unrelated reds.
  /** @type {Map<string, typeof red>} */
  const classes = new Map();
  for (const job of red) {
    const step = (job.steps || []).find((s) => RED.has(String(s.conclusion)))?.name || '(no step reported)';
    // "Install v2026.9.24 (installer-script)" and "Install HEAD (installer-script+desktop)"
    // are the same step of the driver; the leg name already carries ref and method.
    const phase = step.replace(/\bv\d{4}\.\d+\.\d+(?:\.\d+)?\b|\bHEAD\b|\bNEXT\b/g, '<ref>').replace(/\s*\([^)]*\)\s*$/, '');
    const key = `${job.conclusion} in step \`${phase}\``;
    if (!classes.has(key)) classes.set(key, []);
    classes.get(key)?.push(job);
  }
  const lines = [
    `The scheduled **Install & Update E2E** run ${run.html_url} (\`${run.head_sha.slice(0, 10)}\`, ${run.created_at}) finished **${run.conclusion}** with ${red.length} red leg(s).`,
    '',
    'This issue is rewritten in place by `.github/workflows/install-e2e-red.yml` after every scheduled run and closed by the first green one. Root-cause each class below; a historical-release limitation that cannot be fixed goes into `tests/install/e2e-assets/known-failures.json`.',
    '',
    '### Red legs by failure class',
  ];
  if (history.length > 0) {
    const counts = red.map((job) => history.filter((reds) => reds.has(job.name)).length);
    const consistent = counts.filter((c) => c === history.length).length;
    const fresh = counts.filter((c) => c === 0).length;
    lines.push('', `Over the previous ${history.length} scheduled run(s): ${consistent} leg(s) red every time, ${red.length - consistent - fresh} intermittent, ${fresh} new.`);
  }
  let rows = 0;
  for (const [key, members] of [...classes].sort((a, b) => b[1].length - a[1].length)) {
    lines.push('', `**${members.length} × ${key}**`, '');
    for (const job of members) {
      if (rows >= MAX_ROWS) break;
      lines.push(`- [${job.name}](${job.html_url})${legHistory(job.name, history)}`);
      rows += 1;
    }
  }
  if (red.length > rows) lines.push('', `…and ${red.length - rows} more; see the run's jobs list.`);
  const title = `Install & Update E2E matrix is red (${red.length} leg${red.length === 1 ? '' : 's'})`;
  return { action: openIssue ? 'update' : 'open', title, body: lines.join('\n') };
}

/** @param {string[]} args @returns {string} */
function gh(args) {
  return execFileSync('gh', args, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
}

/**
 * Red leg names of the last HISTORY_RUNS completed scheduled runs before `run`.
 *
 * @param {string} repo
 * @param {{id: number, workflow_id: number}} run
 * @returns {Set<string>[]}
 */
function previousRedLegs(repo, run) {
  const previous = JSON.parse(gh(['api', `repos/${repo}/actions/workflows/${run.workflow_id}/runs?event=schedule&status=completed&per_page=${HISTORY_RUNS + 1}`])).workflow_runs
    .filter((/** @type {any} */ r) => r.id !== run.id)
    .slice(0, HISTORY_RUNS);
  return previous.map((/** @type {any} */ r) => new Set(
    gh(['api', '--paginate', `repos/${repo}/actions/runs/${r.id}/jobs?per_page=100`, '--jq', '.jobs[] | select(.conclusion == "failure" or .conclusion == "cancelled" or .conclusion == "timed_out" or .conclusion == "startup_failure") | .name'])
      .split('\n').filter((l) => l.trim())));
}

async function main() {
  const { values } = parseArgs({ options: { 'run-id': { type: 'string' }, 'dry-run': { type: 'boolean', default: false } } });
  const repo = process.env.GITHUB_REPOSITORY;
  const runId = values['run-id'];
  if (!repo || !runId || !/^\d+$/.test(runId)) throw new Error('need GITHUB_REPOSITORY and a numeric --run-id');
  const run = JSON.parse(gh(['api', `repos/${repo}/actions/runs/${runId}`]));
  const jobs = gh(['api', '--paginate', `repos/${repo}/actions/runs/${runId}/jobs?per_page=100`,
    '--jq', '.jobs[] | {name, conclusion, html_url, steps: [.steps[]? | {name, conclusion}]}'])
    .split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l));
  const open = JSON.parse(gh(['api', `repos/${repo}/issues?labels=${LABEL}&state=open&per_page=5`]))
    .filter((/** @type {any} */ i) => !i.pull_request);
  const openIssue = open.length ? { number: open[0].number, body: open[0].body || '' } : null;
  const plan = planTracker(run, jobs, openIssue, { history: run.status === 'completed' ? previousRedLegs(repo, run) : [] });
  console.log(`run ${runId}: conclusion=${run.conclusion} red-legs=${jobs.filter((j) => RED.has(String(j.conclusion))).length} open-tracker=${openIssue ? `#${openIssue.number}` : 'none'} -> ${plan.action}`);
  if (values['dry-run'] || plan.action === 'none') {
    if (plan.body) console.log(`\n--- ${plan.title || 'comment'} ---\n${plan.body}`);
    return;
  }
  if (plan.action === 'open') {
    gh(['label', 'create', LABEL, '--repo', repo, '--force', '--color', 'B60205',
      '--description', 'The scheduled install/update E2E matrix is red (managed by install-e2e-red.yml)']);
    const url = gh(['issue', 'create', '--repo', repo, '--label', LABEL, '--title', String(plan.title), '--body', String(plan.body)]);
    console.log(`opened ${url.trim()}`);
  } else if (plan.action === 'update' && openIssue) {
    gh(['api', '-X', 'PATCH', `repos/${repo}/issues/${openIssue.number}`, '-f', `title=${plan.title}`, '-f', `body=${plan.body}`]);
    console.log(`updated #${openIssue.number} in place`);
  } else if (plan.action === 'note' && openIssue) {
    gh(['api', '-X', 'PATCH', `repos/${repo}/issues/${openIssue.number}`, '-f', `body=${plan.body}`]);
    console.log(`noted the pending run on #${openIssue.number}`);
  } else if (plan.action === 'close' && openIssue) {
    gh(['issue', 'comment', String(openIssue.number), '--repo', repo, '--body', String(plan.body)]);
    gh(['issue', 'close', String(openIssue.number), '--repo', repo]);
    console.log(`closed #${openIssue.number}`);
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  await main();
}

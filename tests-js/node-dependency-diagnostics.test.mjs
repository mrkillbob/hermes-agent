import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, expect, test } from 'vitest'

const marker = 'HERMES_NPM_FAILURE_V1 '
const secret = 'private-token-do-not-publish'
const knownLog = '0 error code ENOSPC\n1 error syscall write\n2 error errno -28\n'
const roots = []
afterEach(() => { for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true }) })

function fixture(steps) {
  const root = mkdtempSync(join(tmpdir(), 'hermes-npm-diagnostic-'))
  roots.push(root)
  const source = join(root, 'source')
  mkdirSync(join(source, 'web'), { recursive: true })
  mkdirSync(join(source, 'node_modules'))
  writeFileSync(join(source, 'node_modules/preserved-before-ci'), 'existing tree')
  writeFileSync(join(source, 'package.json'), JSON.stringify({ private: true, workspaces: ['web'] }))
  writeFileSync(join(source, 'web/package.json'), JSON.stringify({ name: 'fixture-web' }))
  writeFileSync(join(source, 'package-lock.json'), JSON.stringify({ packages: {
    '': {}, web: { name: 'fixture-web' }, 'node_modules/fixture-web': { link: true, resolved: 'web' },
  } }))
  const npm = join(root, 'npm')
  mkdirSync(join(npm, 'bin'), { recursive: true })
  mkdirSync(join(npm, 'node_modules/semver'), { recursive: true })
  writeFileSync(join(npm, 'package.json'), JSON.stringify({ version: '12.0.2', type: 'module' }))
  writeFileSync(join(npm, 'node_modules/semver/index.js'), 'exports.satisfies = () => { throw Error("unexpected engine probe") }')
  const cli = join(npm, 'bin/npm-cli.js')
  writeFileSync(cli, `import {writeFileSync,readFileSync,existsSync} from 'node:fs';
const plan = JSON.parse(process.env.FIXTURE_PLAN);
const calls = process.env.FIXTURE_CALLS;
const count = existsSync(calls) ? Number(readFileSync(calls,'utf8')) : 0;
writeFileSync(calls,String(count+1));
const dir = process.argv.find(arg=>arg.startsWith('--logs-dir=')).slice(11);
writeFileSync(process.env.FIXTURE_LOGDIR,dir);
const step = plan[count];
if (step.log !== null) writeFileSync(dir+'/attempt-'+count+'.log',step.log);
process.exitCode = step.status;
`)
  const locations = ['PATH', 'SystemRoot', 'SYSTEMROOT', 'ComSpec', 'COMSPEC', 'TMPDIR', 'TMP', 'TEMP', 'PATHEXT']
  const env = Object.fromEntries(locations.filter(key => process.env[key] !== undefined).map(key => [key, process.env[key]]))
  Object.assign(env, { HOME: root, USERPROFILE: root, npm_execpath: cli, FIXTURE_PLAN: JSON.stringify(steps),
    FIXTURE_CALLS: join(root, 'calls'), FIXTURE_LOGDIR: join(root, 'logdir') })
  return { root, source, env }
}

// The npm implementation contract belongs to the JS workspace lane. Python
// separately proves source-builder transport and seed schema/artifact behavior.
test.each([
  ['known failure', [{ status: 152, log: knownLog }], [['ENOSPC']]],
  ['missing log', [{ status: 152, log: null }], [['unknown']]],
  ['bounded tail', [{ status: 152, log: 'discarded detail\n'.repeat(5000) + '9999 error code ENOSPC\n' }], [['ENOSPC']]],
  ['untrusted values', [{ status: 152, log: `0 error code ENOSPC ${secret}\n1 error syscall write/${secret}\n2 error errno -28 ${secret}\n3 verbose argv https://user:${secret}@registry.invalid\n4 error code ${secret}\n` }], [['unknown']]],
  ['failed retry', [{ status: 1, log: '0 error code ENOTEMPTY\n' }, { status: 152, log: '0 error code ECONNRESET\n' }], [['ENOTEMPTY'], ['ECONNRESET']]],
  ['recovered retry', [{ status: 1, log: '0 error code ENOTEMPTY\n' }, { status: 0, log: null }], [['ENOTEMPTY']]],
])('npm failure projects safe evidence and preserves retry/cleanup: %s', (_name, steps, expectedCodes) => {
  const { root, source, env } = fixture(steps)
  const script = `import {prepareNodeDependencies} from ${JSON.stringify(new URL('../scripts/build/node-deps.mjs', import.meta.url).href)};
try { prepareNodeDependencies({source:process.argv[1],workspaces:['web']}) }
catch (error) { process.exitCode = error.status ?? 1 }
`
  const child = spawnSync(process.execPath, ['--input-type=module', '-e', script, source],
    { env, encoding: 'utf8', timeout: 30000 })
  expect(child.error).toBeUndefined()
  expect(child.status, child.stderr).toBe(steps.at(-1).status)
  expect(Number(readFileSync(join(root, 'calls'), 'utf8'))).toBe(steps.length)
  expect(existsSync(readFileSync(join(root, 'logdir'), 'utf8'))).toBe(false)
  expect(existsSync(join(source, 'node_modules/preserved-before-ci'))).toBe(steps.length === 1)
  const frames = child.stderr.split(/\r?\n/).filter(line => line.startsWith(marker)).map(line => JSON.parse(line.slice(marker.length)))
  expect(frames.map(frame => frame.codes), child.stderr).toEqual(expectedCodes)
  for (const [index, frame] of frames.entries()) {
    expect(Object.keys(frame).sort()).toEqual(['schema', 'phase', 'attempt', 'status', 'signal', 'node_version', 'npm_version',
      'elapsed_ms', 'log_present', 'log_readable', 'log_truncated', 'codes', 'syscalls', 'errnos'].sort())
    expect(frame.schema).toBe(1)
    expect(frame.phase).toBe('npm_ci')
    expect(frame.attempt).toBe(index + 1)
    expect(frame.status).toBe(steps[index].status)
    expect(frame.signal).toBe(null)
    expect(frame.node_version).toBe(process.versions.node)
    expect(frame.npm_version).toBe('12.0.2')
    expect(Number.isInteger(frame.elapsed_ms) && frame.elapsed_ms >= 0).toBe(true)
    expect(frame.log_present).toBe(steps[index].log !== null)
    expect(frame.log_readable).toBe(frame.log_present)
    expect(frame.log_truncated).toBe((steps[index].log || '').length > 65536)
    if (steps[index].log === knownLog) {
      expect(frame.syscalls).toEqual(['write'])
      expect(frame.errnos).toEqual(['-28'])
    }
    expect(JSON.stringify(frame)).not.toContain(secret)
    expect(JSON.stringify(frame)).not.toContain('registry.invalid')
  }
}, 30000)

import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { desktopInstallNodeOptions, npmCommand, prepareNodeDependencies } from './node-deps.mjs'

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'hermes-node-options-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  const source = join(root, 'source with spaces')
  mkdirSync(join(source, 'apps/desktop'), { recursive: true })
  mkdirSync(join(source, 'scripts/build'), { recursive: true })
  writeFileSync(join(source, 'package.json'), JSON.stringify({ private: true, workspaces: ['apps/desktop'] }))
  writeFileSync(join(source, 'apps/desktop/package.json'), JSON.stringify({ name: 'fixture-desktop', optionalDependencies: { 'get-windows': '9.3.0' } }))
  writeFileSync(join(source, 'package-lock.json'), JSON.stringify({ packages: {
    'node_modules/fixture-desktop': { link: true, resolved: 'apps/desktop' }, 'apps/desktop': { name: 'fixture-desktop' }
  } }))
  writeFileSync(join(source, 'scripts/build/get-windows-compiler.cjs'), '// Not executed by the read-only config probe.')
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    !/^npm_config_/i.test(key) && !/^(HOME|USERPROFILE|NODE_OPTIONS|npm_execpath)$/i.test(key)))
  env.HOME = env.USERPROFILE = root
  env.npm_config_userconfig = join(root, 'user.npmrc')
  env.npm_config_globalconfig = join(root, 'global.npmrc')
  writeFileSync(env.npm_config_userconfig, '')
  writeFileSync(env.npm_config_globalconfig, '')
  return { root, source, env }
}

function fakeNpm(f, body) {
  const dir = join(f.root, 'fake-npm')
  mkdirSync(join(dir, 'bin'), { recursive: true })
  mkdirSync(join(dir, 'node_modules/semver'), { recursive: true })
  writeFileSync(join(dir, 'package.json'), JSON.stringify({ version: '12.0.2', type: 'module' }))
  // The fixture has no engine constraints. Any compatibility call is a mistake.
  writeFileSync(join(dir, 'node_modules/semver/index.js'), 'exports.satisfies = () => { throw Error("unexpected engine check") }')
  f.env.npm_execpath = join(dir, 'bin/npm-cli.js')
  f.env.PROBE_CALLS = join(f.root, 'calls.jsonl')
  writeFileSync(f.env.npm_execpath, String.raw`import { appendFileSync } from 'node:fs';
appendFileSync(process.env.PROBE_CALLS, JSON.stringify(process.argv.slice(2)) + '\n');
${body}`)
}

function expectStopped(f, expectedCode) {
  assert.throws(() => prepareNodeDependencies({ source: f.source, workspaces: ['apps/desktop'], env: f.env, reuse: true }), error => {
    assert.match(error.message, /npm config get node-options failed.*15-second limit.*stopped/)
    if (expectedCode === 'ETIMEDOUT') {
      assert.equal(error.cause.code, expectedCode)
      assert.equal(error.cause.stdout, 'null\n', 'partial output must not become a resolved setting')
    } else assert.equal(error.cause.status, expectedCode)
    return true
  })
  assert.deepEqual(readFileSync(f.env.PROBE_CALLS, 'utf8').trim().split('\n').map(JSON.parse), [['config', 'get', 'node-options']])
  assert.equal(existsSync(join(f.source, 'node_modules/.hermes-node-deps')), false)
  assert.equal(existsSync(join(f.source, 'node_modules/.hermes-node-deps.native-toolchain')), false)
}

test('partial null followed by a hung npm probe fails closed at the existing deadline', { timeout: 60000 }, t => {
  const f = fixture(t)
  fakeNpm(f, String.raw`process.stdout.write('null\n'); setInterval(() => {}, 1000)` )
  const before = performance.now()
  expectStopped(f, 'ETIMEDOUT')
  assert.ok(performance.now() - before >= 12000, 'exercise the real 15-second probe, not an injected error')
})

test('nonzero npm probe preserves its cause and never starts installation', t => {
  const f = fixture(t)
  fakeNpm(f, String.raw`process.stdout.write('null\n'); process.stderr.write('probe failure\n'); process.exitCode = 7`)
  expectStopped(f, 7)
})

test('real npm retains config precedence, inherited options and quoted preload', t => {
  const f = fixture(t)
  const [, npm] = npmCommand({ env: f.env })
  f.env.npm_execpath = npm
  f.env.NODE_OPTIONS = '--stack-trace-limit=11'
  const project = join(f.source, '.npmrc')
  const preload = join(f.source, 'scripts/build/get-windows-compiler.cjs')
  const suffix = ` --require=${JSON.stringify(process.platform === 'win32' ? preload.replaceAll('\\', '/') : preload)}`
  const check = value => assert.equal(desktopInstallNodeOptions(f.source, { env: f.env }), value + suffix)
  writeFileSync(f.env.npm_config_globalconfig, 'node-options=--max-old-space-size=320\n')
  writeFileSync(f.env.npm_config_userconfig, 'node-options=--max-old-space-size=384\n')
  writeFileSync(project, 'node-options=--max-old-space-size=448\n')
  f.env.npm_config_node_options = '--max-old-space-size=512'
  check('--max-old-space-size=512')
  delete f.env.npm_config_node_options
  check('--max-old-space-size=448')
  rmSync(project)
  check('--max-old-space-size=384')
  writeFileSync(f.env.npm_config_userconfig, '')
  check('--max-old-space-size=320')
  writeFileSync(f.env.npm_config_globalconfig, '')
  check('--stack-trace-limit=11')
})

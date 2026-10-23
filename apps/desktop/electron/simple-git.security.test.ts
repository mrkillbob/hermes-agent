import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { simpleGit } from 'simple-git'
import { afterEach, test, vi } from 'vitest'

import { gitFor, reviewCommit, reviewDiff, reviewStage, reviewUnstage } from './git-review-ops'
import type * as NoConsoleGit from './no-console-git'

const tempDirs: string[] = []

afterEach(() => {
  vi.unstubAllEnvs()

  for (const dir of tempDirs.splice(0)) {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

function tempDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-git-security-'))

  tempDirs.push(dir)

  return dir
}

for (const suffix of ['cmd', 'command']) {
  test(`trusted binary permission does not allow trailer ${suffix} configuration`, async () => {
    const dir = tempDir()
    const key = `trailer.test.${suffix}`
    const value = `${key}=echo harmless`

    const cases = [
      () => gitFor(dir, 'git').raw(['-c', value, '--version']),
      () => gitFor(dir, 'git').raw([`-c${value}`, '--version']),
      () => simpleGit({ baseDir: dir, config: [value] }).raw(['--version']),
      () => gitFor(dir, 'git').addConfig(key, 'echo harmless')
    ]

    for (const run of cases) {
      await assert.rejects(run, /unsafe|not permitted|not allowed|blocked/i)
    }
  })
}

test('an explicitly supplied VISUAL editor is rejected', async () => {
  await assert.rejects(
    () => gitFor(tempDir(), 'git').env('VISUAL', 'echo harmless').raw(['--version']),
    /unsafe|not permitted|not allowed|blocked/i
  )
})

test.each([undefined, '1'])(
  'the host tuple preserves trusted authentication, OS environment and argv (prompt %s)',
  async prompt => {
    const dir = tempDir()
    const hostScript = path.join(dir, 'host (João).cjs')
    const gitBin = String.raw`C:\Program Files\Git\cmd\git.exe`
    const args = ['--version', 'file with spaces', '--', '-option-looking.txt']

    // These values originate in Electron main's process.env, never IPC or repository data.
    const authentication = {
      GIT_SSH_COMMAND: 'echo harmless',
      GIT_SSH: 'trusted-ssh-bin',
      GIT_ASKPASS: 'trusted-git-askpass',
      SSH_ASKPASS: 'trusted-ssh-askpass'
    }
    const guarded = {
      VISUAL: 'echo harmless',
      EDITOR: 'echo harmless',
      PAGER: 'echo harmless',
      PREFIX: '/foreign',
      GIT_PAGER: 'echo harmless',
      GIT_EDITOR: 'echo harmless',
      GIT_DIR: '/foreign/.git',
      GIT_WORK_TREE: '/foreign',
      GIT_INDEX_FILE: '/foreign/index',
      GIT_OBJECT_DIRECTORY: '/foreign/objects',
      GIT_CONFIG: '/foreign/config',
      GIT_CONFIG_GLOBAL: '/foreign/global',
      GIT_CONFIG_SYSTEM: '/foreign/system',
      GIT_CONFIG_PARAMETERS: "'trailer.test.command=echo harmless'",
      GIT_CONFIG_COUNT: '1',
      GIT_CONFIG_KEY_0: 'trailer.test.command',
      GIT_CONFIG_VALUE_0: 'echo harmless',
      git_config_count: '1',
      git_config_key_0: 'trailer.test.command',
      git_config_value_0: 'echo harmless',
      Git_Config_Count: '1',
      Git_Config_Key_0: 'trailer.test.command',
      Git_Config_Value_0: 'echo harmless',
      GIT_SSH_COMMAND_EXTRA: 'echo harmless'
    }
    const envKeys = [
      'HERMES_GIT_ARGV0',
      'GIT_TERMINAL_PROMPT',
      'SystemRoot',
      'HERMES_HOST_CONTROL',
      'PATH',
      'Path',
      ...Object.keys(authentication),
      ...Object.keys(guarded)
    ]

    fs.writeFileSync(
      hostScript,
      `process.stdout.write(JSON.stringify({ args: process.argv.slice(2), env: Object.fromEntries(${JSON.stringify(envKeys)}.map(key => [key, process.env[key]])) }))`
    )
    for (const [key, value] of Object.entries({ ...authentication, ...guarded })) {
      vi.stubEnv(key, value)
    }
    vi.stubEnv('GIT_TERMINAL_PROMPT', prompt)
    vi.stubEnv('SystemRoot', 'C:\\Windows')
    vi.stubEnv('HERMES_HOST_CONTROL', 'retained')
    vi.resetModules()
    vi.doMock('./no-console-git', async importOriginal => {
      const actual = await importOriginal<typeof NoConsoleGit>()

      return {
        ...actual,
        windowsGitHost: () => ({ isWindows: true, pythonBin: process.execPath, scriptPath: hostScript })
      }
    })

    try {
      const { gitFor: hostedGitFor } = await import('./git-review-ops')
      const output = JSON.parse(await hostedGitFor(dir, gitBin).raw(args))

      assert.deepEqual(output.args, args)
      assert.equal(output.env.HERMES_GIT_ARGV0, JSON.stringify(gitBin))
      assert.equal(output.env.GIT_TERMINAL_PROMPT, prompt ?? '0')
      assert.equal(output.env.SystemRoot, 'C:\\Windows')
      assert.equal(output.env.HERMES_HOST_CONTROL, 'retained')
      assert.equal(output.env.PATH ?? output.env.Path, process.env.PATH ?? process.env.Path)

      for (const [key, value] of Object.entries(authentication)) {
        assert.equal(output.env[key], value, `${key} must preserve the trusted process value`)
      }
      for (const key of Object.keys(guarded)) {
        assert.equal(output.env[key], undefined, `${key} must remain guarded`)
      }
    } finally {
      vi.doUnmock('./no-console-git')
      vi.resetModules()
    }
  }
)

test('review stage, unstage, diff and commit retain literal option-looking filenames', async () => {
  const dir = tempDir()
  const filename = '-trailer.test.command=file with spaces.txt'
  const file = path.join(dir, filename)

  execFileSync('git', ['init', '-q'], { cwd: dir })
  execFileSync('git', ['config', 'user.email', 'hermes-test@example.com'], { cwd: dir })
  execFileSync('git', ['config', 'user.name', 'Hermes Test'], { cwd: dir })
  fs.writeFileSync(path.join(dir, 'initial.txt'), 'initial\n')
  execFileSync('git', ['add', '--', 'initial.txt'], { cwd: dir })
  execFileSync('git', ['commit', '-qm', 'initial'], { cwd: dir })
  fs.writeFileSync(file, 'first\n')
  await reviewStage(dir, filename, 'git')
  assert.deepEqual((await gitFor(dir, 'git').status()).staged, [filename])
  await reviewUnstage(dir, filename, 'git')
  assert.deepEqual((await gitFor(dir, 'git').status()).staged, [])
  await reviewStage(dir, filename, 'git')
  const message = 'literal commit message\n\n--trailer=test=value'

  await reviewCommit(dir, message, false, 'git')
  assert.equal((await gitFor(dir, 'git').raw(['log', '-1', '--format=%B'])).trim(), message)
  fs.writeFileSync(file, 'changed\n')
  assert.match(await reviewDiff(dir, filename, 'worktree', null, false, 'git'), /\+changed/)
})

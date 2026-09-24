import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { expect, test } from 'vitest'

import { hasGitEntry } from './git-checkout'

test('update discovery accepts both a clone and its linked worktree', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-update-git-'))
  const clone = path.join(root, 'clone')
  const linked = path.join(root, 'linked')
  const git = (args: string[]) => execFileSync('git', args, { stdio: 'pipe' })

  try {
    git(['init', clone])
    git(['-C', clone, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
      '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-m', 'Initial'])
    git(['-C', clone, 'worktree', 'add', '--detach', linked])
    expect(hasGitEntry(clone)).toBe(true)
    expect(hasGitEntry(linked)).toBe(true)
    expect(hasGitEntry(root)).toBe(false)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

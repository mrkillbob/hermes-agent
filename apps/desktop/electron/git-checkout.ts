import fs from 'node:fs'
import path from 'node:path'

// Linked worktrees store a gitdir pointer in a file; ordinary clones use a directory.
// Git commands perform repository validation after this discovery check.
export function hasGitEntry(root: string): boolean {
  try {
    const entry = fs.statSync(path.join(root, '.git'))

    return entry.isDirectory() || entry.isFile()
  } catch {
    return false
  }
}

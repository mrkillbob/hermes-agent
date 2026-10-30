import assert from 'node:assert/strict'
import type * as nodeChildProcess from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { test, vi } from 'vitest'

const inventory = vi.hoisted(() => ({
  children: [] as nodeChildProcess.ChildProcess[],
  stopping: false,
  overlap: false
}))

vi.mock('node:child_process', async importOriginal => {
  const actual = await importOriginal<typeof nodeChildProcess>()

  const spawn: typeof nodeChildProcess.spawn = ((...args: Parameters<typeof nodeChildProcess.spawn>) => {
    if (inventory.stopping) {
      throw new Error('fixture cleanup: no new probes')
    }

    inventory.overlap ||= inventory.children.some(child => child.exitCode === null && child.signalCode === null)
    const child = actual.spawn(...args)
    inventory.children.push(child)

    return child
  }) as typeof nodeChildProcess.spawn

  return { ...actual, spawn }
})

import { execProbe } from './backend-probes'

test.skipIf(process.platform === 'win32')(
  'a probe that ignores SIGTERM is reaped before the bounded cold-start retry',
  async () => {
    const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-probe-lifetime-'))
    const events = path.join(temp, 'events.jsonl')

    const script = `
    const fs = require('node:fs')
    const record = type => fs.appendFileSync(${JSON.stringify(events)}, JSON.stringify({ type, pid: process.pid }) + '\\n')
    process.on('SIGTERM', () => record('term'))
    record('ready')
    setInterval(() => {}, 1000)
  `

    let timer: ReturnType<typeof setTimeout> | undefined

    const probe = execProbe(process.execPath, ['-e', script], { stdio: 'ignore', timeout: 2_000 }).then(
      () => ({ kind: 'success' as const }),
      error => ({ kind: 'rejected' as const, error })
    )

    try {
      const result = await Promise.race([
        probe,
        new Promise<{ kind: 'deadline' }>(resolve => {
          timer = setTimeout(() => resolve({ kind: 'deadline' }), 8_000)
        })
      ])

      assert.equal(
        result.kind,
        'rejected',
        'the timeout must settle even when the probe handles SIGTERM without exiting'
      )
      assert.equal(inventory.children.length, 2, 'only one cold-start retry is permitted')
      assert.equal(inventory.overlap, false, 'retry cannot overlap an earlier owned probe')

      for (const child of inventory.children) {
        assert.notEqual(child.signalCode, null, 'each exact owned probe must exit before return')
      }

      const recorded = fs
        .readFileSync(events, 'utf8')
        .trim()
        .split('\n')
        .map(line => JSON.parse(line))

      assert.equal(recorded.filter(event => event.type === 'ready').length, 2)
      assert.equal(
        recorded.filter(event => event.type === 'term').length,
        2,
        'ordinary termination precedes any escalation'
      )
    } finally {
      clearTimeout(timer)
      inventory.stopping = true

      for (const child of inventory.children) {
        if (child.exitCode === null && child.signalCode === null) {
          child.kill('SIGKILL')
        }
      }

      await probe
      fs.rmSync(temp, { recursive: true, force: true })
    }
  },
  12_000
)

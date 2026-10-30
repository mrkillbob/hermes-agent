import http from 'node:http'
import type { AddressInfo } from 'node:net'

import { expect, test } from 'vitest'

import { readMockPrompts } from './desktop-chat-smoke.ts'

async function serve(handler: http.RequestListener): Promise<{ url: string; close: () => Promise<void> }> {
  const server = http.createServer(handler)
  await new Promise<void>((resolve): void => { server.listen(0, '127.0.0.1', resolve) })

  return {
    url: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
    close: (): Promise<void> => new Promise<void>((resolve): void => { server.closeAllConnections(); server.close((): void => resolve()) }),
  }
}

// Regression for the Windows install-e2e leg that failed on one stalled witness response.
test('a stalled witness response is retried instead of failing the checkpoint', async (): Promise<void> => {
  let requests = 0

  const mock = await serve((_req, res): void => {
    requests++

    if (requests === 1) {
      return
    }

    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ receivedPrompts: ['hello'] }))
  })

  try {
    expect(await readMockPrompts(mock.url, { timeoutMs: 200 })).toEqual(['hello'])
    expect(requests).toBe(2)
  } finally {
    await mock.close()
  }
})

test('a witness that never answers still fails after the attempts are spent', async (): Promise<void> => {
  let requests = 0
  const mock = await serve((): void => { requests++ })

  try {
    await expect(readMockPrompts(mock.url, { timeoutMs: 100, attempts: 2 })).rejects.toMatchObject({ name: 'TimeoutError' })
    expect(requests).toBe(2)
  } finally {
    await mock.close()
  }
})

test('a non-OK witness response is not retried', async (): Promise<void> => {
  let requests = 0

  const mock = await serve((_req, res): void => {
    requests++
    res.writeHead(500)
    res.end()
  })

  try {
    await expect(readMockPrompts(mock.url, { timeoutMs: 200 })).rejects.toThrow('HTTP 500')
    expect(requests).toBe(1)
  } finally {
    await mock.close()
  }
})

import { Buffer } from 'node:buffer'
import { EventEmitter } from 'node:events'

import type { BrowserWindow, BrowserWindowConstructorOptions, ClientRequest, Session } from 'electron'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { resolveDesktopRemoteRoute } from './desktop-remote-route'
import { LEGACY_OAUTH_PARTITION, resolveOauthPartition } from './oauth-partition'
import { createOauthSessionHandlers } from './oauth-session-handlers'
import { RemoteSessionCookieStore } from './remote-session-cookies'

const HOST = 'https://host.example.test'
const PRIVATE_JAR = 'persist:hermes-remote-oauth-conn-c'

const registry = {
  primary: 'a',
  connections: [
    { id: 'a', kind: 'remote', authMode: 'oauth', url: `${HOST}/a` },
    { id: 'b', kind: 'cloud', url: `${HOST}/b` },
    { id: 'c', kind: 'remote', authMode: 'oauth', url: `${HOST}/c` }
  ]
}

function harness(config: { profiles?: Record<string, unknown> } | null = null) {
  const cookies = new Map<Session, Set<string>>()
  const sessions = new Map<string, Session>()
  const windows: Array<{ options: BrowserWindowConstructorOptions; url: string; destroyed: boolean }> = []
  const requests: Array<{ url: string; session: Session; headers: Record<string, string> }> = []
  const mirror = new RemoteSessionCookieStore()
  let rejected = false

  const fromPartition = (partition: string): Session => {
    if (!sessions.has(partition)) {
      const sess = { partition } as unknown as Session

      sessions.set(partition, sess)
      cookies.set(sess, new Set())
    }

    return sessions.get(partition)!
  }

  const partitionFor = (url: string, identity = {}) => resolveOauthPartition(url, { registry, ...identity })

  class Window extends EventEmitter {
    webContents = new EventEmitter()
    state: (typeof windows)[number]

    constructor(options: BrowserWindowConstructorOptions) {
      super()
      this.state = { options, url: '', destroyed: false }
      windows.push(this.state)
    }

    destroy() {
      this.state.destroyed = true
    }
    focus() {}
    isDestroyed() {
      return this.state.destroyed
    }
    isVisible() {
      return Boolean(this.state.options.show)
    }
    show() {
      this.state.options.show = true
    }
    async loadURL(url: string) {
      this.state.url = url
      const sess = this.state.options.webPreferences!.session!
      const base = url.replace(/\/(?:login)?$/, '')

      cookies.get(sess)!.add(base)
      queueMicrotask(() => this.webContents.emit('did-navigate', {}, url))
    }
  }

  const handlers = createOauthSessionHandlers({
    app: { isReady: () => true },
    session: { fromPartition },
    getOauthSession: () => fromPartition(LEGACY_OAUTH_PARTITION),
    installRemoteHeaderRulesOnSession: () => {},
    resolveOauthPartitionForUrl: partitionFor,
    readDesktopConnectionsRegistry: () => registry,
    readDesktopConnectionConfig: () => config,
    BrowserWindow: Window as unknown as new (options: BrowserWindowConstructorOptions) => BrowserWindow,
    hasOauthSessionCookie: async (url: string, identity = {}) =>
      cookies.get(fromPartition(partitionFor(url, identity)))!.has(url.replace(/\/+$/, '')),
    headersForRemoteRequest: () => ({}),
    rememberLog: () => {},
    onWindowCreated: () => {},
    captureRemoteSessionCookiesIntoMirror: (sess: Session, partition: string, url: string) => {
      if (cookies.get(sess)!.has(url)) {
        const path = new URL(url).pathname

        mirror.recordFromJar(partition, url, [
          { name: '__Secure-hermes_session_at', value: `fresh-${path.slice(1)}`, path }
        ])
      }
    },
    remoteSessionCookies: mirror,
    electronNet: {
      request: (options: { url: string; session: Session }) => {
        const record = { ...options, headers: {} as Record<string, string> }

        const request = Object.assign(new EventEmitter(), {
          abort: () => {},
          setHeader: (name: string, value: string) => {
            record.headers[name] = value
          },
          write: () => {},
          end: () =>
            queueMicrotask(() => {
              const statusCode = rejected ? 200 : 401
              const body = rejected ? { ok: true } : { error: 'unauthenticated', reason: 'no_cookie' }
              const response = Object.assign(new EventEmitter(), { statusCode, headers: {} })

              rejected = true
              request.emit('response', response)
              response.emit('data', Buffer.from(JSON.stringify(body)))
              response.emit('end')
            })
        })

        requests.push(record)

        return request as unknown as ClientRequest
      }
    }
  })

  return { ...handlers, windows, requests, fromPartition }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

test('explicit login loads the registered prefix chooser in its request cookie jar', async () => {
  const h = harness()

  await expect(h.openOauthLoginWindow(`${HOST}/c`)).resolves.toEqual({ baseUrl: `${HOST}/c`, ok: true })
  expect(h.windows).toHaveLength(1)
  expect(h.windows[0].url).toBe(`${HOST}/c/login`)
  expect(h.windows[0].options.webPreferences?.session).toBe(h.fromPartition(PRIVATE_JAR))
  expect(h.windows[0].options.show).toBe(true)
  expect(h.windows[0].destroyed).toBe(true)
})

test.each(['remote', 'cloud'])('401 recovery preserves a legacy per-profile %s override prefix', async mode => {
  const base = `${HOST}/legacy-b`

  const config = {
    profiles: {
      work: { mode, authMode: 'oauth', url: base },
      local: { mode: 'local', url: `${base}/deeper` },
      ssh: { mode: 'ssh', host: 'ssh.example.test', url: `${base}/deeper/api` }
    }
  }

  const route = resolveDesktopRemoteRoute({
    config,
    profile: 'work',
    registry: { version: 2, primary: '', launchMode: 'primary', lastUsed: '', connections: [] }
  })

  expect(route).toMatchObject({ kind: mode, source: 'profile', url: base, authMode: 'oauth' })

  if (!route || route.kind === 'ssh') {
    throw new Error('Expected the real remote route')
  }

  const h = harness(config)

  await expect(h.fetchJsonViaOauthSession(`${route.url}/deeper/api/status`)).resolves.toEqual({ ok: true })
  expect(h.windows).toHaveLength(1)
  expect(h.windows[0].url).toBe(`${base}/`)
  expect(h.windows[0].options.webPreferences?.session).toBe(h.fromPartition(LEGACY_OAUTH_PARTITION))
  expect(h.requests.every(request => request.session === h.fromPartition(LEGACY_OAUTH_PARTITION))).toBe(true)
  expect(h.requests[1].headers.Cookie).toBe('__Secure-hermes_session_at=fresh-legacy-b')
})

test('401 re-login is hidden, keeps the prefix and jar, then retries with the fresh cookie', async () => {
  const h = harness()

  await expect(h.fetchJsonViaOauthSession(`${HOST}/c/api/status`)).resolves.toEqual({ ok: true })
  expect(h.windows).toHaveLength(1)
  expect(h.windows[0].url).toBe(`${HOST}/c/`)
  expect(h.windows[0].options.show).toBe(false)
  expect(h.windows[0].options.webPreferences?.session).toBe(h.fromPartition(PRIVATE_JAR))
  expect(h.requests.map(request => request.url)).toEqual([`${HOST}/c/api/status`, `${HOST}/c/api/status`])
  expect(h.requests.every(request => request.session === h.fromPartition(PRIVATE_JAR))).toBe(true)
  expect(h.requests[0].headers.Cookie).toBeUndefined()
  expect(h.requests[1].headers.Cookie).toBe('__Secure-hermes_session_at=fresh-c')
})

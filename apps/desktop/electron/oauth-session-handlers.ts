import type {
  App,
  BrowserWindowConstructorOptions,
  BrowserWindow as ElectronBrowserWindow,
  Net,
  Session
} from 'electron'

import { normalizeRemoteBaseUrl } from './connection-config'
import { DEFAULT_FETCH_TIMEOUT_MS, resolveTimeoutMs } from './hardening'
import { loginFlightKey, OauthLoginFlights } from './oauth-login-flights'
import { startOauthLoginWindow } from './oauth-login-window'
import { isExpectedOauthNavigationAbort } from './oauth-navigation'
import { serializeJsonBody, setJsonRequestHeaders } from './oauth-net-request'
import { LEGACY_OAUTH_PARTITION, type PartitionRegistrySnapshot, resolveGatewayBase } from './oauth-partition'
import { canShowInteractiveOauthLogin, retryCookie401WithLogin } from './oauth-rest-request'
import { wireOauthSessionResponse } from './oauth-session-response'
import type { RemoteSessionCookieStore } from './remote-session-cookies'
import { oauthLoginLoadUrlOptions } from './remote-ws-headers'

interface OauthIdentity {
  connectionId?: string
  pendingAuthMode?: string
  pendingKind?: string
}

interface OauthSessionHandlerDeps {
  app: Pick<App, 'isReady'>
  session: { fromPartition: (partition: string) => Session }
  getOauthSession: () => Session | null
  installRemoteHeaderRulesOnSession: (session: Session) => void
  resolveOauthPartitionForUrl: (url: string, identity?: OauthIdentity) => string
  readDesktopConnectionsRegistry: () => PartitionRegistrySnapshot | null
  readDesktopConnectionConfig: () => { remote?: { url?: unknown } } | null
  BrowserWindow: new (options: BrowserWindowConstructorOptions) => ElectronBrowserWindow
  hasOauthSessionCookie: (url: string, identity?: OauthIdentity) => Promise<boolean>
  headersForRemoteRequest: (url: string) => Record<string, string>
  rememberLog: (message: string) => void
  onWindowCreated: (window: ElectronBrowserWindow) => void
  captureRemoteSessionCookiesIntoMirror: (session: Session, partition: string, baseUrl: string) => unknown
  remoteSessionCookies: RemoteSessionCookieStore
  electronNet: Pick<Net, 'request'>
}

/** Keep login and 401 recovery in one importable scope; Electron I/O stays at the boundary. */
export function createOauthSessionHandlers(deps: OauthSessionHandlerDeps) {
  const {
    app,
    session,
    getOauthSession,
    installRemoteHeaderRulesOnSession,
    resolveOauthPartitionForUrl,
    readDesktopConnectionsRegistry,
    readDesktopConnectionConfig,
    BrowserWindow,
    hasOauthSessionCookie,
    headersForRemoteRequest,
    rememberLog,
    onWindowCreated,
    captureRemoteSessionCookiesIntoMirror,
    remoteSessionCookies,
    electronNet
  } = deps

  const OAUTH_SESSION_PARTITION = LEGACY_OAUTH_PARTITION
  const oauthSessionsByPartition = new Map<string, Session>()
  const oauthLoginFlights = new OauthLoginFlights()

  // The gateway (origin + proxy prefix) a URL belongs to, for the login-flight key.
  function resolveGatewayBaseForUrl(url) {
    try {
      return resolveGatewayBase(url, {
        registry: readDesktopConnectionsRegistry(),
        v1RemoteUrl: readDesktopConnectionConfig()?.remote?.url
      })
    } catch {
      return resolveGatewayBase(url)
    }
  }

  function getOauthSessionForUrl(url, { connectionId = '', pendingAuthMode = '', pendingKind = '' } = {}) {
    const partition = resolveOauthPartitionForUrl(url, { connectionId, pendingAuthMode, pendingKind })

    if (partition === OAUTH_SESSION_PARTITION) {
      return getOauthSession()
    }

    if (!app.isReady()) {
      return null
    }

    let sess = oauthSessionsByPartition.get(partition)

    if (!sess) {
      sess = session.fromPartition(partition)
      oauthSessionsByPartition.set(partition, sess)
      installRemoteHeaderRulesOnSession(sess)
    }

    return sess
  }

  function openOauthLoginWindow(
    baseUrl,
    { silent = false, background = false, connectionId = '', pendingAuthMode = '', pendingKind = '' } = {}
  ) {
    const identity = { connectionId, pendingAuthMode, pendingKind }
    const unattended = background || !canShowInteractiveOauthLogin()
    const partition = resolveOauthPartitionForUrl(baseUrl, identity)

    return oauthLoginFlights.run(
      loginFlightKey(partition, resolveGatewayBaseForUrl(baseUrl)),
      unattended ? 'background' : 'foreground',
      () => {
        const sess = app.isReady() ? getOauthSessionForUrl(baseUrl, identity) : null

        if (!sess) {
          throw new Error(
            app.isReady() ? 'OAuth session partition is unavailable.' : 'Desktop is not ready to start an OAuth login.'
          )
        }

        return startOauthLoginWindow(
          { baseUrl: normalizeRemoteBaseUrl(baseUrl), session: sess, silent, background: unattended },
          {
            createWindow: ({ session: windowSession, show, title }) =>
              new BrowserWindow({
                width: 520,
                height: 720,
                title,
                autoHideMenuBar: true,
                show,
                webPreferences: {
                  contextIsolation: true,
                  nodeIntegration: false,
                  sandbox: true,
                  session: windowSession,
                  webSecurity: true
                }
              }),
            hasSessionCookie: () => hasOauthSessionCookie(baseUrl, identity),
            isExpectedNavigationAbort: isExpectedOauthNavigationAbort,
            loadOptionsFor: loginUrl => {
              const loginHeaders = headersForRemoteRequest(loginUrl)
              rememberLog(
                `OAuth login: attaching ${Object.keys(loginHeaders).length} extra gateway header(s) to ${new URL(loginUrl).host}`
              )

              return oauthLoginLoadUrlOptions(loginHeaders)
            },
            log: rememberLog,
            // #61457: mirror the fresh cookies before the jar can drop them again.
            onLanded: () => void captureRemoteSessionCookiesIntoMirror(sess, partition, baseUrl),
            // A crashed sign-in renderer never settles the promise; log it (#81290).
            onWindowCreated: win => onWindowCreated(win as ElectronBrowserWindow)
          }
        )
      }
    )
  }

  // JSON request routed through the OAuth session partition so the HttpOnly
  // session cookie is attached automatically by Electron's net stack. Used for
  // authed REST against a gated gateway, including minting WS tickets.
  //
  // #61457: the partition's Chromium jar is not a reliable cookie source — it can
  // drop the `hermes_session*` cookies (Windows %3A profile folders, lazy
  // hydration, flush races) and `useSessionCookies: true` then intermittently
  // omits the cookie → 401 `no_cookie` right after a successful sign-in. Two
  // mitigations, both keyed by the request's resolved OAuth partition + origin
  // (the same owner the jar itself is scoped to, so same-origin gateways on
  // separate jars stay separate here too):
  //   1. an explicit `Cookie` header built from the in-memory session-cookie
  //      mirror (remote-session-cookies.ts) — an explicit header bypasses the
  //      network stack's jar lookup entirely, and every `Set-Cookie` observed
  //      here (and in the login window) feeds the mirror back; and
  //   2. one forced silent re-login + single retry when the gateway still
  //      answers 401 — ONLY when shouldReplayAfterCookie401 holds: the 401 is
  //      the auth gate's pre-handler refusal AND the operation is idempotent or
  //      vouched replay-safe (`options.replayOn401`). Anything else keeps the
  //      no-replay rule of requestWithOauthFallback.
  function fetchJsonViaOauthSession(url, options: any = {}) {
    const partition = resolveOauthPartitionForUrl(url)

    const attempt = (): Promise<unknown> =>
      new Promise((resolve, reject) => {
        const sess = getOauthSessionForUrl(url)

        if (!sess) {
          reject(new Error('OAuth session partition is unavailable.'))

          return
        }

        let parsed

        try {
          parsed = new URL(url)
        } catch (error) {
          reject(new Error(`Invalid URL: ${error.message}`))

          return
        }

        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
          reject(new Error(`Unsupported Hermes backend URL protocol: ${parsed.protocol}`))

          return
        }

        const body = serializeJsonBody(options.body)
        const timeoutMs = resolveTimeoutMs(options.timeoutMs, DEFAULT_FETCH_TIMEOUT_MS)

        const request = electronNet.request({
          method: options.method || 'GET',
          url,
          session: sess,
          useSessionCookies: true,
          redirect: 'follow'
        } as any)

        setJsonRequestHeaders(request)

        const headerBag = { ...headersForRemoteRequest(url), ...(options.headers || {}) }

        // Explicit mirror cookies ride last so an existing caller-supplied
        // Cookie header still wins, and the mirror never clobbers intent.
        const mirroredCookies = remoteSessionCookies.cookieHeaderFor(partition, url)

        if (mirroredCookies && headerBag.Cookie === undefined && headerBag.cookie === undefined) {
          headerBag.Cookie = mirroredCookies
        }

        for (const [name, value] of Object.entries(headerBag)) {
          request.setHeader(name, String(value))
        }

        let timedOut = false

        const timer = setTimeout(() => {
          timedOut = true

          try {
            request.abort()
          } catch {
            // already finished
          }

          reject(new Error(`Timed out connecting to Hermes backend after ${timeoutMs}ms`))
        }, timeoutMs)

        request.on('response', (res: Electron.IncomingMessage): void => {
          wireOauthSessionResponse(res, {
            url,
            isTimedOut: (): boolean => timedOut,
            clearTimer: (): void => clearTimeout(timer),
            resolve,
            reject,
            onSetCookies: setCookie => remoteSessionCookies.record(partition, url, setCookie)
          })
        })
        request.on('error', error => {
          if (timedOut) {
            return
          }

          clearTimeout(timer)
          reject(error)
        })

        if (body) {
          request.write(body)
        }

        request.end()
      })

    return attempt().catch(async error => {
      const gatewayBase = resolveGatewayBaseForUrl(url)

      return retryCookie401WithLogin(error, options, {
        // One burst shares one clear: a joiner must not wipe the mirror the
        // running window is about to fill.
        clearCookies: () => {
          if (!oauthLoginFlights.isRunning(loginFlightKey(partition, gatewayBase))) {
            remoteSessionCookies.clear(partition, url)
          }
        },
        login: async () => {
          try {
            // A refused request is never a user gesture: try the hidden
            // auto-SSO, and on failure leave the 401 to the app's own "Sign in"
            // surface instead of opening a window nobody asked for.
            await openOauthLoginWindow(gatewayBase, { silent: true, background: true })
          } catch (reloginError) {
            rememberLog(
              `Remote session re-login after 401 failed for ${new URL(url).host}: ${reloginError?.message || reloginError}`
            )
            throw reloginError
          }
        },
        retry: attempt
      })
    })
  }

  return { getOauthSessionForUrl, openOauthLoginWindow, fetchJsonViaOauthSession }
}

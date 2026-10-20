/**
 * remote-session-cookies.ts
 *
 * In-memory session-cookie mirror for remote gateways (#61457).
 *
 * The `persist:hermes-remote-oauth` partition family is supposed to keep the
 * dashboard `hermes_session*` cookies on disk, but in the field the Chromium
 * jar can drop them (Windows %3A profile folders, lazy hydration, jar flush
 * races), and `electronNet` with `useSessionCookies: true` then intermittently
 * omits the cookie entirely → every authed REST call and WS-ticket mint 401s
 * as `no_cookie` right after a successful sign-in.
 *
 * This module is the belt-and-braces fix: capture every `Set-Cookie` the
 * gateway sends (login window navigations AND authed REST responses) into a
 * process-lifetime map, and let `fetchJsonViaOauthSession` attach them as an
 * explicit `Cookie` header — an explicit header is never subject to the
 * network stack's jar-lookup flakiness.
 *
 * Scoping mirrors the jar it shadows, never something weaker:
 *   - the map is keyed by the resolved OAuth PARTITION (the same owner
 *     `resolveOauthPartition` picks for the request — per-connection for
 *     non-primary registered remotes, #92183) and then by origin, so two
 *     same-origin gateways that ride separate jars never see each other's
 *     credentials through this mirror either;
 *   - each cookie is identified by name + effective `Path`, so disjoint proxy
 *     prefixes can share a jar without overwriting each other; presentation
 *     still requires a matching path (RFC 6265 §5.1.4);
 *   - a `Set-Cookie` with `Max-Age<=0`, a past `Expires`, or an empty value
 *     DELETES the mirror entry, so a server-side sign-out is honoured.
 *
 * Secrets stay in process memory only (never persisted to disk), and
 * `clearGateway` drops only the resolved gateway's canonical cookie Path on
 * re-login; explicit `clear(partition, origin)` remains origin-wide.
 */

export interface ParsedCookie {
  name: string
  value: string
  /** Effective cookie path (`Path=` attribute, else `/`). */
  path: string
  /** True when the header asks the client to delete the cookie. */
  expired: boolean
}

interface MirroredCookie {
  name: string
  value: string
  path: string
}

function cookieKey(name: string, path: string): string {
  return JSON.stringify([name, path])
}

function normalizeCookiePath(raw: string | undefined): string {
  const path = String(raw ?? '').trim()

  // RFC 6265 §5.1.4: an empty or non-absolute Path attribute falls back to
  // the default path; the gateway always sets Path=/ so "/" is the fallback.
  return path.startsWith('/') ? path : '/'
}

/** First `Name=Value` pair plus effective path/deletion of a Set-Cookie header; null when unparsable. */
export function parseSetCookie(header: string): ParsedCookie | null {
  if (typeof header !== 'string' || !header.trim()) {
    return null
  }

  const [firstPair = '', ...attributes] = header.split(';')
  const eq = firstPair.indexOf('=')

  if (eq <= 0) {
    return null
  }

  const name = firstPair.slice(0, eq).trim()
  const value = firstPair.slice(eq + 1).trim()

  if (!name) {
    return null
  }

  let path: string | undefined
  // An empty value is how some servers clear a cookie; treat it as deletion.
  let expired = !value

  for (const attribute of attributes) {
    const attrEq = attribute.indexOf('=')
    const attrName = (attrEq >= 0 ? attribute.slice(0, attrEq) : attribute).trim().toLowerCase()
    const attrValue = attrEq >= 0 ? attribute.slice(attrEq + 1).trim() : ''

    if (attrName === 'path') {
      path = attrValue
    } else if (attrName === 'max-age') {
      const seconds = Number(attrValue)

      if (Number.isFinite(seconds) && seconds <= 0) {
        expired = true
      }
    } else if (attrName === 'expires') {
      const at = Date.parse(attrValue)

      if (Number.isFinite(at) && at <= Date.now()) {
        expired = true
      }
    }
  }

  return { name, value, path: normalizeCookiePath(path), expired }
}

/** `protocol//host` origin key for a request URL; null when not http(s). */
export function originKeyFor(url: string): string | null {
  try {
    const parsed = new URL(url)

    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return null
    }

    return `${parsed.protocol}//${parsed.host}`
  } catch {
    return null
  }
}

function requestPathFor(url: string): string {
  try {
    return new URL(url).pathname || '/'
  } catch {
    return '/'
  }
}

/** RFC 6265 §5.1.4 path-match. */
export function cookiePathMatches(cookiePath: string, requestPath: string): boolean {
  if (cookiePath === requestPath) {
    return true
  }

  if (!requestPath.startsWith(cookiePath)) {
    return false
  }

  return cookiePath.endsWith('/') || requestPath.charAt(cookiePath.length) === '/'
}

export class RemoteSessionCookieStore {
  /** partition → origin → (cookie name, effective Path) → mirrored cookie */
  private readonly byPartition = new Map<string, Map<string, Map<string, MirroredCookie>>>()

  private jarFor(partition: string, origin: string, create: boolean): Map<string, MirroredCookie> | undefined {
    let origins = this.byPartition.get(partition)

    if (!origins) {
      if (!create) {
        return undefined
      }

      origins = new Map()
      this.byPartition.set(partition, origins)
    }

    let jar = origins.get(origin)

    if (!jar && create) {
      jar = new Map()
      origins.set(origin, jar)
    }

    return jar
  }

  /** Record every parsable cookie from a response's `Set-Cookie` header(s); deletions are honoured. */
  record(partition: string, url: string, setCookie: string | string[] | undefined | null): void {
    const origin = originKeyFor(url)

    if (!partition || !origin || !setCookie) {
      return
    }

    const headers = Array.isArray(setCookie) ? setCookie : [setCookie]

    for (const header of headers) {
      const parsed = parseSetCookie(header)

      if (!parsed) {
        continue
      }

      if (parsed.expired) {
        this.jarFor(partition, origin, false)?.delete(cookieKey(parsed.name, parsed.path))

        continue
      }

      this.jarFor(partition, origin, true)!.set(cookieKey(parsed.name, parsed.path), {
        name: parsed.name,
        value: parsed.value,
        path: parsed.path
      })
    }
  }

  /** Seed the mirror from a session jar read (`sess.cookies.get({url})` results). */
  recordFromJar(
    partition: string,
    url: string,
    cookies: Array<{ name?: unknown; value?: unknown; path?: unknown }> | null | undefined
  ): void {
    const origin = originKeyFor(url)

    if (!partition || !origin || !Array.isArray(cookies)) {
      return
    }

    for (const cookie of cookies) {
      if (typeof cookie?.name === 'string' && typeof cookie?.value === 'string' && cookie.name && cookie.value) {
        const path = normalizeCookiePath(typeof cookie.path === 'string' ? cookie.path : undefined)

        this.jarFor(partition, origin, true)!.set(cookieKey(cookie.name, path), {
          name: cookie.name,
          value: cookie.value,
          path
        })
      }
    }
  }

  /** Present the most specific matching value per name; null when this scope has no cookies. */
  cookieHeaderFor(partition: string, url: string): string | null {
    const origin = originKeyFor(url)
    const jar = partition && origin ? this.jarFor(partition, origin, false) : undefined

    if (!jar || jar.size === 0) {
      return null
    }

    const requestPath = requestPathFor(url)

    // Stable sort preserves mirror first-seen order for ties, including rotations.
    // Chromium creation times are not available in the jar snapshot.
    const seenNames = new Set<string>()

    const pairs = [...jar.values()]
      .filter(cookie => cookiePathMatches(cookie.path, requestPath))
      .sort((a, b) => b.path.length - a.path.length)
      .filter(cookie => {
        // Starlette keeps the last duplicate name; send only the most specific
        // value so a broader gateway's cookie cannot replace this credential.
        if (seenNames.has(cookie.name)) {
          return false
        }

        seenNames.add(cookie.name)

        return true
      })
      .map(cookie => `${cookie.name}=${cookie.value}`)

    return pairs.length ? pairs.join('; ') : null
  }

  /** Clear only the canonical cookie Path of an already resolved, trusted gateway base. */
  clearGateway(partition: string, gatewayBase: string): void {
    const origin = originKeyFor(gatewayBase)
    const jar = partition && origin ? this.jarFor(partition, origin, false) : undefined

    if (!jar) {
      return
    }

    const path = requestPathFor(gatewayBase)

    for (const [key, cookie] of jar) {
      // An ancestor, nested path or sibling can belong to a different gateway.
      if (cookie.path === path) {
        jar.delete(key)
      }
    }
  }

  /**
   * Drop one partition's mirror for an origin (stale identity / forced
   * re-login). Omit the url to drop the whole partition; omit both to clear
   * everything.
   */
  clear(partition?: string, originOrUrl?: string): void {
    if (!partition) {
      this.byPartition.clear()

      return
    }

    if (!originOrUrl) {
      this.byPartition.delete(partition)

      return
    }

    const origin = originKeyFor(originOrUrl) ?? originOrUrl

    this.byPartition.get(partition)?.delete(origin)
  }
}

/** Process-lifetime mirror used by main.ts. */
export const remoteSessionCookies = new RemoteSessionCookieStore()

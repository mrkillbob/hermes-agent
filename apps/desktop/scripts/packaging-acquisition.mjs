/**
 * Keep supplier identity and structured network facts, never raw error messages
 * or cause objects: those can carry signed URLs, request headers and credentials.
 * @template T
 * @param {string} resource
 * @param {() => Promise<T>} acquire
 * @returns {Promise<T>}
 */
export async function acquirePackagingInput(resource, acquire) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await acquire()
    } catch (error) {
      const { facts, connectionTimeout, transientHttp } = packagingFailure(error)
      // Some builders retry only top-level network codes. Preserve integrity
      // checks while retrying nested timeouts and transient HTTP responses.
      if ((connectionTimeout || transientHttp) && attempt < 2) {
        await new Promise(resolve => setTimeout(resolve, 1000 * (attempt + 1)))
        continue
      }
      throw new Error(`Failed preparing ${resource} (${facts.join('; ') || 'unknown cause'})`)
    }
  }
}

/** @param {unknown} error @returns {{ facts: string[], connectionTimeout: boolean, transientHttp: boolean }} */
function packagingFailure(error) {
  const facts = []
  let connectionTimeout = false
  let transientHttp = false
  const seen = new Set()
  const pending = [error]
  while (pending.length && seen.size < 8) {
    const cause = pending.shift()
    if (!cause || typeof cause !== 'object' || seen.has(cause)) continue
    seen.add(cause)
    if (cause.code === 'UND_ERR_CONNECT_TIMEOUT') connectionTimeout = true
    if (typeof cause.name === 'string' && /^[A-Za-z][A-Za-z0-9]*Error$|^Error$/.test(cause.name)) facts.push(cause.name)
    if (typeof cause.code === 'string' && /^[A-Z][A-Z_0-9]{0,63}$/.test(cause.code)) facts.push(`code=${cause.code}`)
    const response = cause.response && typeof cause.response === 'object' ? cause.response : null
    const status = cause.statusCode ?? cause.status ?? response?.statusCode ?? response?.status
    if (Number.isInteger(status) && status >= 100 && status <= 599) {
      facts.push(`status=${status}`)
      if (status === 408 || status === 425 || status === 429 || status >= 500) transientHttp = true
    }
    if (typeof cause.url === 'string') {
      try {
        const url = new URL(cause.url)
        if (['https:', 'http:'].includes(url.protocol)) facts.push(`host=${url.hostname}`)
      } catch { /* An invalid URL supplies no safe endpoint evidence. */ }
    }
    if (cause.cause) pending.push(cause.cause)
    if (Array.isArray(cause.errors)) pending.push(...cause.errors.slice(0, 8))
  }
  return { facts: [...new Set(facts)], connectionTimeout, transientHttp }
}

/**
 * Keep supplier identity and structured network facts, never raw error messages
 * or cause objects: those can carry signed URLs, request headers and credentials.
 * @template T
 * @param {string} resource
 * @param {() => Promise<T>} acquire
 * @returns {Promise<T>}
 */
export async function acquirePackagingInput(resource, acquire) {
  try {
    return await acquire()
  } catch (error) {
    const facts = []
    const seen = new Set()
    const pending = [error]
    while (pending.length && seen.size < 8) {
      const cause = pending.shift()
      if (!cause || typeof cause !== 'object' || seen.has(cause)) continue
      seen.add(cause)
      if (typeof cause.name === 'string' && /^[A-Za-z][A-Za-z0-9]*Error$|^Error$/.test(cause.name)) facts.push(cause.name)
      if (typeof cause.code === 'string' && /^[A-Z][A-Z_0-9]{0,63}$/.test(cause.code)) facts.push(`code=${cause.code}`)
      const status = cause.statusCode ?? cause.status
      if (Number.isInteger(status) && status >= 100 && status <= 599) facts.push(`status=${status}`)
      if (typeof cause.url === 'string') {
        try {
          const url = new URL(cause.url)
          if (['https:', 'http:'].includes(url.protocol)) facts.push(`host=${url.hostname}`)
        } catch { /* An invalid URL supplies no safe endpoint evidence. */ }
      }
      if (cause.cause) pending.push(cause.cause)
      if (Array.isArray(cause.errors)) pending.push(...cause.errors.slice(0, 8))
    }
    throw new Error(`Failed preparing ${resource} (${[...new Set(facts)].join('; ') || 'unknown cause'})`)
  }
}

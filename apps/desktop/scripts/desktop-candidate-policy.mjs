import { createHash } from 'node:crypto'

/** Python's sorted, compact, ensure_ascii JSON is the cross-language identity. */
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value && typeof value === 'object') {
    return '{' + Object.keys(value).sort().map(key => canonical(key) + ':' + canonical(value[key])).join(',') + '}'
  }
  return JSON.stringify(value).replace(/[\u007f-\uffff]/g,
    character => '\\u' + character.charCodeAt(0).toString(16).padStart(4, '0'))
}

export function candidateDigest(request) {
  return createHash('sha256').update(canonical(request)).digest('hex')
}

export function rejectCandidateSigningInputs(env) {
  for (const [key, value] of Object.entries(env)) {
    const name = key.toUpperCase()
    if (value && (/^(CSC_|WIN_CSC_|APPLE_)/.test(name) || name === 'HERMES_MAC_SIGNATURE_CACHE')) {
      throw new Error('development candidate refuses ambient signing input ' + key)
    }
  }
}

/** Pure admission; receipt digests bind identity, not independent authorization. */
export function validateCandidateReceipt(candidate, { source, commit, target }, env) {
  if (candidate === undefined || candidate === null) return null
  const request = candidate.request
  if (!request || request.development_candidate !== true || request.variant !== 'bundled' ||
      request.target !== 'darwin-arm64' || target !== request.target ||
      request.source !== source || request.commit !== commit || !/^[a-f0-9]{40}$/.test(request.commit) ||
      request.release_commit != null || request.tag !== null || request.channel_request !== null || request.release_epoch !== null ||
      typeof request.version !== 'string' || !request.version ||
      candidate.requestDigest !== candidateDigest(request)) {
    throw new Error('development candidate packaging differs from its admitted source/commit/target request')
  }
  rejectCandidateSigningInputs(env)
  return request
}


import { createHash } from 'node:crypto'

/** @typedef {{ source: string, commit: string, target: string, version: string, variant: string, development_candidate: boolean, tag: string | null, channel_request: unknown, release_epoch: number | null, release_commit?: string | null, [key: string]: unknown }} CandidateRequest */
/** @typedef {{ request: CandidateRequest, requestDigest: string }} CandidateReceipt */

/**
 * Python's sorted, compact, ensure_ascii JSON is the cross-language identity.
 * @param {unknown} value
 * @returns {string}
 */
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value && typeof value === 'object') {
    const record = /** @type {Record<string, unknown>} */ (value)
    return '{' + Object.keys(record).sort().map(key => canonical(key) + ':' + canonical(record[key])).join(',') + '}'
  }
  const encoded = JSON.stringify(value)
  if (encoded === undefined) throw new Error('development candidate request contains a non-JSON value')
  return encoded.replace(/[\u007f-\uffff]/g,
    character => '\\u' + character.charCodeAt(0).toString(16).padStart(4, '0'))
}

/** @param {unknown} request @returns {string} */
export function candidateDigest(request) {
  return createHash('sha256').update(canonical(request)).digest('hex')
}

/** @param {NodeJS.ProcessEnv} env @returns {void} */
export function rejectCandidateSigningInputs(env) {
  for (const [key, value] of Object.entries(env)) {
    const name = key.toUpperCase()
    if (value && (/^(CSC_|WIN_CSC_|APPLE_)/.test(name) || name === 'HERMES_MAC_SIGNATURE_CACHE')) {
      throw new Error('development candidate refuses ambient signing input ' + key)
    }
  }
}

/**
 * Pure admission; receipt digests bind identity, not independent authorization.
 * @param {CandidateReceipt | null | undefined} candidate
 * @param {{ source: string, commit: string, target: string }} expected
 * @param {NodeJS.ProcessEnv} env
 * @returns {CandidateRequest | null}
 */
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


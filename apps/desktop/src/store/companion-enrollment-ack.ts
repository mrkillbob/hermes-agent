import { type CapturedRouteLease, type DesktopRouteFence, identifier, positiveInteger, readCapturedDecisionDelivery, type RouteSnapshot } from './companion-enrollment-route'

/** Proposed internal Task4 adapter result. NOT an accepted native wire schema.
 * Canonical signed receipt bytes stay opaque in Task4/Task5 custody; this digest cannot
 * substitute for their authenticated receipt or the durable crash-safe join.
 */
export interface NativeAcknowledgement {
  operationId: string; challengeSha256: string; approvalId: string; installationId: string
  nativeSubject: string; nativeProfileId: string; nativeInstanceId: string
  storedSessionId: string; runtimeSessionId: string
  authorityEpoch: number; bindingGeneration: number; receiptSha256: string
  issuedAtSeconds: number; expiresAtSeconds: number
}
export interface ExpectedAcknowledgement { operationId: string; challengeSha256: string; target: Readonly<RouteSnapshot> }
const strings = ['operationId','approvalId','installationId','nativeSubject','nativeProfileId','nativeInstanceId','storedSessionId','runtimeSessionId'] as const
const digests = ['challengeSha256','receiptSha256'] as const
const numbers = ['authorityEpoch','bindingGeneration','issuedAtSeconds','expiresAtSeconds'] as const
const targetKeys = ['installationId','nativeSubject','nativeProfileId','nativeInstanceId','storedSessionId','runtimeSessionId','authorityEpoch','bindingGeneration'] as const
export const sha256 = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)

/** Structural correlation only, called after captured-socket delivery and live lease check.
 * No signature/authentication is established by constructing this object or calling here.
 */
export function validateNativeAcknowledgement(value: unknown, expected: ExpectedAcknowledgement, nowMs: number): Readonly<NativeAcknowledgement> | undefined {
  if (!value || typeof value !== 'object' || !Number.isSafeInteger(nowMs) || nowMs < 0) { return undefined }
  const fields = [...strings, ...digests, ...numbers]
  const descriptors = Object.getOwnPropertyDescriptors(value)

  if (Reflect.ownKeys(value).length !== fields.length || !fields.every(key => descriptors[key] && 'value' in descriptors[key])) { return undefined }
  // Scalars are read once into a private snapshot; accessors and extra marker fields refuse.
  const result = Object.freeze(Object.fromEntries(fields.map(key => [key, descriptors[key].value])))

  if (!strings.every(key => identifier(result[key])) || !digests.every(key => sha256(result[key])) ||
    !positiveInteger(result.authorityEpoch) || !positiveInteger(result.bindingGeneration)) { return undefined }

  if (result.operationId !== expected.operationId || result.challengeSha256 !== expected.challengeSha256 ||
    !targetKeys.every(key => result[key] === expected.target[key])) { return undefined }

  const issued = result.issuedAtSeconds, expires = result.expiresAtSeconds
  const safeSecond = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 && v <= Math.floor(Number.MAX_SAFE_INTEGER / 1000)

  if (!safeSecond(issued) || !safeSecond(expires) || issued * 1000 > nowMs || expires * 1000 <= nowMs || expires <= issued || expires - issued > 60) { return undefined }

  return result as unknown as Readonly<NativeAcknowledgement>
}


export interface CapturedAcknowledgement extends NativeAcknowledgement, DesktopRouteFence {}

/** Require a locally issued captured-port result; remote JSON cannot echo/mint this fence. */
export function validateCapturedAcknowledgement(delivery: unknown, expected: ExpectedAcknowledgement & { captureLease: CapturedRouteLease }, nowMs: number): Readonly<CapturedAcknowledgement> | undefined {
  const captured = readCapturedDecisionDelivery(delivery, expected.captureLease)

  if (!captured || !expected.captureLease.isCurrent() || !Object.keys(captured.target).every(key => captured.target[key as keyof RouteSnapshot] === expected.target[key as keyof RouteSnapshot]) || captured.desktopFence.desktopSocketIncarnationId !== expected.target.desktopSocketIncarnationId ||
    captured.desktopFence.desktopRouteRevision !== expected.target.desktopRouteRevision) { return undefined }

  const native = validateNativeAcknowledgement(captured.nativeAcknowledgement, expected, nowMs)

  return native ? Object.freeze({ ...native, ...captured.desktopFence }) : undefined
}

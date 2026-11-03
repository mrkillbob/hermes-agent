import { describe, expect, it } from 'vitest'

import { validateCapturedAcknowledgement, validateNativeAcknowledgement } from './companion-enrollment-ack'
import { captureAlreadyOpenRoute, createDesktopRouteRegistry, readCapturedDecisionDelivery } from './companion-enrollment-route'
import { ack, challenge, digest, fixture, operationId } from './enrollment-fixtures.test-support'

function validate(value: unknown, now = 125000) {
  const f = fixture(), lease = captureAlreadyOpenRoute(f.source, 'exact-target')!
  const result = validateNativeAcknowledgement(value, { operationId, challengeSha256: digest, target: lease.snapshot }, now)
  lease.release();

 return result
}

describe('normalized internal native acknowledgement, never a binding', () => {
  it('retains full exact join fields in an immutable candidate', () => {
    const value = ack(), result = validate(value)!
    expect(result).toEqual(value); expect(Object.isFrozen(result)).toBe(true)
    value.approvalId = 'mutated'; expect(result.approvalId).toBe('approval-a')
  })
  it.each(['operationId','challengeSha256','installationId','nativeSubject','nativeProfileId','nativeInstanceId','storedSessionId','runtimeSessionId','authorityEpoch','bindingGeneration'])('rejects changed %s', key => {
    expect(validate(ack({ [key]: typeof ack()[key as keyof ReturnType<typeof ack>] === 'number' ? 99 : 'other' }))).toBeUndefined()
  })
  it.each([0, -1, NaN, Infinity, 9007199254740992])('rejects unsafe integer %s', value => {
    expect(validate(ack({ authorityEpoch: value }))).toBeUndefined()
    expect(validate(ack({ bindingGeneration: value }))).toBeUndefined()
  })
  it.each([{ expiresAtSeconds: 125 }, { issuedAtSeconds: 126 }, { expiresAtSeconds: 161 }, { expiresAtSeconds: 9007199254740 }, { issuedAtSeconds: 100.5 }])('rejects invalid or unsafe time %j', value => {
    expect(validate(ack(value))).toBeUndefined()
  })
  it.each([null, {}, { ...ack(), authenticated: true }, { ...ack(), receiptSha256: 'not-a-digest' }, { ...ack(), approvalId: 'bad\nidentifier' }])('rejects malformed/extra caller assertions %j', value => {
    expect(validate(value)).toBeUndefined()
  })
})

describe('captured ACK provenance',()=>{
 it('refuses another route or capture even when native tuple, operation and fence strings coincide',async()=>{
  const f=fixture(), physical=new EventTarget() as EventTarget & {readyState:number}; physical.readyState=1
  f.socket.requestDecision=async()=>ack()
  const registry=createDesktopRouteRegistry()
  registry.observeOwnedRoute('a',{...f.live,physicalSocket:physical})
  registry.observeOwnedRoute('b',{...f.live,connectionId:'connection-b',profile:'desktop-b',physicalSocket:physical})
  registry.observeOwnedRoute('alias',{...f.live,physicalSocket:physical})
  const a=captureAlreadyOpenRoute(registry.source,'a')!, b=captureAlreadyOpenRoute(registry.source,'b')!, alias=captureAlreadyOpenRoute(registry.source,'alias')!
  const delivery=await a.dispatchOnce({operationId,challenge,challengeSha256:digest},new AbortController().signal)
  expect(validateCapturedAcknowledgement(delivery,{operationId,challengeSha256:digest,target:b.snapshot,captureLease:b},125000)).toBeUndefined()
  expect(validateCapturedAcknowledgement(delivery,{operationId,challengeSha256:digest,target:alias.snapshot,captureLease:alias},125000)).toBeUndefined()
  expect(validateCapturedAcknowledgement(delivery,{operationId,challengeSha256:digest,target:a.snapshot,captureLease:a},125000)).toBeDefined()
  registry.dispose()
 })
 it('does not expose mutable delivery or native-ACK records',async()=>{
  const f=fixture(), native=ack();f.socket.requestDecision=async()=>native
  const lease=captureAlreadyOpenRoute(f.source,'exact-target')!, delivery=await lease.dispatchOnce({operationId,challenge,challengeSha256:digest},new AbortController().signal)
  const view=readCapturedDecisionDelivery(delivery)!
  expect(Object.isFrozen(view)).toBe(true); expect(Object.isFrozen(view.nativeAcknowledgement)).toBe(true)
  native.approvalId='mutated'
  expect(validateCapturedAcknowledgement(delivery,{operationId,challengeSha256:digest,target:lease.snapshot,captureLease:lease},125000)?.approvalId).toBe('approval-a')
  lease.release()
 })
})

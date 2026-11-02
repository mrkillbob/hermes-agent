import { afterEach, describe, expect, it, vi } from 'vitest'

import { captureAlreadyOpenRoute, createDesktopRouteRegistry, isDesktopRouteRevision, readCapturedDecisionDelivery } from './companion-enrollment-route'
import { challenge, digest, fixture, operationId } from './enrollment-fixtures.test-support'
afterEach(() => vi.useRealTimers())
const intent = { operationId, challenge, challengeSha256: digest }
describe('already-open exact-route lease', () => {
  it('refuses missing or closed targets without creating a route', () => {
    const f = fixture(); expect(captureAlreadyOpenRoute(f.source, 'missing')).toBeUndefined()
    f.close(); expect(captureAlreadyOpenRoute(f.source, 'exact-target')).toBeUndefined()
    expect(f.calls).toEqual([]); expect(f.listeners.size).toBe(0)
  })
  it('captures immutable scalars and dispatches only once on that socket', async () => {
    const f = fixture(), lease = captureAlreadyOpenRoute(f.source, 'exact-target')!
    expect(lease).toBeDefined(); expect(lease.snapshot.connectionId).toBe('connection-a')
    expect(Object.isFrozen(lease.snapshot)).toBe(true)
    await lease.dispatchOnce(intent, new AbortController().signal)
    await expect(lease.dispatchOnce(intent, new AbortController().signal)).rejects.toThrow()
    expect(f.calls).toEqual([intent]); lease.release(); expect(f.listeners.size).toBe(0)
  })
  it.each(['connectionId','profile','targetProfile','installationId','nativeSubject','nativeProfileId','nativeInstanceId','storedSessionId','runtimeSessionId','authorityEpoch','bindingGeneration','socket','incarnation'] as const)('invalidates changed %s even when labels/session IDs are reused', async key => {
    const f = fixture(), lease = captureAlreadyOpenRoute(f.source, 'exact-target')!
    expect(lease).toBeDefined()
    f.live = { ...f.live, [key]: typeof f.live[key] === 'number' ? 99 : typeof f.live[key] === 'object' ? {} : 'other' }; f.change()
    expect(lease.isCurrent()).toBe(false)
    await expect(lease.dispatchOnce(intent, new AbortController().signal)).rejects.toThrow()
    expect(f.calls).toEqual([]); lease.release()
  })
  it('rejects an acknowledgement after same-ID socket replacement', async () => {
    const f = fixture(); let finish!: (value: unknown) => void
    f.socket.requestDecision = async () => new Promise(resolve => { finish = resolve })
    const lease = captureAlreadyOpenRoute(f.source, 'exact-target')!; expect(lease).toBeDefined()
    const result = lease.dispatchOnce(intent, new AbortController().signal)
    f.live = { ...f.live, incarnation: {} }; f.change(); finish({ ok: true })
    await expect(result).rejects.toThrow(); lease.release()
  })
  it.each([0, -1, NaN, 9007199254740992])('refuses unsafe native generation %s', value => {
    const f = fixture(); f.live.bindingGeneration = value
    expect(captureAlreadyOpenRoute(f.source, 'exact-target')).toBeUndefined()
  })
  it('released or aborted leases cannot dispatch, and cannot revive', async () => {
    const f = fixture(), lease = captureAlreadyOpenRoute(f.source, 'exact-target')!; expect(lease).toBeDefined()
    lease.release(); expect(lease.isCurrent()).toBe(false)
    await expect(lease.dispatchOnce(intent, new AbortController().signal)).rejects.toThrow()
    const other = captureAlreadyOpenRoute(f.source, 'exact-target')!, abort = new AbortController(); abort.abort()
    await expect(other.dispatchOnce(intent, abort.signal)).rejects.toThrow(); other.release(); expect(f.calls).toEqual([])
  })
  it('lease validity ends exactly at the supplied source deadline even without a change event', () => {
    vi.useFakeTimers(); vi.setSystemTime(1000)
    const f=fixture(); f.live.validUntilMs=1100
    const lease=captureAlreadyOpenRoute(f.source,'exact-target')!
    expect(lease.isCurrent()).toBe(true); vi.advanceTimersByTime(100)
    expect(lease.isCurrent()).toBe(false); lease.release()
  })
  it('aborting a pending decision settles locally even when the adapter ignores abort', async () => {
    const f=fixture(); f.socket.requestDecision=async()=>new Promise(()=>{})
    const lease=captureAlreadyOpenRoute(f.source,'exact-target')!, abort=new AbortController()
    const result=lease.dispatchOnce(intent,abort.signal); abort.abort()
    let settled=false; void result.catch(()=>{settled=true})
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve()
    expect(settled).toBe(true); lease.release()
  })
  it('invalidates clock rollback after any later observed progress, not just capture time', () => {
    vi.useFakeTimers(); vi.setSystemTime(125000)
    const f=fixture(), lease=captureAlreadyOpenRoute(f.source,'exact-target')!
    vi.setSystemTime(126000); expect(lease.isCurrent()).toBe(true)
    vi.setSystemTime(125500); expect(lease.isCurrent()).toBe(false); lease.release()
  })
})

describe('issued desktop physical-socket incarnation and route revision', () => {
  class PhysicalSocket extends EventTarget {
    readyState = 1
    close() { this.readyState = 3; this.dispatchEvent(new Event('close')) }
  }
  it('issues a non-reused physical identity and a lossless monotonically advancing revision', () => {
    const f=fixture(), physical=new PhysicalSocket(), registry=createDesktopRouteRegistry()
    registry.observeOwnedRoute('exact-target', {...f.live, physicalSocket:physical})
    const first=registry.source.lookup('exact-target')!
    expect(first.desktopSocketIncarnationId).toBeTypeOf('string'); expect(first.desktopRouteRevision).toBe('1')
    registry.observeOwnedRoute('exact-target', {...f.live, physicalSocket:physical})
    expect(registry.source.lookup('exact-target')?.desktopRouteRevision).toBe('1')
    registry.observeOwnedRoute('exact-target', {...f.live, nativeSubject:'user-b', physicalSocket:physical})
    const second=registry.source.lookup('exact-target')!
    expect(second.desktopSocketIncarnationId).toBe(first.desktopSocketIncarnationId)
    expect(second.desktopRouteRevision).toBe('2')
    registry.dispose()
  })
  it('invalidates synchronously on close and refuses reuse of a retired physical object', () => {
    const f=fixture(), physical=new PhysicalSocket(), registry=createDesktopRouteRegistry()
    registry.observeOwnedRoute('exact-target', {...f.live, physicalSocket:physical})
    const first=registry.source.lookup('exact-target')!, lease=captureAlreadyOpenRoute(registry.source,'exact-target')!, lost=vi.fn()
    lease.onInvalidation(lost); physical.close()
    expect(lost).toHaveBeenCalledOnce(); expect(lease.isCurrent()).toBe(false)
    physical.readyState=1; expect(registry.observeOwnedRoute('exact-target',{...f.live,physicalSocket:physical})).toBe(false)
    registry.observeOwnedRoute('exact-target',{...f.live,physicalSocket:new PhysicalSocket()})
    expect(registry.source.lookup('exact-target')?.desktopSocketIncarnationId).not.toBe(first.desktopSocketIncarnationId)
    registry.dispose()
  })
  it('binds ACK delivery to the locally captured fence, never a native echoed marker', async () => {
    const f=fixture(), registry=createDesktopRouteRegistry()
    f.socket.requestDecision=async()=>({native:'result'})
    registry.observeOwnedRoute('exact-target',{...f.live,physicalSocket:new PhysicalSocket()})
    const lease=captureAlreadyOpenRoute(registry.source,'exact-target')!, result=await lease.dispatchOnce(intent,new AbortController().signal)
    expect(readCapturedDecisionDelivery(result)?.desktopFence).toEqual({desktopSocketIncarnationId:lease.snapshot.desktopSocketIncarnationId,desktopRouteRevision:lease.snapshot.desktopRouteRevision})
    expect(readCapturedDecisionDelivery({...result})).toBeUndefined()
    expect(readCapturedDecisionDelivery({authenticated:true,desktopFence:{...lease.snapshot}})).toBeUndefined()
    registry.dispose()
  })
  it.each(['0','01','+1','1.0','9223372036854775808',1])('rejects noncanonical or overflowing decimal revision %j', value=>{
    expect(isDesktopRouteRevision(value)).toBe(false)
  })
})

it('old physical retirement cannot delete a replacement published by another invalidation callback',()=>{
 class Physical extends EventTarget{readyState=1;close(){this.readyState=3;this.dispatchEvent(new Event('close'))}}
 const f=fixture(), old=new Physical(), replacement=new Physical(), registry=createDesktopRouteRegistry()
 registry.observeOwnedRoute('a',{...f.live,physicalSocket:old}); registry.observeOwnedRoute('b',{...f.live,physicalSocket:old})
 registry.source.watch('a',()=>registry.observeOwnedRoute('b',{...f.live,physicalSocket:replacement}))
 old.close(); expect(registry.source.lookup('b')).toBeDefined()
 const lease=captureAlreadyOpenRoute(registry.source,'b')!;expect(lease.isCurrent()).toBe(true);registry.dispose()
})

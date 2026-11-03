import { afterEach, describe, expect, it, vi } from 'vitest'

import { ack, challenge, digest, fixture, operationId } from '@/store/enrollment-fixtures.test-support'

import { createDesktopEnrollment, createUnavailableEnrollment } from './decision-state'
const deps = (f: ReturnType<typeof fixture>) => ({ source: f.source, digestChallenge: async () => digest, newOperationId: () => operationId })
afterEach(() => vi.useRealTimers())

function approve(c: ReturnType<typeof createDesktopEnrollment>, code: string, key: string) { c.selectTarget(key);

 return c.approve(code,key) }

function setup() { vi.useFakeTimers(); vi.setSystemTime(125000); const f=fixture(); f.socket.requestDecision=async()=>ack();

 return {f,c:createDesktopEnrollment(deps(f))} }

describe('one-time desktop decision lifecycle', () => {
  it('production facade cannot be enabled by flags, fixtures or a source argument', async () => {
    const f=fixture(), c=createUnavailableEnrollment({ source:f.source, authenticated:true })
    await approve(c,challenge,'exact-target'); expect(c.snapshot().status).toBe('unavailable'); expect(f.calls).toEqual([]); c.dispose()
  })
  it('validates exact-route delivery into candidate-only state and keeps all join fields', async () => {
    const {f,c}=setup(); await approve(c,challenge,'exact-target')
    expect(c.snapshot().status).toBe('candidate'); expect(c.snapshot().candidate).toEqual({...ack(),desktopSocketIncarnationId:'desktop-socket-a',desktopRouteRevision:'1'})
    expect(c.snapshot().target!.connectionId).toBe('connection-a')
    expect(c.snapshot()).not.toHaveProperty('connected'); expect(c.snapshot()).not.toHaveProperty('challenge')
    expect(f.listeners.size).toBe(1); c.dispose(); expect(f.listeners.size).toBe(0); expect(vi.getTimerCount()).toBe(0)
  })
  it.each(['', 'bad', challenge+'=', 'A'.repeat(42)+'B'])('refuses malformed challenge %j before capture or dispatch', async value => {
    const {f,c}=setup(); await c.approve(value,'exact-target')
    expect(c.snapshot().status).toBe('invalid'); expect(f.listeners.size).toBe(0); expect(f.calls).toEqual([]); c.dispose()
  })
  it('does not choose a session by a matching label or fallback', async () => {
    const {f,c}=setup(); await approve(c,challenge,'native-a')
    expect(c.snapshot().status).toBe('unavailable'); expect(f.calls).toEqual([]); c.dispose()
  })
  it('hashes the decoded 32 bytes, not the user string', async () => {
    const {f}=setup();

 const hash=vi.fn(async(bytes:Uint8Array)=>{ expect([...bytes]).toEqual(Array(32).fill(0));

 return digest })

    const c=createDesktopEnrollment({...deps(f),digestChallenge:hash}); await approve(c,challenge,'exact-target')
    expect(hash).toHaveBeenCalledOnce(); expect(c.snapshot().status).toBe('candidate'); c.dispose()
  })
  it('dispatches only once despite repeated clicks or candidate re-entry', async () => {
    const {f}=setup(); let finish!:(v:unknown)=>void; const request=vi.fn(async()=>new Promise(resolve=>{finish=resolve})); f.socket.requestDecision=request
    const c=createDesktopEnrollment(deps(f)), first=approve(c,challenge,'exact-target'); await Promise.resolve(); await Promise.resolve()
    await approve(c,challenge,'exact-target'); finish(ack()); await first; await approve(c,challenge,'exact-target')
    expect(request).toHaveBeenCalledOnce(); expect(c.snapshot().status).toBe('candidate'); c.dispose()
  })
  it('route loss during hashing prevents dispatch', async () => {
    const {f}=setup(); let finish!:(v:string)=>void
    const c=createDesktopEnrollment({...deps(f),digestChallenge:()=>new Promise(resolve=>{finish=resolve})})
    const result=approve(c,challenge,'exact-target'); f.close(); finish(digest); await result
    expect(c.snapshot().status).toBe('unavailable'); expect(f.calls).toEqual([]); c.dispose()
  })
  it('close after dispatch is uncertain, consumes local attempt and ignores late acknowledgement', async () => {
    const {f}=setup(); let finish!:(v:unknown)=>void; const request=vi.fn(async()=>new Promise(resolve=>{finish=resolve})); f.socket.requestDecision=request
    const c=createDesktopEnrollment(deps(f)), result=approve(c,challenge,'exact-target'); await Promise.resolve(); await Promise.resolve()
    c.cancel(); expect(c.snapshot().status).toBe('uncertain'); finish(ack()); await result
    await approve(c,challenge,'exact-target'); expect(request).toHaveBeenCalledOnce(); expect(c.snapshot().status).toBe('uncertain'); c.dispose()
  })
  it('timeout never retries or claims a remote cancellation', async () => {
    const {f}=setup(); const request=vi.fn(async()=>new Promise(()=>{})); f.socket.requestDecision=request
    const c=createDesktopEnrollment(deps(f)); void approve(c,challenge,'exact-target'); await Promise.resolve(); await Promise.resolve()
    vi.advanceTimersByTime(30000); expect(c.snapshot().status).toBe('uncertain')
    await approve(c,challenge,'exact-target'); expect(request).toHaveBeenCalledOnce(); c.dispose(); expect(vi.getTimerCount()).toBe(0)
  })
  it('candidate expiry and later owner replacement remove local candidate availability', async () => {
    const {f,c}=setup(); await approve(c,challenge,'exact-target'); vi.advanceTimersByTime(25000)
    expect(c.snapshot().status).toBe('expired'); expect(c.snapshot().candidate).toBeUndefined(); c.dispose()
    vi.setSystemTime(125000); const next=createDesktopEnrollment(deps(f)); await approve(next,challenge,'exact-target')
    f.live={...f.live,incarnation:{}}; f.change(); expect(next.snapshot().status).toBe('uncertain'); expect(next.snapshot().candidate).toBeUndefined(); next.dispose()
  })
  it('wrong target ack is uncertain, never an authenticated binding', async () => {
    const {f}=setup(); f.socket.requestDecision=async()=>ack({nativeSubject:'user-b'})
    const c=createDesktopEnrollment(deps(f)); await approve(c,challenge,'exact-target'); expect(c.snapshot().status).toBe('uncertain'); c.dispose()
  })
  it('dispose before async completion cannot notify a future view', async () => {
    const {f}=setup(); let finish!:(v:unknown)=>void; f.socket.requestDecision=async()=>new Promise(resolve=>{finish=resolve})
    const c=createDesktopEnrollment(deps(f)), changed=vi.fn(), unsub=c.subscribe(changed)
    const result=approve(c,challenge,'exact-target'); await Promise.resolve(); await Promise.resolve(); c.dispose(); const count=changed.mock.calls.length
    finish(ack()); await result; expect(changed.mock.calls.length).toBe(count); unsub(); expect(f.listeners.size).toBe(0)
  })
  it('shows the captured target before approval and refuses same-key replacement before the click', async () => {
    const {f,c}=setup(); expect(c.selectTarget('exact-target')).toBe(true)
    expect(c.snapshot().target!.runtimeSessionId).toBe('runtime-a')
    f.live={...f.live,nativeSubject:'user-b'}; f.change()
    await c.approve(challenge,'exact-target')
    expect(c.snapshot().status).toBe('unavailable'); expect(f.calls).toEqual([]); c.dispose()
  })
  it('selected source lease expires without a request or further event', () => {
    const {f,c}=setup(); f.live.validUntilMs=125100
    expect(c.selectTarget('exact-target')).toBe(true)
    vi.advanceTimersByTime(100); expect(c.snapshot().status).toBe('unavailable')
    expect(f.calls).toEqual([]); c.dispose()
  })
  it('does not rearm pending deadlines after observed backward time', async () => {
    const {f}=setup(); f.live.validUntilMs=140000
    const c=createDesktopEnrollment({...deps(f),digestChallenge:async()=>new Promise(()=>{})})
    void approve(c,challenge,'exact-target')
    vi.setSystemTime(100000); vi.advanceTimersByTime(15000)
    expect(c.snapshot().status).toBe('unavailable'); expect(vi.getTimerCount()).toBe(0); c.dispose()
  })
  it.each(['cancel','dispose'] as const)('cannot arm candidate timers after subscriber %s', async action => {
    const {c}=setup(); c.subscribe(()=>{if(c.snapshot().status==='candidate'){c[action]()}})
    await approve(c,challenge,'exact-target'); const status=c.snapshot().status
    expect(vi.getTimerCount()).toBe(0); vi.advanceTimersByTime(25000)
    expect(c.snapshot().status).toBe(status); c.dispose()
  })
  it('keeps the consented target when correcting a syntactically invalid challenge', async () => {
    const {c}=setup(); c.selectTarget('exact-target'); await c.approve('bad','exact-target')
    expect(c.snapshot().status).toBe('invalid'); expect(c.snapshot().target?.runtimeSessionId).toBe('runtime-a')
    await c.approve(challenge,'exact-target'); expect(c.snapshot().status).toBe('candidate'); c.dispose()
  })
})

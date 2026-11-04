// @vitest-environment jsdom
import { JsonRpcGatewayClient } from '@hermes/shared'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { createDesktopEnrollment } from '@/app/companion-enrollment/decision-state'

import { validateCapturedAcknowledgement } from './companion-enrollment-ack'
import { createDesktopEnrollmentClientCapture } from './companion-enrollment-client'
import { captureAlreadyOpenRoute, createDesktopRouteRegistry, type RouteSnapshot } from './companion-enrollment-route'
import { ack, challenge, digest, fixture, operationId } from './enrollment-fixtures.test-support'

class Socket extends EventTarget {
  static OPEN=1
  static CONNECTING=0
  static CLOSING=2
  static CLOSED=3
  readyState=0
  sent:string[]=[]
  send(text:string){this.sent.push(text)}
  open(){this.readyState=1;this.dispatchEvent(new Event('open'))}
  close(){this.readyState=3;this.dispatchEvent(new CloseEvent('close'))}
  reply(id:string,result:unknown){this.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({jsonrpc:'2.0',id,result})}))}
}
const resources:Array<()=>void>=[]
afterEach(()=>{resources.splice(0).reverse().forEach(clean=>clean());vi.unstubAllGlobals()})

function setup(interceptClose=false){
 vi.stubGlobal('WebSocket',Socket)

 const registry=createDesktopRouteRegistry(), sockets:Socket[]=[], factory=vi.fn((_url:string)=>{const socket=new Socket();sockets.push(socket);

return socket as unknown as WebSocket})

 const capture=createDesktopEnrollmentClientCapture(factory,registry)
 const client=new JsonRpcGatewayClient({socketFactory:capture.socketFactory,replay:false,heartbeatIntervalMs:0,onSocketClose:()=>interceptClose})
 resources.push(()=>{registry.dispose();client.close();capture.dispose()})
 const f=fixture()
 const target={...f.live,socket:{isOpen:()=>client.connectionState==='open',requestDecision:async(_intent:unknown,selected:Readonly<RouteSnapshot>,signal:AbortSignal)=>client.request('fixture.native-decision',{session_id:selected.runtimeSessionId},1000,signal)}}

 async function connect(){const pending=client.connect('ws://fixture.invalid');const socket=sockets.at(-1)!;socket.open();await pending;

return socket}

 return{registry,capture,client,factory,sockets,target,connect}
}

describe('real gateway client socket-factory capture',()=>{
 it('does not dial or register a target on construction, before open, or after disposal',()=>{
  const {capture,factory,registry,target}=setup()
  expect(factory).not.toHaveBeenCalled();expect(capture.observeOwnedTarget('exact-target',target)).toBe(false)
  expect(registry.source.lookup('exact-target')).toBeUndefined();capture.dispose()
  expect(capture.observeOwnedTarget('exact-target',target)).toBe(false)
  expect(()=>capture.socketFactory('ws://fixture.invalid')).toThrow()
  expect(factory).not.toHaveBeenCalled()
 })
 it('captures the actual client-created socket and binds its channel ACK to the exact lease',async()=>{
  const {capture,registry,target,connect}=setup();const physical=await connect()
  expect(capture.observeOwnedTarget('exact-target',target)).toBe(true)
  const lease=captureAlreadyOpenRoute(registry.source,'exact-target')!
  const pending=lease.dispatchOnce({operationId,challenge,challengeSha256:digest},new AbortController().signal)
  const request=JSON.parse(physical.sent.at(-1)!)
  expect(request.method).toBe('fixture.native-decision')
  physical.reply(request.id,ack({issuedAtSeconds:Math.floor(Date.now()/1000),expiresAtSeconds:Math.floor(Date.now()/1000)+60}))
  const delivery=await pending
  expect(validateCapturedAcknowledgement(delivery,{operationId,challengeSha256:digest,target:lease.snapshot,captureLease:lease},Date.now())).toBeDefined()
  lease.release()
 })
 it('revokes on physical close even when the client owner intercepts its closed-state transition',async()=>{
  const {capture,registry,target,connect,client}=setup(true);const old=await connect()
  expect(capture.observeOwnedTarget('exact-target',target)).toBe(true)
  const lease=captureAlreadyOpenRoute(registry.source,'exact-target')!, lost=vi.fn();lease.onInvalidation(lost)
  old.close()
  expect(client.connectionState).toBe('open')
  expect(lost).toHaveBeenCalledOnce();expect(lease.isCurrent()).toBe(false)
  expect(registry.source.lookup('exact-target')).toBeUndefined()
 })
 it('does not reuse a physical incarnation or republish a target automatically on client reconnect',async()=>{
  const {capture,registry,target,connect}=setup(true);const old=await connect()
  expect(capture.observeOwnedTarget('exact-target',target)).toBe(true);const first=registry.source.lookup('exact-target')!
  old.close();await connect()
  expect(registry.source.lookup('exact-target')).toBeUndefined()
  expect(capture.observeOwnedTarget('exact-target',target)).toBe(true);const second=registry.source.lookup('exact-target')!
  expect(second.desktopSocketIncarnationId).not.toBe(first.desktopSocketIncarnationId)
  old.dispatchEvent(new CloseEvent('close'))
  expect(registry.source.lookup('exact-target')).toBe(second)
 })
 it('disposal leaves the real client and another writer replacement untouched',async()=>{
  const {capture,registry,target,connect,client}=setup();const physical=await connect()
  expect(capture.observeOwnedTarget('exact-target',target)).toBe(true)
  const other=new Socket();other.open()
  registry.observeOwnedRoute('exact-target',{...target,nativeSubject:'other-user',physicalSocket:other as unknown as WebSocket})
  const replacement=registry.source.lookup('exact-target')!
  capture.dispose()
  expect(registry.source.lookup('exact-target')).toBe(replacement)
  expect(physical.readyState).toBe(1);expect(client.connectionState).toBe('open')
 })
 it('cannot reclaim a route key replaced by another source writer',async()=>{
  const {capture,registry,target,connect}=setup();await connect()
  capture.observeOwnedTarget('exact-target',target)
  const other=new Socket();other.open()
  registry.observeOwnedRoute('exact-target',{...target,nativeSubject:'other-user',physicalSocket:other as unknown as WebSocket})
  const replacement=registry.source.lookup('exact-target')!
  expect(capture.observeOwnedTarget('exact-target',target)).toBe(false)
  expect(registry.source.lookup('exact-target')).toBe(replacement)
 })
 it('failed factory replacement invalidates old consent without closing the existing client',async()=>{
  const {capture,registry,target,connect,client,factory}=setup();const physical=await connect()
  capture.observeOwnedTarget('exact-target',target)
  const lease=captureAlreadyOpenRoute(registry.source,'exact-target')!
  factory.mockImplementationOnce(()=>{throw new Error('fixture construction failed')})
  expect(()=>capture.socketFactory('ws://fixture.invalid')).toThrow()
  expect(lease.isCurrent()).toBe(false);expect(physical.readyState).toBe(1)
  expect(client.connectionState).toBe('open')
 })
 it('physical close revokes pending approval through the existing controller and real request channel',async()=>{
  const {capture,registry,target,connect}=setup(true);const old=await connect()
  capture.observeOwnedTarget('exact-target',target)
  const controller=createDesktopEnrollment({source:registry.source,digestChallenge:async()=>digest,newOperationId:()=>operationId})
  resources.push(()=>controller.dispose())
  controller.selectTarget('exact-target')
  const result=controller.approve(challenge,'exact-target')
  await Promise.resolve(); await Promise.resolve()
  const id=JSON.parse(old.sent.at(-1)!).id
  old.close();await result
  expect(controller.snapshot().status).toBe('uncertain')
  const replacement=await connect()
  old.reply(id,ack())
  expect(controller.snapshot().status).toBe('uncertain')
  expect(replacement.sent).toEqual([])
  expect(registry.source.lookup('exact-target')).toBeUndefined()
 })

 it('reentrant disposal during replacement invalidation prevents another factory call',async()=>{
  const {capture,registry,target,connect,factory}=setup(true);await connect()
  capture.observeOwnedTarget('exact-target',target)
  registry.source.watch('exact-target',()=>capture.dispose())
  expect(()=>capture.socketFactory('ws://fixture.invalid')).toThrow()
  expect(factory).toHaveBeenCalledOnce()
 })
 it('reentrant disposal during route publication cannot leave an orphan observation',async()=>{
  const {capture,registry,target,connect}=setup();await connect()
  registry.source.watch('exact-target',()=>capture.dispose())
  expect(capture.observeOwnedTarget('exact-target',target)).toBe(false)
  expect(registry.source.lookup('exact-target')).toBeUndefined()
 })

})

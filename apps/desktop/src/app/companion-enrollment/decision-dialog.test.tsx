import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
// @vitest-environment jsdom
import { StrictMode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ack, challenge, digest, enrollmentCopy, fixture, operationId } from '@/store/enrollment-fixtures.test-support'

import { EnrollmentDecisionDialog } from './decision-dialog'
import { createDesktopEnrollment, createUnavailableEnrollment } from './decision-state'
afterEach(() => { cleanup(); vi.useRealTimers() })
const targets=[{key:'exact-target',label:'Visible selected session'}]
describe('explicit one-time setup UI', () => {
  it('is absent from ordinary closed entry and creates no request', () => {
    const f=fixture(), c=createUnavailableEnrollment()
    const view=render(<EnrollmentDecisionDialog controller={c} copy={enrollmentCopy} onClose={()=>{}} open={false} targets={targets} />)
    expect(view.container.textContent).toBe(''); expect(f.calls).toEqual([]); c.dispose()
  })
  it('production setup stays unavailable even if targets are passed', () => {
    const c=createUnavailableEnrollment()
    render(<EnrollmentDecisionDialog controller={c} copy={enrollmentCopy} onClose={()=>{}} open targets={targets} />)
    expect(screen.getByRole('button',{name:'Approve once'}).hasAttribute('disabled')).toBe(true)
    expect(screen.getByRole('status').textContent).toMatch(/unavailable.*disconnected/i)
    c.dispose()
  })
  it('requires explicit target and approval, clears code and never claims a live binding', async () => {
    const f=fixture(); const request=vi.fn(async()=>ack({issuedAtSeconds:Math.floor(Date.now()/1000),expiresAtSeconds:Math.floor(Date.now()/1000)+60})); f.socket.requestDecision=request
    const c=createDesktopEnrollment({source:f.source,digestChallenge:async()=>digest,newOperationId:()=>operationId})
    render(<EnrollmentDecisionDialog controller={c} copy={enrollmentCopy} onClose={()=>{}} open targets={targets} />)
    const code=screen.getByLabelText('One-time challenge') as HTMLInputElement
    const target=screen.getByLabelText('Exact live session') as HTMLSelectElement
    expect(target.value).toBe(''); expect(request).not.toHaveBeenCalled()
    fireEvent.change(code,{target:{value:challenge}}); fireEvent.change(target,{target:{value:'exact-target'}})
    expect(request).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('button',{name:'Approve once'}))
    expect(code.value).toBe('')
    await waitFor(()=>expect(c.snapshot().status).toBe('candidate'))
    expect(screen.getByRole('status').textContent).toMatch(/candidate.*disconnected/i)
    expect(request).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole('button',{name:'Approve once'})); expect(request).toHaveBeenCalledOnce()
    expect(code.getAttribute('autocomplete')).toBe('off'); c.dispose()
  })
  it('cancel before dispatch clears challenge and does not send any decision', () => {
    const f=fixture(), c=createDesktopEnrollment({source:f.source}), close=vi.fn()
    render(<EnrollmentDecisionDialog controller={c} copy={enrollmentCopy} onClose={close} open targets={targets} />)
    const input=screen.getByLabelText('One-time challenge') as HTMLInputElement
    fireEvent.change(input,{target:{value:challenge}}); fireEvent.click(screen.getByRole('button',{name:'Cancel setup'}))
    expect(input.value).toBe(''); expect(close).toHaveBeenCalledOnce(); expect(c.snapshot().status).toBe('cancelled'); expect(f.calls).toEqual([]); c.dispose()
  })
  it('unmount during pending request is uncertain and late callbacks cannot confirm a reopened view', async () => {
    const f=fixture(); let finish!:(v:unknown)=>void; f.socket.requestDecision=async()=>new Promise(resolve=>{finish=resolve})
    const c=createDesktopEnrollment({source:f.source,digestChallenge:async()=>digest,newOperationId:()=>operationId})
    const view=render(<EnrollmentDecisionDialog controller={c} copy={enrollmentCopy} onClose={()=>{}} open targets={targets} />)
    fireEvent.change(screen.getByLabelText('One-time challenge'),{target:{value:challenge}})
    fireEvent.change(screen.getByLabelText('Exact live session'),{target:{value:'exact-target'}})
    fireEvent.click(screen.getByRole('button',{name:'Approve once'}))
    await waitFor(()=>expect(finish).toBeTypeOf('function'))
    view.unmount(); await waitFor(()=>expect(c.snapshot().status).toBe('uncertain'))
    finish(ack()); await Promise.resolve(); await Promise.resolve()
    render(<EnrollmentDecisionDialog controller={c} copy={enrollmentCopy} onClose={()=>{}} open targets={targets} />)
    expect(screen.getByRole('status').textContent).toMatch(/uncertain.*disconnected/i)
    expect(screen.getByRole('button',{name:'Approve once'}).hasAttribute('disabled')).toBe(true); c.dispose()
  })
  it('StrictMode mount probing does not cancel fresh explicit setup', async () => {
    const f=fixture(), c=createDesktopEnrollment({source:f.source})
    render(<StrictMode><EnrollmentDecisionDialog controller={c} copy={enrollmentCopy} onClose={()=>{}} open targets={targets} /></StrictMode>)
    await Promise.resolve(); expect(c.snapshot().status).toBe('ready')
    expect(screen.getByLabelText('One-time challenge').closest('fieldset')?.hasAttribute('disabled')).toBe(false)
    expect(f.calls).toEqual([]); c.dispose()
  })
  it.each(['unmount','close-prop'])('revokes pending consent synchronously on %s before an already-queued digest can dispatch', async mode => {
    const f=fixture(), request=vi.fn(async()=>ack()); f.socket.requestDecision=request
    const c=createDesktopEnrollment({source:f.source,digestChallenge:async()=>digest,newOperationId:()=>operationId})
    const view=render(<EnrollmentDecisionDialog controller={c} copy={enrollmentCopy} onClose={()=>{}} open targets={targets} />)
    fireEvent.change(screen.getByLabelText('One-time challenge'),{target:{value:challenge}})
    fireEvent.change(screen.getByLabelText('Exact live session'),{target:{value:'exact-target'}})
    fireEvent.click(screen.getByRole('button',{name:'Approve once'}))

    if(mode==='unmount'){view.unmount()}else{view.rerender(<EnrollmentDecisionDialog controller={c} copy={enrollmentCopy} onClose={()=>{}} open={false} targets={targets} />)}
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve()
    expect(request).not.toHaveBeenCalled(); expect(c.snapshot().status).toBe('cancelled')
    expect(f.listeners.size).toBe(0); c.dispose()
  })
})

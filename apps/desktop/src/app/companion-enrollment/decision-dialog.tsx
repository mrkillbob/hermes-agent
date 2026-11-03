import { useEffect, useId, useRef, useState, useSyncExternalStore } from 'react'

import { Button } from '@/components/ui/button'
import { controlVariants } from '@/components/ui/control'
import { Input } from '@/components/ui/input'

import type { DecisionStatus, DesktopEnrollmentController } from './decision-state'

export interface EnrollmentDecisionCopy {
  title: string; description: string; legend: string; challengeLabel: string; sessionLabel: string; chooseSession: string
  connectionLabel: string; nativeLabel: string; sessionIdsLabel: string; approve: string; cancel: string
  messages: Record<DecisionStatus, string>
}
interface Props {
  open: boolean
  controller: DesktopEnrollmentController
  targets: readonly { key: string; label: string }[]
  onClose(): void
  /** Integration must supply localized copy; this unmounted candidate has English defaults. */
  copy: EnrollmentDecisionCopy
}

/** Explicit one-time setup only. This component is not imported by ordinary companion launch. */
export function EnrollmentDecisionDialog({ open, controller, targets, onClose, copy }: Props) {
  const state = useSyncExternalStore(controller.subscribe, controller.snapshot, controller.snapshot)
  const [challenge, setChallenge] = useState(''), [target, setTarget] = useState('')
  const titleId = useId()
  const lifetimes = useRef(new Map<DesktopEnrollmentController, number>())
  useEffect(() => {
    setChallenge(''); setTarget('')

    if (!open) { return }
    const entries = lifetimes.current
    entries.set(controller, (entries.get(controller) ?? 0) + 1)

    return () => {
      // Pending consent is revoked synchronously, before any queued digest can dispatch.
      if (['pending', 'candidate'].includes(controller.snapshot().status)) {
        entries.delete(controller); controller.cancel();

 return
      }

      const closing = (entries.get(controller) ?? 0) + 1
      entries.set(controller, closing)
      // StrictMode reattaches the same owner before this microtask; a real exit does not.
      queueMicrotask(() => {
        if (entries.get(controller) === closing) { entries.delete(controller); controller.cancel() }
      })
    }
  }, [open, controller])

  if (!open) { return null }
  const editable = state.status === 'ready' || state.status === 'invalid'

  const close = () => { setChallenge(''); controller.cancel(); onClose() }

  return <section aria-labelledby={titleId} onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); close() } }} role="dialog">
    <h2 id={titleId}>{copy.title}</h2>
    <p>{copy.description}</p>
    <p aria-live="polite" role="status">{copy.messages[state.status]}</p>
    <form onSubmit={event => {
      event.preventDefault()

      if (!editable || !challenge || !target || !state.target) { return }
      const entered = challenge; setChallenge(''); void controller.approve(entered, target)
    }}>
      <fieldset disabled={!editable}>
        <legend>{copy.legend}</legend>
        <label>{copy.challengeLabel}<Input autoComplete="off" onChange={event => setChallenge(event.target.value)} spellCheck={false} type="password" value={challenge} /></label>
        <label>{copy.sessionLabel}<select className={controlVariants()} onChange={event => { const key = event.target.value; setTarget(key); controller.selectTarget(key) }} value={target}>
          <option value="">{copy.chooseSession}</option>
          {targets.map(item => <option key={item.key} value={item.key}>{item.label} ({item.key})</option>)}
        </select></label>
        {state.target ? <dl>
          <dt>{copy.connectionLabel}</dt><dd>{state.target.connectionId} / {state.target.profile}</dd>
          <dt>{copy.nativeLabel}</dt><dd>{state.target.nativeProfileId} / {state.target.nativeInstanceId}</dd>
          <dt>{copy.sessionIdsLabel}</dt><dd>{state.target.storedSessionId} / {state.target.runtimeSessionId}</dd>
        </dl> : null}
        <Button disabled={!editable || !challenge || !target || !state.target} type="submit">{copy.approve}</Button>
      </fieldset>
      <Button onClick={close} type="button">{copy.cancel}</Button>
    </form>
  </section>
}

import type { WorldCondition, WorldEvent } from './world-events'

export interface DialogueSubject {
  title: string
  detail?: string
  condition?: WorldCondition
  event?: WorldEvent
}

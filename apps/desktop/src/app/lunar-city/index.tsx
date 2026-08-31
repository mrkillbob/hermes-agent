import './lunar-city.css'

import { LOCAL_CONNECTION_ID } from '@hermes/shared'
import { useStore } from '@nanostores/react'
import { type CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Activity,
  Archive,
  Check,
  Clock,
  Eye,
  Layers3,
  Lock,
  Moon,
  Network,
  Pause,
  PawPrint,
  Play,
  RefreshCw,
  SteeringWheel,
  Users,
  Wrench,
  X
} from '@/lib/icons'
import { cn } from '@/lib/utils'
import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile'
import { $connection } from '@/store/session'

import { createKanbanCitySource } from './adapters/kanban'
import { startLunarCityReconciler } from './adapters/reconciler'
import { LunarCityCommandRuntime } from './command-runtime'
import { CameraControls } from './components/camera-controls'
import type { InspectorSessionTarget } from './components/entity-inspector'
import { entityFriendlyLabel, EntityList } from './components/entity-list'
import { LeaderDialogueRuntime } from './components/leader-dialogue-runtime'
import { QualityControl, type RendererStatus } from './components/quality-control'
import { reducedMotionPresentation } from './components/reduced-motion'
import { SourceHealthPanel } from './components/source-health'
import {
  leaderModelFocusKeyForOwner,
  leaderModelIdForOwner,
  leaderOwnerForProfile,
  profileLeaders
} from './leader-runtime'
import { type LeaderOwner, leaderOwnerKey, type LeaderSession, resolveLeaderSession } from './leader-sessions'
import { loadWorldManifest } from './manifest'
import type {
  CameraControlState,
  CameraIntent,
  EntityKey,
  LunarCityIntent,
  LunarCityWorldHandle,
  LunarEntity,
  QualityTier,
  WorldManifestV2
} from './model'
import { $lunarCitySnapshot } from './store'
import { createLunarCityWorld } from './world/create-world'

const LUNAR_CITY_MANIFEST_URL = './lunar-city/v2/world-manifest.v2.json'

type WorkerState = 'blocked' | 'break' | 'done' | 'heartbeat' | 'ready' | 'resource' | 'review' | 'triage' | 'working'

interface Room {
  label: string
  state: WorkerState
  detail: string
}

interface District {
  id: string
  name: string
  leader: string
  species: string
  description: string
  accent: string
  icon: 'archive' | 'council' | 'paw' | 'research' | 'tools'
  position: { left: string; top: string }
  rooms: Room[]
  workerCount: number
}

interface Task {
  id: string
  districtId: string
  label: string
  detail: string
  progress: number
  state: WorkerState
  eta: string
  rate: number
}

interface Worker {
  design: 'archivist' | 'artist' | 'builder' | 'courier' | 'dispatcher' | 'orbital' | 'verifier'
  id: string
  name: string
  districtId: string
  soul: string
  taskId: string
  state: WorkerState
  routeClass: string
  delay: number
}

const DISTRICTS: District[] = [
  {
    id: 'library',
    name: 'Library',
    leader: 'Owl Librarian',
    species: 'Owl',
    description: 'Context retrieval, source comparison, and quiet conversation with the librarian.',
    accent: 'text-(--ui-purple)',
    icon: 'archive',
    position: { left: '18%', top: '20%' },
    rooms: [
      { label: 'Consultation desk', state: 'working', detail: 'The librarian is speaking with a child worker.' },
      { label: 'Stacks', state: 'resource', detail: 'Two workers are waiting for a source bundle.' },
      { label: 'Quiet reading room', state: 'break', detail: 'The brood is taking a short recovery break.' }
    ],
    workerCount: 7
  },
  {
    id: 'research',
    name: 'Research Lab',
    leader: 'Fox Scientist',
    species: 'Fox',
    description: 'Telescopes, microscopes, and experiment bays turn questions into evidence.',
    accent: 'text-(--ui-cyan)',
    icon: 'research',
    position: { left: '66%', top: '18%' },
    rooms: [
      { label: 'Observatory', state: 'working', detail: 'A scout is tracking a new source on the lunar horizon.' },
      { label: 'Microscopy bench', state: 'review', detail: 'A sample is under the scientist’s inspection beam.' },
      { label: 'Sample locker', state: 'ready', detail: 'The next courier can collect the prepared sample.' }
    ],
    workerCount: 9
  },
  {
    id: 'arts',
    name: 'Arts Studio',
    leader: 'Moth Artist',
    species: 'Moth',
    description: 'A warm studio for visual identity, storyboards, and creative experiments.',
    accent: 'text-(--ui-orange)',
    icon: 'paw',
    position: { left: '17%', top: '54%' },
    rooms: [
      { label: 'Sketch room', state: 'working', detail: 'The artist is guiding a brush-bot through a new frame.' },
      { label: 'Render chamber', state: 'heartbeat', detail: 'A live render is pulsing through the chamber.' },
      { label: 'Critique nook', state: 'review', detail: 'A visual reviewer is checking legibility.' }
    ],
    workerCount: 6
  },
  {
    id: 'engineering',
    name: 'Engineering Workshop',
    leader: 'Badger Builder',
    species: 'Badger',
    description: 'A practical workshop for repairs, tests, and safe changes.',
    accent: 'text-(--ui-yellow)',
    icon: 'tools',
    position: { left: '56%', top: '70%' },
    rooms: [
      { label: 'Build floor', state: 'working', detail: 'A builder child is assembling a verified patch.' },
      { label: 'Test chamber', state: 'triage', detail: 'One worker is queued for deterministic triage.' },
      { label: 'Review bridge', state: 'blocked', detail: 'A gate is closed until an independent review arrives.' }
    ],
    workerCount: 11
  },
  {
    id: 'council',
    name: 'Council Hall',
    leader: 'Stag Coordinator',
    species: 'Stag',
    description: 'The civic center where groups align, route work, and resolve dependencies.',
    accent: 'text-(--ui-warm)',
    icon: 'council',
    position: { left: '52%', top: '47%' },
    rooms: [
      { label: 'Dispatch floor', state: 'working', detail: 'The coordinator is assigning work to the right group.' },
      { label: 'Resource desk', state: 'resource', detail: 'The council is waiting for a model slot to open.' },
      { label: 'Handoff gallery', state: 'done', detail: 'Completed work is returning to its parent group.' }
    ],
    workerCount: 8
  },
  {
    id: 'release',
    name: 'Release Gatehouse',
    leader: 'Crane Verifier',
    species: 'Crane',
    description: 'A verification gate where work is checked before it can leave the city.',
    accent: 'text-(--ui-blue)',
    icon: 'tools',
    position: { left: '78%', top: '51%' },
    rooms: [
      { label: 'Admission gate', state: 'ready', detail: 'The next completed worker can enter.' },
      { label: 'Verification theater', state: 'review', detail: 'A result is being checked against its contract.' },
      { label: 'Return dock', state: 'done', detail: 'A worker is carrying a completed seed home.' }
    ],
    workerCount: 5
  },
  {
    id: 'operations',
    name: 'Operations Depot',
    leader: 'Otter Dispatcher',
    species: 'Otter',
    description: 'Transit, routes, and service support for every other district.',
    accent: 'text-(--ui-green)',
    icon: 'council',
    position: { left: '26%', top: '82%' },
    rooms: [
      { label: 'READY bus stop', state: 'ready', detail: 'Three workers are waiting for the next route.' },
      { label: 'Route control', state: 'heartbeat', detail: 'The city heartbeat is synchronizing transit.' },
      { label: 'Service bay', state: 'working', detail: 'A helper bot is refueling a lunar shuttle.' }
    ],
    workerCount: 10
  },
  {
    id: 'archive',
    name: 'Archive & Acquisition',
    leader: 'Tortoise Archivist',
    species: 'Tortoise',
    description: 'A receiving hall for durable records, provenance, and long-term care.',
    accent: 'text-(--ui-cyan)',
    icon: 'archive',
    position: { left: '83%', top: '82%' },
    rooms: [
      { label: 'Receiving hall', state: 'working', detail: 'A courier is cataloging a new source package.' },
      { label: 'Long-term stacks', state: 'resource', detail: 'Archive shelves are waiting for a storage slot.' },
      { label: 'Lineage garden', state: 'done', detail: 'A completed memory seed has been planted.' }
    ],
    workerCount: 8
  }
]

const TASKS: Task[] = [
  {
    id: 'survey-archive',
    districtId: 'research',
    label: 'Survey the archive',
    detail: 'Compare the latest source bundle',
    progress: 42,
    state: 'working',
    eta: '02:14',
    rate: 5
  },
  {
    id: 'calibrate-telescope',
    districtId: 'research',
    label: 'Calibrate telescope',
    detail: 'Prepare the observatory for a new scan',
    progress: 68,
    state: 'review',
    eta: '01:06',
    rate: 3
  },
  {
    id: 'index-sources',
    districtId: 'library',
    label: 'Index source bundle',
    detail: 'Sort provenance into the public stacks',
    progress: 76,
    state: 'resource',
    eta: '03:40',
    rate: 2
  },
  {
    id: 'render-storyboard',
    districtId: 'arts',
    label: 'Render storyboard',
    detail: 'Bake the next visual experiment',
    progress: 31,
    state: 'working',
    eta: '04:28',
    rate: 6
  },
  {
    id: 'verify-patch',
    districtId: 'engineering',
    label: 'Verify patch train',
    detail: 'Run the independent contract checks',
    progress: 84,
    state: 'triage',
    eta: '00:48',
    rate: 4
  },
  {
    id: 'resolve-dependency',
    districtId: 'council',
    label: 'Resolve dependency',
    detail: 'Find a free model slot for the queue',
    progress: 57,
    state: 'resource',
    eta: '01:52',
    rate: 2
  },
  {
    id: 'admit-release',
    districtId: 'release',
    label: 'Admit release seed',
    detail: 'Check the final handoff contract',
    progress: 91,
    state: 'review',
    eta: '00:22',
    rate: 2
  },
  {
    id: 'route-workers',
    districtId: 'operations',
    label: 'Route worker shift',
    detail: 'Send helpers to their next rooms',
    progress: 63,
    state: 'heartbeat',
    eta: '01:18',
    rate: 4
  },
  {
    id: 'preserve-lineage',
    districtId: 'archive',
    label: 'Preserve lineage',
    detail: 'Plant a durable memory seed',
    progress: 48,
    state: 'working',
    eta: '02:52',
    rate: 3
  }
]

const WORKERS: Worker[] = [
  {
    design: 'orbital',
    id: 'w-01',
    name: 'Pip',
    districtId: 'research',
    soul: 'curious scout; confirm before concluding',
    taskId: 'survey-archive',
    state: 'working',
    routeClass: 'route-north',
    delay: 0
  },
  {
    design: 'archivist',
    id: 'w-02',
    name: 'Kite',
    districtId: 'library',
    soul: 'patient keeper; preserve provenance',
    taskId: 'index-sources',
    state: 'resource',
    routeClass: 'route-west',
    delay: 1.8
  },
  {
    design: 'builder',
    id: 'w-03',
    name: 'Mica',
    districtId: 'engineering',
    soul: 'careful maker; test every joint',
    taskId: 'verify-patch',
    state: 'triage',
    routeClass: 'route-east',
    delay: 3.2
  },
  {
    design: 'artist',
    id: 'w-04',
    name: 'Nix',
    districtId: 'arts',
    soul: 'playful maker; make the invisible legible',
    taskId: 'render-storyboard',
    state: 'working',
    routeClass: 'route-south',
    delay: 4.4
  },
  {
    design: 'dispatcher',
    id: 'w-05',
    name: 'Rook',
    districtId: 'council',
    soul: 'steady coordinator; unblock the next move',
    taskId: 'resolve-dependency',
    state: 'resource',
    routeClass: 'route-center',
    delay: 2.7
  },
  {
    design: 'verifier',
    id: 'w-06',
    name: 'Dew',
    districtId: 'release',
    soul: 'skeptical guardian; trust evidence',
    taskId: 'admit-release',
    state: 'review',
    routeClass: 'route-gate',
    delay: 5.7
  },
  {
    design: 'courier',
    id: 'w-07',
    name: 'Sol',
    districtId: 'operations',
    soul: 'reliable carrier; arrive with the right resource',
    taskId: 'route-workers',
    state: 'ready',
    routeClass: 'route-transit',
    delay: 1.1
  },
  {
    design: 'archivist',
    id: 'w-08',
    name: 'Tock',
    districtId: 'archive',
    soul: 'quiet gardener; leave a durable trail',
    taskId: 'preserve-lineage',
    state: 'done',
    routeClass: 'route-south',
    delay: 6.4
  }
]

const STATE_TONE: Record<WorkerState, string> = {
  blocked: 'text-(--ui-red)',
  break: 'text-(--ui-cyan)',
  done: 'text-(--ui-green)',
  heartbeat: 'text-(--ui-purple)',
  ready: 'text-(--ui-green)',
  resource: 'text-(--ui-orange)',
  review: 'text-(--ui-blue)',
  triage: 'text-(--ui-yellow)',
  working: 'text-(--ui-accent)'
}

const STATE_RING: Record<WorkerState, string> = {
  blocked: 'border-(--ui-red) bg-(--ui-red)/15',
  break: 'border-(--ui-cyan) bg-(--ui-cyan)/15',
  done: 'border-(--ui-green) bg-(--ui-green)/15',
  heartbeat: 'border-(--ui-purple) bg-(--ui-purple)/15',
  ready: 'border-(--ui-green) bg-(--ui-green)/15',
  resource: 'border-(--ui-orange) bg-(--ui-orange)/15',
  review: 'border-(--ui-blue) bg-(--ui-blue)/15',
  triage: 'border-(--ui-yellow) bg-(--ui-yellow)/15',
  working: 'border-(--ui-accent) bg-(--ui-accent)/15'
}

function districtIcon(icon: District['icon']) {
  if (icon === 'archive') {
    return Archive
  }

  if (icon === 'council') {
    return Users
  }

  if (icon === 'research') {
    return Eye
  }

  if (icon === 'tools') {
    return Wrench
  }

  return PawPrint
}

function taskSnapshot(task: Task, tick: number) {
  const progress = (task.progress + tick * task.rate) % 101

  const state: WorkerState =
    progress >= 96 ? 'done' : progress >= 74 && task.state === 'working' ? 'review' : task.state

  return { ...task, progress: Math.round(progress), state }
}

function roomSnapshot(room: Room, task: Task, tick: number) {
  const snapshot = taskSnapshot(task, tick)

  return {
    ...room,
    state: snapshot.state,
    detail: `${room.detail} ${snapshot.progress}% through ${snapshot.label.toLowerCase()}.`
  }
}

export function disposeLunarCityRuntime(
  stopReconciler: (() => void) | undefined,
  stopSnapshot: (() => void) | undefined,
  world: LunarCityWorldHandle | undefined
): void {
  stopReconciler?.()
  stopSnapshot?.()
  world?.destroy()
}

export interface LunarCityProps {
  onOpenEntitySession?: (target: InspectorSessionTarget) => void
  onOpenFullChat?: (storedId: string, owner: LeaderOwner) => Promise<void> | void
  onOpenMemoryGraph: () => void
}

function sameProfileLeaderList(left: readonly LunarEntity[], right: readonly LunarEntity[]): boolean {
  return left.length === right.length && left.every((entity, index) => entity.key === right[index]?.key)
}

interface LunarCityOperationsProps {
  getCameraOrder(): readonly EntityKey[]
  onQualityChange(tier: QualityTier): void
  onSelect(entity: LunarEntity): void
  qualityTier: QualityTier
  reducedMotion: boolean
  rendererStatus: RendererStatus
  selectedEntityKey?: EntityKey
}

/**
 * The text-first operational surface owns its narrow store subscription so
 * high-frequency world publications never re-render the route or canvas.
 */
function LunarCityOperations({
  getCameraOrder,
  onQualityChange,
  onSelect,
  qualityTier,
  reducedMotion,
  rendererStatus,
  selectedEntityKey
}: LunarCityOperationsProps) {
  const snapshot = useStore($lunarCitySnapshot)
  const motion = reducedMotionPresentation(reducedMotion)

  return (
    <aside aria-label="Lunar City accessible operations" className="lunar-city-accessible-operations">
      {rendererStatus === 'unavailable' ? (
        <p className="lunar-city-renderer-fallback" role="status">
          3D world renderer unavailable. Live Hermes operations remain available below.
        </p>
      ) : null}
      <p className="lunar-city-motion-status">
        {motion.snapToDestination
          ? 'Reduced motion: destinations snap into place; camera easing and looping clips are stopped.'
          : 'Motion: camera easing and worker travel are enabled.'}
      </p>
      <QualityControl onTierChange={onQualityChange} rendererStatus={rendererStatus} tier={qualityTier} />
      <SourceHealthPanel sources={snapshot.sources} />
      <EntityList
        cameraOrder={getCameraOrder()}
        onSelect={onSelect}
        selectedEntityKey={selectedEntityKey}
        snapshot={snapshot}
      />
    </aside>
  )
}

export function LunarCity({ onOpenEntitySession, onOpenFullChat, onOpenMemoryGraph }: LunarCityProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const activeGatewayProfile = useStore($activeGatewayProfile)
  const connection = useStore($connection)
  const [selectedId, setSelectedId] = useState('research')
  const [inside, setInside] = useState(false)
  const [playing, setPlaying] = useState(true)
  const [tick, setTick] = useState(0)
  const [cameraState, setCameraState] = useState<CameraControlState>({ focusedEntityKey: undefined, following: false })
  const [selectedEntityKey, setSelectedEntityKey] = useState<EntityKey | undefined>(undefined)
  const [focusedEntityLabel, setFocusedEntityLabel] = useState<string | undefined>(undefined)
  const [qualityTier, setQualityTier] = useState<QualityTier>('efficient')
  const [rendererStatus, setRendererStatus] = useState<RendererStatus>('degraded')
  const [operationsReady, setOperationsReady] = useState(false)

  const [reducedMotion, setReducedMotion] = useState(
    () =>
      typeof globalThis.matchMedia === 'function' && globalThis.matchMedia('(prefers-reduced-motion: reduce)').matches
  )

  const [worldManifest, setWorldManifest] = useState<WorldManifestV2 | undefined>(undefined)

  const [profileLeaderEntities, setProfileLeaderEntities] = useState(() =>
    profileLeaders($lunarCitySnapshot.get().entities)
  )

  const [selectedLeaderOwner, setSelectedLeaderOwner] = useState<LeaderOwner | undefined>(undefined)
  const [leaderSession, setLeaderSession] = useState<LeaderSession | undefined>(undefined)
  const [leaderSessionError, setLeaderSessionError] = useState<string | undefined>(undefined)
  const worldHandleRef = useRef<LunarCityWorldHandle | undefined>(undefined)
  const qualityTierRef = useRef(qualityTier)
  const profileLeaderEntitiesRef = useRef(profileLeaderEntities)
  const stopReconcilerRef = useRef<(() => void) | undefined>(undefined)

  const kanbanScope = useMemo(() => {
    const connectionId =
      connection?.connectionId?.trim() || (connection?.mode === 'remote' ? undefined : LOCAL_CONNECTION_ID)

    return connectionId
      ? {
          connectionId,
          profile: normalizeProfileKey(activeGatewayProfile)
        }
      : undefined
  }, [activeGatewayProfile, connection?.connectionId, connection?.mode])

  const dispatchCamera = (intent: CameraIntent): void => {
    const world = worldHandleRef.current

    world?.dispatchCamera(intent)
    const state = world?.getCameraState() ?? { focusedEntityKey: undefined, following: false }
    setCameraState(state)

    if (!state.focusedEntityKey) {
      setFocusedEntityLabel(undefined)
    } else {
      const entity = $lunarCitySnapshot.get().entities.get(state.focusedEntityKey)
      setFocusedEntityLabel(entity ? entityFriendlyLabel(entity) : 'selected entity')
    }
  }

  const selectEntity = (entity: LunarEntity): void => {
    setSelectedEntityKey(entity.key)
    dispatchCamera({ kind: 'focus', entityKey: entity.key, follow: false })
    setFocusedEntityLabel(entityFriendlyLabel(entity))
  }

  const changeQuality = (tier: QualityTier): void => {
    qualityTierRef.current = tier
    setQualityTier(tier)
    worldHandleRef.current?.setQuality(tier)
  }

  useEffect(() => {
    if (typeof globalThis.matchMedia !== 'function') {
      return
    }

    const query = globalThis.matchMedia('(prefers-reduced-motion: reduce)')

    const applyPreference = (matches: boolean): void => {
      setReducedMotion(matches)
      worldHandleRef.current?.setReducedMotion(matches)
    }

    const onChange = (event: MediaQueryListEvent): void => applyPreference(event.matches)
    applyPreference(query.matches)
    query.addEventListener?.('change', onChange)

    return () => query.removeEventListener?.('change', onChange)
  }, [])

  const selectedLeaderKey = selectedLeaderOwner ? leaderOwnerKey(selectedLeaderOwner) : undefined

  const selectedLeaderModel = selectedLeaderOwner ? leaderModelIdForOwner(selectedLeaderOwner) : undefined

  const onLeaderStateChange = useCallback(
    (state: Parameters<LunarCityWorldHandle['setLeaderAnimation']>[1]): void => {
      if (selectedLeaderModel) {
        worldHandleRef.current?.setLeaderAnimation(selectedLeaderModel, state)
      }
    },
    [selectedLeaderModel]
  )

  const closeLeaderDialogue = (): void => {
    if (leaderSessionError) {
      onLeaderStateChange('idle')
    }

    setSelectedLeaderOwner(undefined)
    setLeaderSession(undefined)
    setLeaderSessionError(undefined)
  }

  const openLeader = (entity: LunarEntity): void => {
    const owner = leaderOwnerForProfile(entity)

    if (!owner) {
      return
    }

    // The source profile is the selected identity. The animal model is only a
    // deterministic visual assignment and never a route or mutation key.
    setSelectedLeaderOwner(owner)
    setLeaderSession(undefined)
    setLeaderSessionError(undefined)
    dispatchCamera({ kind: 'focus', entityKey: leaderModelFocusKeyForOwner(owner), follow: false })
    setFocusedEntityLabel(`${owner.profile} leader`)
  }

  // Profile entities change far less often than workers. Keep this compact
  // semantic list separate from the imperative world snapshot listener so
  // worker/frame publications do not re-render the React route.
  useEffect(
    () =>
      $lunarCitySnapshot.listen(snapshot => {
        const next = profileLeaders(snapshot.entities)
        const current = profileLeaderEntitiesRef.current

        if (!sameProfileLeaderList(current, next)) {
          profileLeaderEntitiesRef.current = next
          setProfileLeaderEntities(next)
        }
      }),
    []
  )

  useEffect(() => {
    if (!selectedLeaderKey) {
      return
    }

    if (!profileLeaderEntities.some(entity => leaderOwnerKey(leaderOwnerForProfile(entity)!) === selectedLeaderKey)) {
      setSelectedLeaderOwner(undefined)
      setLeaderSession(undefined)
      setLeaderSessionError(undefined)
    }
  }, [profileLeaderEntities, selectedLeaderKey])

  useEffect(() => {
    if (!selectedLeaderOwner || !selectedLeaderKey) {
      return
    }

    let current = true
    const owner = selectedLeaderOwner

    setLeaderSession(undefined)
    setLeaderSessionError(undefined)
    void resolveLeaderSession(owner).then(
      resolved => {
        if (current && leaderOwnerKey(owner) === selectedLeaderKey) {
          setLeaderSession(resolved)
        }
      },
      error => {
        if (current && leaderOwnerKey(owner) === selectedLeaderKey) {
          setLeaderSessionError(error instanceof Error ? error.message : String(error))
        }
      }
    )

    return () => {
      current = false
    }
  }, [selectedLeaderKey, selectedLeaderOwner])

  useEffect(() => {
    if (leaderSessionError) {
      onLeaderStateChange('unavailable')
    }
  }, [leaderSessionError, onLeaderStateChange])

  const voiceAvailable = Boolean(
    selectedLeaderOwner &&
    kanbanScope &&
    selectedLeaderOwner.connectionId === kanbanScope.connectionId &&
    selectedLeaderOwner.profile === kanbanScope.profile
  )

  const openSelectedLeaderFullChat = async (storedId: string, owner: LeaderOwner): Promise<void> => {
    if (!onOpenFullChat) {
      throw new Error('Open Full Chat is available only from the dedicated Lunar City route')
    }

    await onOpenFullChat(storedId, owner)
  }

  // The reconciler is imperative route lifetime state, so the world cleanup can stop it before disposal.
  // eslint-disable-next-line no-restricted-syntax
  useEffect(() => {
    if (!worldManifest) {
      return
    }

    const kanbanSource = kanbanScope
      ? createKanbanCitySource({ manifest: worldManifest, scope: kanbanScope })
      : undefined

    const stop = startLunarCityReconciler({ optionalSources: kanbanSource ? [kanbanSource] : [] })
    let stopped = false

    const stopOnce = (): void => {
      if (stopped) {
        return
      }

      stopped = true

      if (stopReconcilerRef.current === stopOnce) {
        stopReconcilerRef.current = undefined
      }

      stop()
    }

    stopReconcilerRef.current = stopOnce

    return stopOnce
  }, [kanbanScope, worldManifest])

  // The Babylon handle is imperative runtime state, not a mirror of reactive route data.
  // eslint-disable-next-line no-restricted-syntax
  useEffect(() => {
    const canvas = canvasRef.current

    if (!canvas) {
      return
    }

    canvas.dataset.worldStatus = 'loading'

    const abortController = new AbortController()
    let disposed = false
    let generation = 0
    let manifest: WorldManifestV2 | undefined
    let restorationArmedGeneration: number | undefined
    let restorationAttempted = false
    let world: LunarCityWorldHandle | undefined
    let stopSnapshot: (() => void) | undefined

    const handleWorldIntent = (intent: LunarCityIntent): void => {
      if (intent.kind === 'camera-state' && intent.state) {
        setCameraState(intent.state)

        const focused = intent.state.focusedEntityKey
          ? $lunarCitySnapshot.get().entities.get(intent.state.focusedEntityKey)
          : undefined

        setFocusedEntityLabel(
          intent.state.focusedEntityKey ? (focused ? entityFriendlyLabel(focused) : 'selected entity') : undefined
        )
      } else if (intent.kind === 'select-focus') {
        setSelectedEntityKey(intent.entityKey)
        const entity = $lunarCitySnapshot.get().entities.get(intent.entityKey)
        setFocusedEntityLabel(entity ? entityFriendlyLabel(entity) : 'selected entity')
      } else if (intent.kind === 'clear-selection') {
        setSelectedEntityKey(undefined)
        setFocusedEntityLabel(undefined)
      }
    }

    const retireWorld = (): void => {
      stopSnapshot?.()
      stopSnapshot = undefined
      const retired = world
      world = undefined

      if (worldHandleRef.current === retired) {
        worldHandleRef.current = undefined
      }

      retired?.destroy()
    }

    const createWorldGeneration = async (): Promise<void> => {
      const ownGeneration = ++generation

      try {
        manifest ??= await loadWorldManifest(LUNAR_CITY_MANIFEST_URL, abortController.signal)

        if (disposed || ownGeneration !== generation) {
          return
        }

        setWorldManifest(manifest)

        const created = await createLunarCityWorld(
          canvas,
          manifest,
          handleWorldIntent,
          undefined,
          LUNAR_CITY_MANIFEST_URL
        )

        if (disposed || ownGeneration !== generation) {
          created.destroy()
        } else {
          world = created
          worldHandleRef.current = created
          created.setQuality(qualityTierRef.current)
          created.setReducedMotion(
            typeof globalThis.matchMedia === 'function' &&
              globalThis.matchMedia('(prefers-reduced-motion: reduce)').matches
          )
          created.applySnapshot($lunarCitySnapshot.get())
          stopSnapshot = $lunarCitySnapshot.listen(snapshot => {
            if (!disposed && ownGeneration === generation && worldHandleRef.current === created) {
              created.applySnapshot(snapshot)
            }
          })
          canvas.dataset.worldStatus = 'ready'
          setRendererStatus('ready')
          setOperationsReady(true)
        }
      } catch (error) {
        if (
          !disposed &&
          ownGeneration === generation &&
          !(error instanceof DOMException && error.name === 'AbortError')
        ) {
          canvas.dataset.worldStatus = 'unavailable'
          setRendererStatus('unavailable')
          setOperationsReady(true)
        }
      }
    }

    const onContextLost = (event: Event): void => {
      event.preventDefault()

      if (disposed) {
        return
      }

      if (restorationArmedGeneration !== undefined) {
        return
      }

      canvas.dataset.worldStatus = 'restoring'
      setRendererStatus('degraded')
      generation += 1
      retireWorld()

      if (restorationAttempted) {
        canvas.dataset.worldStatus = 'unavailable'
        setRendererStatus('unavailable')

        return
      }

      restorationArmedGeneration = generation
    }

    const onContextRestored = (): void => {
      const armedGeneration = restorationArmedGeneration

      if (disposed || armedGeneration === undefined || armedGeneration !== generation || restorationAttempted) {
        return
      }

      restorationArmedGeneration = undefined
      restorationAttempted = true
      void createWorldGeneration()
    }

    canvas.addEventListener('webglcontextlost', onContextLost)
    canvas.addEventListener('webglcontextrestored', onContextRestored)
    void createWorldGeneration()

    return () => {
      disposed = true
      generation += 1
      abortController.abort()
      canvas.removeEventListener('webglcontextlost', onContextLost)
      canvas.removeEventListener('webglcontextrestored', onContextRestored)
      disposeLunarCityRuntime(stopReconcilerRef.current, stopSnapshot, world)
      stopSnapshot = undefined
      worldHandleRef.current = undefined
      world = undefined
    }
  }, [])

  useEffect(() => {
    if (!playing) {
      return
    }

    const timer = window.setInterval(() => setTick(value => (value + 1) % 101), 1200)

    return () => window.clearInterval(timer)
  }, [playing])

  const selected = useMemo(() => DISTRICTS.find(district => district.id === selectedId) ?? DISTRICTS[0]!, [selectedId])

  const selectedTasks = useMemo(
    () => TASKS.filter(task => task.districtId === selected.id).map(task => taskSnapshot(task, tick)),
    [selected.id, tick]
  )

  const selectedRooms = selected.rooms.map((room, index) =>
    roomSnapshot(room, selectedTasks[index % selectedTasks.length] ?? TASKS[0]!, tick)
  )

  const taskById = useMemo(() => new Map(TASKS.map(task => [task.id, task])), [])

  const workerSnapshots = WORKERS.map(worker => {
    const task = taskById.get(worker.taskId) ?? TASKS[0]!
    const snapshot = taskSnapshot(task, tick)

    return { ...worker, task: snapshot, state: snapshot.state === 'done' ? 'done' : worker.state }
  })

  const activeWorkers = workerSnapshots.filter(
    worker => worker.state === 'working' || worker.state === 'review' || worker.state === 'triage'
  ).length

  const transitWorkers = workerSnapshots.filter(
    worker => worker.state === 'ready' || worker.state === 'resource'
  ).length

  const blockedWorkers = workerSnapshots.filter(worker => worker.state === 'blocked').length

  const cityProgress = Math.round(
    TASKS.reduce((sum, task) => sum + taskSnapshot(task, tick).progress, 0) / TASKS.length
  )

  const LeaderIcon = districtIcon(selected.icon)

  return (
    <div
      className={cn(
        'lunar-city relative flex min-h-0 flex-1 flex-col overflow-hidden bg-(--ui-bg-editor)',
        !playing && 'is-paused'
      )}
    >
      <header className="lunar-city-hud pointer-events-none absolute inset-x-0 top-0 z-50 flex items-center justify-between gap-4 px-4 py-3 sm:px-6">
        <div className="pointer-events-auto flex min-w-0 items-center gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-(--ui-accent)/12 text-(--ui-accent)">
            <Moon aria-hidden="true" size={18} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-sm font-semibold text-foreground">Lunar City</h2>
              <span className="rounded-full bg-(--ui-green)/12 px-2 py-0.5 text-[0.62rem] font-medium text-(--ui-green)">
                SIMULATION
              </span>
            </div>
            <p className="truncate text-xs text-muted-foreground">
              A living diorama of Hermes groups and their worker routes
            </p>
          </div>
        </div>
        <div className="pointer-events-auto flex shrink-0 items-center gap-1.5">
          <span className="hidden rounded-full border border-(--ui-stroke-tertiary) px-2 py-1 text-[0.62rem] tabular-nums text-muted-foreground sm:inline">
            CYCLE {String(tick + 1).padStart(3, '0')}
          </span>
          <Button
            aria-label={playing ? 'Pause city simulation' : 'Play city simulation'}
            onClick={() => setPlaying(value => !value)}
            size="icon-sm"
            variant="ghost"
          >
            {playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
          </Button>
          <Button aria-label="Open memory graph" onClick={onOpenMemoryGraph} size="sm" variant="secondary">
            <Network aria-hidden="true" />
            <span className="hidden sm:inline">Memory graph</span>
          </Button>
        </div>
      </header>

      <div
        className="lunar-city-viewport relative min-h-0 flex-1 overflow-hidden"
        data-camera="angled-simcity"
        data-testid="lunar-city-viewport"
      >
        <div className="lunar-city-world absolute inset-[-4%]">
          <canvas
            aria-label="Interactive 3D Lunar City"
            className="lunar-city-terrain absolute inset-0 size-full object-cover object-center"
            ref={canvasRef}
          />
          <div aria-hidden="true" className="lunar-city-atmosphere absolute inset-0" />
          <div aria-hidden="true" className="lunar-city-grid absolute inset-0" />
          <div aria-hidden="true" className="lunar-city-route lunar-city-route-one" />
          <div aria-hidden="true" className="lunar-city-route lunar-city-route-two" />
          <div aria-hidden="true" className="lunar-city-route lunar-city-route-three" />

          <div className="pointer-events-none absolute inset-0 z-10">
            {DISTRICTS.map(district => {
              const isSelected = district.id === selected.id

              return (
                <div
                  className={cn('lunar-city-building absolute -translate-x-1/2 -translate-y-1/2', isSelected && 'z-20')}
                  key={district.id}
                  style={{ left: district.position.left, top: district.position.top }}
                >
                  <button
                    aria-label={`Open ${district.name}, led by ${district.leader}`}
                    className={cn(
                      'lunar-city-building-button group pointer-events-auto relative flex size-11 items-center justify-center rounded-full border-2 bg-background/80 shadow-lg backdrop-blur-sm transition duration-200 hover:scale-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-(--ui-accent)',
                      isSelected ? 'border-(--ui-accent) ring-4 ring-(--ui-accent)/20' : 'border-(--ui-stroke-primary)'
                    )}
                    onClick={() => {
                      setSelectedId(district.id)
                      setInside(false)
                    }}
                  >
                    <span className="sr-only">{district.name}</span>
                  </button>
                  <button
                    aria-label={`Inspect ${district.leader}`}
                    className="lunar-city-leader pointer-events-auto absolute -right-14 -top-12"
                    data-character-kind="leader"
                    onClick={() => {
                      setSelectedId(district.id)
                      setInside(true)
                    }}
                    title={`${district.leader}: sentient leader`}
                  >
                    <span className="sr-only">{district.leader}</span>
                  </button>
                  <span aria-hidden="true" className="lunar-city-building-shadow" />
                  <span aria-hidden="true" className="lunar-city-building-signal" />
                </div>
              )
            })}

            {workerSnapshots.map(worker => {
              const workerStyle = { '--worker-delay': `${worker.delay}s` } as CSSProperties

              return (
                <button
                  aria-label={`Inspect ${worker.name} worker`}
                  className={cn(
                    'lunar-city-worker pointer-events-auto',
                    `lunar-city-worker-${worker.routeClass}`,
                    `lunar-city-worker-state-${worker.state}`
                  )}
                  data-state={worker.state}
                  data-worker-design={worker.design}
                  key={worker.id}
                  onClick={() => {
                    setSelectedId(worker.districtId)
                    setInside(true)
                  }}
                  style={workerStyle}
                  title={`${worker.name}: ${worker.task.label} (${worker.task.progress}%)`}
                >
                  <span className="sr-only">{worker.name}</span>
                  <span aria-hidden="true" className="lunar-city-worker-light" />
                </button>
              )
            })}
          </div>
        </div>

        <div className="pointer-events-none absolute inset-0 z-20">
          {selectedEntityKey ? (
            <LunarCityCommandRuntime onOpenSession={onOpenEntitySession} selectedEntityKey={selectedEntityKey} />
          ) : null}

          {operationsReady ? (
            <LunarCityOperations
              getCameraOrder={() => {
                const world = worldHandleRef.current

                return typeof world?.getEntityCameraOrder === 'function' ? world.getEntityCameraOrder() : []
              }}
              onQualityChange={changeQuality}
              onSelect={selectEntity}
              qualityTier={qualityTier}
              reducedMotion={reducedMotion}
              rendererStatus={rendererStatus}
              selectedEntityKey={selectedEntityKey}
            />
          ) : null}

          <div className="pointer-events-auto absolute left-3 top-20 flex items-center gap-1.5 sm:left-5 sm:top-24">
            <CameraControls dispatch={dispatchCamera} focusedEntityLabel={focusedEntityLabel} state={cameraState} />
          </div>

          {profileLeaderEntities.length > 0 ? (
            <section
              aria-label="Profile leaders"
              className="pointer-events-auto absolute left-3 top-34 z-30 flex max-w-[min(20rem,calc(100%-1.5rem))] flex-wrap gap-1.5 sm:left-5 sm:top-40"
            >
              {profileLeaderEntities.map(entity => {
                const owner = leaderOwnerForProfile(entity)!
                const selected = leaderOwnerKey(owner) === selectedLeaderKey

                return (
                  <Button
                    aria-label={`Talk to ${owner.profile} leader`}
                    key={entity.key}
                    onClick={() => openLeader(entity)}
                    size="xs"
                    variant={selected ? 'default' : 'secondary'}
                  >
                    Talk to {owner.profile}
                  </Button>
                )
              })}
            </section>
          ) : null}

          <div className="pointer-events-auto absolute right-3 top-3 flex max-w-[calc(100%-1.5rem)] flex-wrap justify-end gap-1.5 sm:right-5 sm:top-5">
            <div className="lunar-city-counter">
              <Activity aria-hidden="true" className="text-(--ui-green)" size={13} />
              <span>{activeWorkers} active</span>
            </div>
            <div className="lunar-city-counter">
              <SteeringWheel aria-hidden="true" className="text-(--ui-yellow)" size={13} />
              <span>{transitWorkers} in transit</span>
            </div>
            <div className="lunar-city-counter">
              <Lock
                aria-hidden="true"
                className={blockedWorkers ? 'text-(--ui-red)' : 'text-muted-foreground'}
                size={13}
              />
              <span>{blockedWorkers} blocked</span>
            </div>
          </div>

          <div className="lunar-city-inspector pointer-events-auto absolute bottom-3 left-3 max-w-[min(22rem,calc(100%-1.5rem))] rounded-xl border border-(--ui-stroke-tertiary) bg-background/88 p-3 shadow-lg backdrop-blur-md sm:bottom-5 sm:left-5">
            <div className="flex items-center gap-2">
              <div className="flex size-8 items-center justify-center rounded-lg bg-(--ui-accent)/12 text-(--ui-accent)">
                <LeaderIcon aria-hidden="true" size={16} />
              </div>
              <div className="min-w-0">
                <p className="truncate text-xs font-semibold text-foreground">{selected.name}</p>
                <p className="truncate text-[0.68rem] text-muted-foreground">
                  {selected.leader} · {selected.workerCount} child workers
                </p>
              </div>
              <span
                className={cn(
                  'ml-auto shrink-0 text-[0.62rem] font-medium',
                  playing ? 'text-(--ui-green)' : 'text-muted-foreground'
                )}
              >
                {playing ? 'LIVE' : 'PAUSED'}
              </span>
            </div>
            <div className="mt-3 flex items-center gap-2 text-[0.65rem] text-muted-foreground">
              <span className="font-medium text-foreground">{cityProgress}% city progress</span>
              <span aria-hidden="true">·</span>
              <span>{selected.species} leader on duty</span>
            </div>
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-(--ui-stroke-tertiary)">
              <div
                className="h-full rounded-full bg-(--ui-accent) transition-[width] duration-500"
                style={{ width: `${cityProgress}%` }}
              />
            </div>
            <div className="mt-3 flex items-center gap-1.5">
              <Button onClick={() => setInside(true)} size="xs" variant="default">
                Enter building
              </Button>
              <Button onClick={() => setPlaying(value => !value)} size="xs" variant="ghost">
                {playing ? 'Pause' : 'Resume'}
              </Button>
            </div>
          </div>

          <div className="lunar-city-task-queue pointer-events-auto absolute bottom-3 right-3 w-[min(18rem,calc(100%-1.5rem))] rounded-xl border border-(--ui-stroke-tertiary) bg-background/88 p-3 shadow-lg backdrop-blur-md sm:bottom-5 sm:right-5">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Layers3 aria-hidden="true" className="text-(--ui-accent)" size={15} />
                <p className="text-[0.7rem] font-semibold tracking-[0.16em] text-foreground">MISSIONS</p>
              </div>
              <span className="text-[0.62rem] tabular-nums text-muted-foreground">
                {selectedTasks.length} local · {cityProgress}%
              </span>
            </div>
            <div className="mt-2 space-y-2">
              {selectedTasks.map(task => (
                <div key={task.id}>
                  <div className="flex items-center justify-between gap-2 text-[0.62rem]">
                    <span className="truncate font-medium text-foreground">{task.label}</span>
                    <span className={cn('shrink-0 tabular-nums', STATE_TONE[task.state])}>{task.progress}%</span>
                  </div>
                  <div
                    aria-label={task.label}
                    aria-valuemax={100}
                    aria-valuemin={0}
                    aria-valuenow={task.progress}
                    className="mt-1 h-1.5 overflow-hidden rounded-full bg-(--ui-stroke-tertiary)"
                    role="progressbar"
                  >
                    <div
                      className={cn(
                        'h-full rounded-full transition-[width] duration-500',
                        task.state === 'done' ? 'bg-(--ui-green)' : 'bg-(--ui-accent)'
                      )}
                      style={{ width: `${task.progress}%` }}
                    />
                  </div>
                  <div className="mt-1 flex items-center justify-between gap-2 text-[0.58rem] text-muted-foreground">
                    <span className="truncate uppercase tracking-[0.12em]">{task.state}</span>
                    <span className="shrink-0 tabular-nums">ETA {task.eta}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {inside ? (
            <div className="lunar-city-room-panel pointer-events-auto absolute inset-y-3 right-3 z-30 flex w-[min(23rem,calc(100%-1.5rem))] flex-col overflow-hidden rounded-xl border border-(--ui-stroke-tertiary) bg-background/95 shadow-xl backdrop-blur-xl sm:inset-y-5 sm:right-5">
              <div className="flex items-start justify-between gap-3 border-b border-(--ui-stroke-tertiary) px-4 py-3">
                <div>
                  <p className="text-sm font-semibold text-foreground">Inside {selected.name}</p>
                  <p className="mt-0.5 text-[0.68rem] text-muted-foreground">
                    {selected.leader} is managing this shift
                  </p>
                </div>
                <Button aria-label="Leave building" onClick={() => setInside(false)} size="icon-xs" variant="ghost">
                  <X aria-hidden="true" />
                </Button>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
                <div className="rounded-lg bg-(--ui-bg-quaternary) p-3">
                  <div className="flex items-center gap-2">
                    <PawPrint aria-hidden="true" className={selected.accent} size={16} />
                    <p className="text-xs font-medium text-foreground">Leader conversation</p>
                  </div>
                  <p className="mt-2 text-[0.72rem] leading-relaxed text-muted-foreground">
                    “I can see the brood’s current state. Let’s send the next child to the room that has what it needs.”
                  </p>
                </div>
                <div className="mt-4 space-y-2">
                  <div className="flex items-center justify-between">
                    <p className="text-[0.68rem] font-semibold text-foreground">Rooms and activity</p>
                    <span className="text-[0.62rem] tabular-nums text-muted-foreground">
                      {selectedRooms.length} rooms
                    </span>
                  </div>
                  {selectedRooms.map(room => {
                    const Icon =
                      room.state === 'done'
                        ? Check
                        : room.state === 'blocked'
                          ? Lock
                          : room.state === 'review'
                            ? Eye
                            : room.state === 'resource'
                              ? Archive
                              : Activity

                    return (
                      <div
                        className={cn(
                          'flex w-full items-start gap-2 rounded-lg border px-3 py-2',
                          STATE_RING[room.state]
                        )}
                        key={room.label}
                      >
                        <Icon aria-hidden="true" className={cn('mt-0.5 shrink-0', STATE_TONE[room.state])} size={14} />
                        <span className="min-w-0">
                          <span className="block text-[0.7rem] font-medium text-foreground">{room.label}</span>
                          <span className="mt-0.5 block text-[0.65rem] leading-relaxed text-muted-foreground">
                            {room.detail}
                          </span>
                        </span>
                      </div>
                    )
                  })}
                </div>
                <div className="mt-4 border-t border-(--ui-stroke-tertiary) pt-3 text-[0.64rem] leading-relaxed text-muted-foreground">
                  <div className="flex items-center gap-2">
                    <Clock aria-hidden="true" size={13} />
                    <span>Each tick advances tasks and worker routes.</span>
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <RefreshCw aria-hidden="true" size={13} />
                    <span>Close this room to return to the city view.</span>
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {selectedLeaderOwner && leaderSession ? (
            <div className="pointer-events-auto absolute right-3 top-20 z-40 w-[min(27rem,calc(100%-1.5rem))] sm:right-5 sm:top-24">
              <LeaderDialogueRuntime
                clips={
                  selectedLeaderModel ? worldHandleRef.current?.leaderStateClips?.get?.(selectedLeaderModel) : undefined
                }
                key={`${selectedLeaderKey}::${leaderSession.storedId}::${leaderSession.runtimeId}`}
                leaderLabel={`${selectedLeaderOwner.profile} leader`}
                onClose={closeLeaderDialogue}
                onLeaderStateChange={onLeaderStateChange}
                onOpenFullChat={openSelectedLeaderFullChat}
                owner={selectedLeaderOwner}
                session={leaderSession}
                voiceAvailable={voiceAvailable}
              />
            </div>
          ) : null}

          {selectedLeaderOwner && leaderSessionError ? (
            <section
              aria-label={`${selectedLeaderOwner.profile} leader conversation error`}
              className="pointer-events-auto absolute right-3 top-20 z-40 w-[min(27rem,calc(100%-1.5rem))] rounded-xl border border-(--ui-red)/35 bg-background/95 p-4 text-sm shadow-xl backdrop-blur-xl sm:right-5 sm:top-24"
              role="status"
            >
              <p className="font-medium text-foreground">Unable to open {selectedLeaderOwner.profile} leader</p>
              <p className="mt-1 text-xs text-muted-foreground">{leaderSessionError}</p>
              <Button className="mt-3" onClick={closeLeaderDialogue} size="xs" variant="secondary">
                Close
              </Button>
            </section>
          ) : null}
        </div>
      </div>
    </div>
  )
}

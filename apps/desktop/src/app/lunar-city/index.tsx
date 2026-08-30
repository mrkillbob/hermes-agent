import './lunar-city.css'

import { useEffect, useMemo, useState } from 'react'

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

const DISTRICTS: District[] = [
  {
    id: 'library',
    name: 'Library',
    leader: 'Owl Librarian',
    species: 'Owl',
    description: 'A quiet place to retrieve context, compare sources, and speak with the librarian.',
    accent: 'text-(--ui-purple)',
    icon: 'archive',
    position: { left: '17%', top: '18%' },
    rooms: [
      { label: 'Consultation desk', state: 'working', detail: 'Librarian is speaking with a child worker.' },
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
    position: { left: '61%', top: '18%' },
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
    position: { left: '31%', top: '36%' },
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
    position: { left: '76%', top: '38%' },
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
    position: { left: '50%', top: '43%' },
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
    position: { left: '68%', top: '52%' },
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
    position: { left: '20%', top: '62%' },
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
    position: { left: '44%', top: '69%' },
    rooms: [
      { label: 'Receiving hall', state: 'working', detail: 'A courier is cataloging a new source package.' },
      { label: 'Long-term stacks', state: 'resource', detail: 'Archive shelves are waiting for a storage slot.' },
      { label: 'Lineage garden', state: 'done', detail: 'A completed memory seed has been planted.' }
    ],
    workerCount: 8
  }
]

const WORKER_STATES: Array<{ id: WorkerState; label: string; icon: typeof Activity }> = [
  { id: 'working', label: 'Working', icon: Activity },
  { id: 'ready', label: 'Ready', icon: SteeringWheel },
  { id: 'triage', label: 'Triage', icon: Layers3 },
  { id: 'review', label: 'Review', icon: Eye },
  { id: 'resource', label: 'Resources', icon: Archive },
  { id: 'heartbeat', label: 'Heartbeat', icon: RefreshCw },
  { id: 'break', label: 'Break', icon: Moon },
  { id: 'blocked', label: 'Blocked', icon: Lock },
  { id: 'done', label: 'Done', icon: Check }
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

function stateForTick(room: Room, tick: number): WorkerState {
  const states: WorkerState[] = ['working', 'ready', 'review', 'done', 'heartbeat', 'resource', 'break', 'triage', 'blocked']
  const index = Math.max(0, states.indexOf(room.state))

  return states[(index + tick) % states.length] ?? room.state
}

export function LunarCity({ onOpenMemoryGraph }: { onOpenMemoryGraph: () => void }) {
  const [selectedId, setSelectedId] = useState('research')
  const [inside, setInside] = useState(false)
  const [playing, setPlaying] = useState(true)
  const [tick, setTick] = useState(0)
  const [activeState, setActiveState] = useState<WorkerState | null>(null)

  useEffect(() => {
    if (!playing) {
      return
    }

    const timer = window.setInterval(() => setTick(value => (value + 1) % 9), 4800)

    return () => window.clearInterval(timer)
  }, [playing])

  const selected = useMemo(
    () => DISTRICTS.find(district => district.id === selectedId) ?? DISTRICTS[0]!,
    [selectedId]
  )

  const LeaderIcon = districtIcon(selected.icon)

  const selectedRooms = selected.rooms.map(room => ({
    ...room,
    state: tick === 0 ? room.state : stateForTick(room, tick)
  }))

  const displayedWorkers = activeState
    ? selectedRooms.filter(room => room.state === activeState).length
    : selected.workerCount

  return (
    <div className="lunar-city relative flex min-h-0 flex-1 flex-col overflow-hidden bg-(--ui-bg-editor)">
      <header className="relative z-20 flex shrink-0 items-center justify-between gap-4 border-b border-(--ui-stroke-tertiary) bg-(--ui-bg-editor)/95 px-4 py-3 backdrop-blur-md sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-(--ui-accent)/12 text-(--ui-accent)">
            <Moon aria-hidden="true" size={18} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-sm font-semibold text-foreground">Lunar City</h2>
              <span className="rounded-full bg-(--ui-green)/12 px-2 py-0.5 text-[0.62rem] font-medium text-(--ui-green)">CITY SIMULATION</span>
            </div>
            <p className="truncate text-xs text-muted-foreground">A living overview of Hermes groups, leaders, rooms, and worker routes</p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button aria-label={playing ? 'Pause city simulation' : 'Play city simulation'} onClick={() => setPlaying(value => !value)} size="icon-sm" variant="ghost">
            {playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
          </Button>
          <Button aria-label="Open memory graph" onClick={onOpenMemoryGraph} size="sm" variant="secondary">
            <Network aria-hidden="true" />
            <span className="hidden sm:inline">Memory graph</span>
          </Button>
        </div>
      </header>

      <div className="relative min-h-0 flex-1 overflow-hidden">
        <img
          alt="Isometric lunar settlement with Hermes buildings, leaders, and helper workers"
          className="absolute inset-0 size-full object-cover object-center"
          src="./lunar-city/moon-settlement.png"
        />
        <div aria-hidden="true" className="absolute inset-0 bg-background/8" />

        <div className="pointer-events-none absolute inset-0 z-10">
          {DISTRICTS.map(district => {
            const Icon = districtIcon(district.icon)
            const isSelected = district.id === selected.id

            return (
              <div
                className={cn('pointer-events-auto absolute -translate-x-1/2 -translate-y-1/2', isSelected && 'z-20')}
                key={district.id}
                style={{ left: district.position.left, top: district.position.top }}
              >
                <button
                  aria-label={`Open ${district.name}, led by ${district.leader}`}
                  className={cn(
                    'group relative flex size-10 items-center justify-center rounded-full border-2 bg-background/80 shadow-lg backdrop-blur-sm transition duration-200 hover:scale-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-(--ui-accent)',
                    isSelected ? 'border-(--ui-accent) ring-4 ring-(--ui-accent)/20' : 'border-(--ui-stroke-primary)'
                  )}
                  onClick={() => {
                    setSelectedId(district.id)
                    setInside(false)
                    setActiveState(null)
                  }}
                >
                  <Icon aria-hidden="true" className={district.accent} size={17} />
                  <span className="absolute -bottom-6 left-1/2 hidden -translate-x-1/2 whitespace-nowrap rounded bg-background/90 px-1.5 py-0.5 text-[0.6rem] font-medium text-foreground shadow group-hover:block">
                    {district.name}
                  </span>
                </button>
                <div className="pointer-events-none absolute -right-3 -top-2 flex items-center gap-0.5">
                  {district.rooms.slice(0, 3).map((room, index) => {
                    const state = tick === 0 ? room.state : stateForTick(room, tick + index)

                    return <span aria-hidden="true" className={cn('size-1.5 rounded-full shadow-sm', STATE_TONE[state].replace('text-', 'bg-'))} key={`${district.id}-${room.label}`} />
                  })}
                </div>
              </div>
            )
          })}

          <div className="lunar-city-route lunar-city-route-one" />
          <div className="lunar-city-route lunar-city-route-two" />
          <div className="lunar-city-worker lunar-city-worker-one">
            <Activity aria-hidden="true" className="text-(--ui-green)" size={14} />
          </div>
          <div className="lunar-city-worker lunar-city-worker-two">
            <SteeringWheel aria-hidden="true" className="text-(--ui-yellow)" size={13} />
          </div>
          <div className="lunar-city-worker lunar-city-worker-three">
            <Check aria-hidden="true" className="text-(--ui-green)" size={13} />
          </div>
        </div>

        <div className="absolute bottom-3 left-3 z-20 max-w-[min(24rem,calc(100%-1.5rem))] rounded-xl border border-(--ui-stroke-tertiary) bg-background/90 p-3 shadow-lg backdrop-blur-md sm:bottom-5 sm:left-5">
          <div className="flex items-center gap-2">
            <div className="flex size-8 items-center justify-center rounded-lg bg-(--ui-accent)/12 text-(--ui-accent)">
              <LeaderIcon aria-hidden="true" size={16} />
            </div>
            <div className="min-w-0">
              <p className="truncate text-xs font-semibold text-foreground">{selected.name}</p>
              <p className="truncate text-[0.68rem] text-muted-foreground">Managed by {selected.leader}</p>
            </div>
            <span className={cn('ml-auto shrink-0 text-[0.62rem] font-medium', playing ? 'text-(--ui-green)' : 'text-muted-foreground')}>
              {playing ? 'moving' : 'paused'}
            </span>
          </div>
          <p className="mt-2 text-[0.7rem] leading-relaxed text-muted-foreground">{selected.description}</p>
          <div className="mt-3 flex items-center gap-1.5">
            <Button onClick={() => setInside(true)} size="xs" variant="default">
              Enter building
            </Button>
            <Button onClick={() => setPlaying(value => !value)} size="xs" variant="ghost">
              {playing ? 'Pause routes' : 'Resume routes'}
            </Button>
          </div>
        </div>

        <div className="absolute right-3 top-3 z-20 flex max-w-[calc(100%-1.5rem)] flex-wrap justify-end gap-1.5 sm:right-5 sm:top-5">
          <div className="flex items-center gap-1.5 rounded-full border border-(--ui-stroke-tertiary) bg-background/90 px-2.5 py-1.5 text-[0.65rem] text-muted-foreground shadow backdrop-blur-md">
            <Users aria-hidden="true" className="text-(--ui-accent)" size={13} />
            <span>{DISTRICTS.length} groups</span>
          </div>
          <div className="flex items-center gap-1.5 rounded-full border border-(--ui-stroke-tertiary) bg-background/90 px-2.5 py-1.5 text-[0.65rem] text-muted-foreground shadow backdrop-blur-md">
            <Activity aria-hidden="true" className="text-(--ui-green)" size={13} />
            <span>{selected.workerCount} workers in motion</span>
          </div>
        </div>

        <div className="absolute bottom-3 right-3 z-20 hidden w-48 rounded-xl border border-(--ui-stroke-tertiary) bg-background/90 p-3 shadow-lg backdrop-blur-md lg:block sm:bottom-5 sm:right-5">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-[0.68rem] font-semibold text-foreground">Worker states</p>
            {activeState ? (
              <button className="text-[0.62rem] text-(--ui-accent) hover:underline" onClick={() => setActiveState(null)}>
                clear
              </button>
            ) : null}
          </div>
          <div className="grid grid-cols-3 gap-1">
            {WORKER_STATES.map(({ id, icon: Icon, label }) => (
              <button
                aria-pressed={activeState === id}
                className={cn(
                  'flex min-w-0 flex-col items-center gap-1 rounded-md px-1 py-1.5 text-[0.56rem] text-muted-foreground transition hover:bg-(--ui-row-hover-background) hover:text-foreground',
                  activeState === id && 'bg-(--ui-row-active-background) text-foreground'
                )}
                key={id}
                onClick={() => setActiveState(value => (value === id ? null : id))}
              >
                <Icon aria-hidden="true" className={STATE_TONE[id]} size={14} />
                <span className="truncate">{label}</span>
              </button>
            ))}
          </div>
        </div>

        {inside ? (
          <div className="absolute inset-y-3 right-3 z-30 flex w-[min(23rem,calc(100%-1.5rem))] flex-col overflow-hidden rounded-xl border border-(--ui-stroke-tertiary) bg-background/95 shadow-xl backdrop-blur-xl sm:inset-y-5 sm:right-5">
            <div className="flex items-start justify-between gap-3 border-b border-(--ui-stroke-tertiary) px-4 py-3">
              <div>
                <p className="text-sm font-semibold text-foreground">Inside {selected.name}</p>
                <p className="mt-0.5 text-[0.68rem] text-muted-foreground">{selected.leader} is managing this shift</p>
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
                    {activeState ? `${displayedWorkers} matching rooms` : `${selected.workerCount} workers`}
                  </span>
                </div>
                {selectedRooms.map(room => {
                  const Icon = WORKER_STATES.find(item => item.id === room.state)?.icon ?? Activity

                  return (
                    <button
                      className={cn('flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left transition hover:bg-(--ui-row-hover-background)', STATE_RING[room.state])}
                      key={room.label}
                      onClick={() => setActiveState(room.state)}
                    >
                      <Icon aria-hidden="true" className={cn('mt-0.5 shrink-0', STATE_TONE[room.state])} size={14} />
                      <span className="min-w-0">
                        <span className="block text-[0.7rem] font-medium text-foreground">{room.label}</span>
                        <span className="mt-0.5 block text-[0.65rem] leading-relaxed text-muted-foreground">{room.detail}</span>
                      </span>
                    </button>
                  )
                })}
              </div>
              <div className="mt-4 border-t border-(--ui-stroke-tertiary) pt-3 text-[0.64rem] leading-relaxed text-muted-foreground">
                <div className="flex items-center gap-2">
                  <Clock aria-hidden="true" size={13} />
                  <span>Routes update every 4.8s while the city is playing.</span>
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <Network aria-hidden="true" size={13} />
                  <span>Handoffs are shown as paths between group buildings.</span>
                </div>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}

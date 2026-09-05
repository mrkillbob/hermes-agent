import { type CityStatus, cityStatusLabel, resolveCityStatus } from '../city-status'
import { projectCompoundKey } from '../identity'
import type { EntityKey, LunarCitySnapshot, LunarEntity } from '../model'

export interface ProjectZoneSummary {
  activeStatuses: readonly CityStatus[]
  entityKeys: readonly EntityKey[]
  key: string
  projectId: string
  unplaced: number
}

export function projectZoneSummaries(snapshot: LunarCitySnapshot): readonly ProjectZoneSummary[] {
  const zones = new Map<
    string,
    { activeStatuses: Set<CityStatus>; entityKeys: EntityKey[]; projectId: string; unplaced: number }
  >()

  for (const entity of snapshot.entities.values()) {
    if (entity.identity.kind !== 'kanban' || !entity.projectId?.trim()) {continue}
    const projectId = entity.projectId.trim()
    const key = projectCompoundKey(entity.identity.connectionId, projectId)
    const zone = zones.get(key) ?? { activeStatuses: new Set(), entityKeys: [], projectId, unplaced: 0 }
    zone.entityKeys.push(entity.key)

    if (!entity.position) {zone.unplaced += 1}
    const status = resolveCityStatus(entity, snapshot.observedAt || Date.now())

    if (status.active) {zone.activeStatuses.add(status.status)}
    zones.set(key, zone)
  }

  return [...zones]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, zone]) => ({
      activeStatuses: [...zone.activeStatuses].sort(),
      entityKeys: zone.entityKeys,
      key,
      projectId: zone.projectId,
      unplaced: zone.unplaced
    }))
}

export function ProjectZonePanel({
  onSelect,
  snapshot
}: {
  onSelect(entity: LunarEntity): void
  snapshot: LunarCitySnapshot
}) {
  const zones = projectZoneSummaries(snapshot)

  return (
    <section aria-label="Project zones" className="lunar-city-project-zone-panel">
      <h2>Project zones</h2>
      {zones.length === 0 ? (
        <p>No project compounds observed.</p>
      ) : (
        <ul>
          {zones.map(zone => {
            const entity = zone.entityKeys.map(key => snapshot.entities.get(key)).find(Boolean)

            const statuses =
              zone.activeStatuses.length > 0 ? zone.activeStatuses.map(cityStatusLabel).join(', ') : 'Idle'

            return (
              <li key={zone.key}>
                <button disabled={!entity} onClick={() => entity && onSelect(entity)} type="button">
                  {zone.projectId}
                </button>
                <span>
                  {statuses}; {zone.entityKeys.length} entities{zone.unplaced > 0 ? `; ${zone.unplaced} unplaced` : ''}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

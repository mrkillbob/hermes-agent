import { describe, expect, it } from 'vitest'

import { projectCompoundKey } from '../identity'
import type { EntityKey, LunarCitySnapshot, LunarEntity } from '../model'

import { projectZoneSummaries } from './project-zone-panel'

const entity = (key: string, projectId: string): LunarEntity => ({
  authority: 'authoritative',
  animation: 'work',
  destination: 'project',
  identity: { kind: 'kanban', connectionId: 'local', profile: 'default', board: 'main', taskId: key },
  key: key as EntityKey,
  observedAt: 100,
  projectId,
  position: { x: 1, y: 0, z: 1 }
})

describe('projectZoneSummaries', () => {
  it('groups exact project identities and reports active/unplaced work', () => {
    const snapshot = {
      observedAt: 100,
      entities: new Map([
        [entity('task-a', 'project-a').key, entity('task-a', 'project-a')],
        [entity('task-b', 'project-a').key, { ...entity('task-b', 'project-a'), position: undefined }]
      ]),
      revision: 1,
      sources: []
    } as unknown as LunarCitySnapshot

    expect(projectZoneSummaries(snapshot)).toEqual([
      {
        activeStatuses: ['working'],
        entityKeys: ['task-a', 'task-b'],
        key: projectCompoundKey('local', 'project-a'),
        projectId: 'project-a',
        unplaced: 1
      }
    ])
  })
})

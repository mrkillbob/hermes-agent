import { lazy } from 'react'
import { useNavigate } from 'react-router'

import { ROUTES_AREA, SIDEBAR_NAV_AREA, STARMAP_ROUTE } from '@/app/routes'
import { registry } from '@/contrib/registry'
import type { Contribution } from '@/contrib/types'

const LazyLunarCity = lazy(async () => ({ default: (await import('./index')).LunarCity }))

export const LUNAR_CITY_ROUTE = '/lunar-city'

// The bundled Kanban plugin owns order 50. Lunar City is its immediate
// successor among contributed navigation rows while remaining independent of
// whether Kanban is enabled for a particular profile.
export const LUNAR_CITY_NAV_ORDER = 60

export function LunarCityRoute() {
  const navigate = useNavigate()

  return <LazyLunarCity onOpenMemoryGraph={() => navigate(STARMAP_ROUTE)} />
}

export const LUNAR_CITY_CONTRIBUTIONS: Contribution[] = [
  {
    id: 'lunar-city:page',
    area: ROUTES_AREA,
    data: { path: LUNAR_CITY_ROUTE },
    render: () => <LunarCityRoute />
  },
  {
    id: 'lunar-city:nav',
    area: SIDEBAR_NAV_AREA,
    order: LUNAR_CITY_NAV_ORDER,
    data: { codicon: 'globe', label: 'Lunar City', path: LUNAR_CITY_ROUTE }
  }
]

export function registerLunarCityContributions(): () => void {
  return registry.registerMany(LUNAR_CITY_CONTRIBUTIONS)
}

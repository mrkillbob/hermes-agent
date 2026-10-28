import {
  activeGateway,
  activeGatewayConnectionId,
  activeGatewayProfileKey,
  gatewayActivationEpoch
} from '@/store/gateway'

import { type AttachedRoute, type InventoryOwner, readAttachedSessionInventory } from './attached-session-read'

/** Only uses synchronous getters. Deliberately excludes getConnection/ensure/open/requestForAgent helpers. */
export function currentlyAttachedSessionRoute(): AttachedRoute | null {
  const gateway = activeGateway()
  const connectionId = activeGatewayConnectionId()
  const profile = activeGatewayProfileKey()

  if (!gateway || gateway.connectionState !== 'open' || !connectionId || !profile) {
    return null
  }

  return { gateway, connectionId, profile, epoch: gatewayActivationEpoch() }
}

/** Only an explicit refresh calls this reader; mounting never reads, ensures or opens a connection. */
export function readCurrentAttachedSessions(owner: InventoryOwner, signal?: AbortSignal) {
  return readAttachedSessionInventory(owner, currentlyAttachedSessionRoute, signal)
}

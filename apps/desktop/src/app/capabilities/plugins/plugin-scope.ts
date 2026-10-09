import type { ProfileScope } from '@/hermes'
import { activeGatewayConnectionId } from '@/store/gateway'

/** True when the Capabilities scope pins a connection other than the active one. Settings plugin
 *  pages only speak to the active gateway, so a hand-off from such a scope would edit (or miss) a
 *  same-named profile on the wrong backend; the settings gear is withheld instead. */
export function scopeIsForeignConnection(scope: ProfileScope): boolean {
  if (!scope || typeof scope !== 'object') {
    return false
  }

  const connectionId = (scope.connectionId ?? '').trim()

  return Boolean(connectionId) && connectionId !== (activeGatewayConnectionId() ?? 'local')
}

import { execFileSync } from 'node:child_process'
import { unlinkSync } from 'node:fs'
import fs from 'node:fs/promises'
import net from 'node:net'
import path from 'node:path'

import { afterEach, expect, test, vi } from 'vitest'

import { gatewayWsUrlIpcResult } from './connection-config'
import { isStaleLocalGatewayError, mintLocalGatewayTicket } from './local-gateway'
import { shortSocketTmpDir } from './local-gateway.test-helpers'

const homes: string[] = []
const servers: net.Server[] = []
const sockets = new Set<net.Socket>()
const endpoint = (profile_id: string) => ({ profile_id, instance_id: 'fixture-owner', authority_epoch: 1, runtime_protocol: 1, api_origin: 'http://127.0.0.1:1234', capabilities: ['session-authority-v1'], supervisor: 'none' })

async function fixture(onConnect = (_socket: net.Socket) => {}) {
  const home = await shortSocketTmpDir('ticket-diag-')
  homes.push(home)

  const server = net.createServer(socket => {
    sockets.add(socket)
    socket.on('close', () => sockets.delete(socket))
    onConnect(socket)
  })

  servers.push(server)
  await new Promise<void>(resolve => server.listen(path.join(home, 'gateway.sock'), resolve))
  await fs.chmod(path.join(home, 'gateway.sock'), 0o600)

  return home
}

afterEach(async () => {
  vi.restoreAllMocks()

  for (const socket of sockets) {socket.destroy()}
  await Promise.all(servers.splice(0).map(server => new Promise<void>(resolve => server.close(() => resolve()))))
  await Promise.all(homes.splice(0).map(home => fs.rm(home, { recursive: true, force: true })))
})

function diagnostic(error: any, reason: string) {
  expect(error).toBeInstanceOf(Error)
  expect(error.message).toBe('Gateway ticket bootstrap failed')
  expect(error.reason).toBe(reason)
  expect(error.stack).toContain(`cause=${reason}`)
  expect(error.stack).toMatch(/elapsed_ms=\d{1,5} connected=(?:true|false) received_data=(?:true|false)/)
  expect(isStaleLocalGatewayError(error)).toBe(true)
}

test.skipIf(process.platform === 'win32')('a real socket deadline remains five seconds and is distinguished from native errors', async () => {
  const home = await fixture()
  const before = performance.now()
  const error = await mintLocalGatewayTicket(endpoint(home)).catch(error => error)

  diagnostic(error, 'deadline')
  expect(performance.now() - before).toBeGreaterThanOrEqual(4900)
  expect(error.stack).toContain('connected=true received_data=false')
})

test.skipIf(process.platform === 'win32')('a native refused socket is distinguished without exposing its path', async () => {
  const home = await shortSocketTmpDir('ticket-refused-')
  homes.push(home)
  const socketPath = path.join(home, 'gateway.sock')
  // A closed AF_UNIX socket keeps its filesystem node; no application/runtime is launched.
  execFileSync(process.env.HERMES_PYTHON || 'python3', ['-c', 'import socket,sys; s=socket.socket(socket.AF_UNIX); s.bind(sys.argv[1]); s.close()', socketPath])
  await fs.chmod(socketPath, 0o600)
  const error = await mintLocalGatewayTicket(endpoint(home)).catch(error => error)

  diagnostic(error, 'ECONNREFUSED')
  expect(error.stack).toContain('connected=false received_data=false')
  expect(error.stack).not.toContain(home)
})

test.skipIf(process.platform === 'win32')('oversized native response stays bounded and receives its own fixed cause', async () => {
  const home = await fixture(socket => socket.once('data', () => socket.write('x'.repeat(65537))))
  const error = await mintLocalGatewayTicket(endpoint(home)).catch(error => error)

  diagnostic(error, 'response_too_large')
  expect(error.stack).toContain('connected=true received_data=true')
  expect(error.stack).not.toContain('x'.repeat(64))
})

test.skipIf(process.platform === 'win32')('a socket disappearing after path validation retains the native missing code', async () => {
  const home = await fixture()
  const original = net.createConnection
  vi.spyOn(net, 'createConnection').mockImplementation(((...args: any[]) => {
    // Fault at the native boundary, after production's actual path/ACL validation.
    unlinkSync(path.join(home, 'gateway.sock'))

    return original.apply(net, args)
  }) as typeof net.createConnection)
  const error = await mintLocalGatewayTicket(endpoint(home)).catch(error => error)

  diagnostic(error, 'ENOENT')
  expect(error.stack).not.toContain(home)
})

test.skipIf(process.platform === 'win32').each(['ECONNRESET', 'UNTRUSTED /private/path?ticket=secret', undefined])(
  'native error %s records only an allowlisted cause, never arbitrary diagnostic input', async code => {
    const home = await fixture()
    const socket = new net.Socket()
    let acknowledge!: () => void
    const created = new Promise<void>(resolve => {acknowledge = resolve})
    vi.spyOn(net, 'createConnection').mockImplementation((() => {
      acknowledge()

      return socket
    }) as typeof net.createConnection)
    const pending = mintLocalGatewayTicket(endpoint(home)).catch(error => error)
    await created
    // Some native transport failures are not portable to induce on AF_UNIX (reset vs EOF).
    // Inject only that OS event; the production listener/classifier remains real.
    socket.emit('error', Object.assign(new Error('secret raw error /private/path https://example.invalid/?ticket=secret'), { code, errno: 'secret', syscall: 'private syscall' }))
    socket.emit('close')
    const error = await pending

    diagnostic(error, code === 'ECONNRESET' ? 'ECONNRESET' : 'socket_error')

    for (const secret of ['secret', '/private/path', 'example.invalid', 'private syscall', 'UNTRUSTED']) {
      expect(error.message).not.toContain(secret)
      expect(error.stack).not.toContain(secret)
      expect(JSON.stringify(error)).not.toContain(secret)
    }
  }
)


test.skipIf(process.platform === 'win32')('WS IPC reports only the bounded diagnostic from an actual native ticket failure', async () => {
  const home = await fixture(socket => socket.write('x'.repeat(65537)))
  const reported: string[] = []
  const result = await gatewayWsUrlIpcResult(() => mintLocalGatewayTicket(endpoint(home)), line => reported.push(line))

  expect(result).toEqual({ ok: false, error: 'Gateway ticket bootstrap failed' })
  expect(reported).toHaveLength(1)
  expect(reported[0]).toMatch(/^\[gateway-ticket cause=response_too_large elapsed_ms=\d{1,5} connected=true received_data=true\]$/)
})

test('WS IPC diagnostic reporting refuses unbounded or untrusted error content', async () => {
  const safe = '[gateway-ticket cause=ECONNRESET elapsed_ms=12 connected=true received_data=false]'
  const reported: string[] = []

  for (const [message, reason, line] of [
    ['secret ticket=private', 'ECONNRESET', safe],
    ['Gateway ticket bootstrap failed', 'secret', safe],
    ['Gateway ticket bootstrap failed', 'ECONNRESET', safe + ' ticket=private'],
    ['Gateway ticket bootstrap failed', 'ECONNRESET', safe.replace('12', '60001')],
    ['Gateway ticket bootstrap failed', 'ECONNRESET', safe.replace('ECONNRESET', 'secret')],
    ['Gateway ticket bootstrap failed', undefined, safe]
  ]) {
    const error = Object.assign(new Error(message), { reason })

    error.stack = `private path /secret?ticket=private\n${line}`
    await gatewayWsUrlIpcResult(() => Promise.reject(error), line => reported.push(line))
  }

  expect(reported).toEqual([])

  const error = Object.assign(new Error('Gateway ticket bootstrap failed'), { reason: 'ECONNRESET' })

  error.stack = `private path /secret?ticket=private\n${safe}`
  expect(await gatewayWsUrlIpcResult(() => Promise.reject(error), line => reported.push(line))).toEqual({ ok: false, error: error.message })
  expect(reported).toEqual([safe])
  expect(await gatewayWsUrlIpcResult(() => Promise.reject(error), () => { throw new Error('logger failed') })).toEqual({ ok: false, error: error.message })
})

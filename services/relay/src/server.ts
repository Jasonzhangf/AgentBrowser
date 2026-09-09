import {createServer, type ServerOptions} from 'node:https';
import type {IncomingMessage, ServerResponse} from 'node:http';
import {randomBytes, randomUUID, verify} from 'node:crypto';
import {WebSocket, WebSocketServer} from 'ws';
import {RELAY_ABI_ID, RelayError, authTranscript, envelope, hostRejectedTunnelReason, object, snapshot, text, tunnelRejectReason, type HostSnapshot, type Channel, type TunnelOffer} from '../../../protocol/relay/index.js';
import {RelayStore, digest, type Account, type Device} from './store.js';

interface Peer {ws: WebSocket; account: Account; device: Device; token: string; hostId?: string; snapshot?: HostSnapshot; publishedAt?: number; expired?: boolean}
interface Ticket {tunnel: Tunnel; side: 0 | 1; channel: Channel; expiresAt: number}
interface Tunnel {id: string; hostId: string; sessionId: string; peers: [Peer, Peer]; sockets: Record<Channel, [WebSocket?, WebSocket?]>; expiresAt: number; tickets: Set<string>; phase: 'pending' | 'active'}

export interface RelayOptions {
  store: RelayStore;
  tls: Pick<ServerOptions, 'key' | 'cert'>;
  directoryTtlMs?: number;
  ticketTtlMs?: number;
  sweepMs?: number;
}

export function createRelayServer(options: RelayOptions) {
  const {store} = options;
  if (!options.tls.key || !options.tls.cert) throw new Error('TLS key and certificate required');
  const directoryTtl = options.directoryTtlMs ?? 30_000;
  const ticketTtl = options.ticketTtlMs ?? 10_000;
  const peers = new Set<Peer>();
  const hosts = new Map<string, Peer>();
  const tickets = new Map<string, Ticket>();
  const tunnels = new Map<string, Tunnel>();
  let inflight = 0;
  let loginWindow = store.now();
  let loginAttempts = 0;
  const accountAttempts = new Map<string, number>();
  const wss = new WebSocketServer({noServer: true, maxPayload: 1024 * 1024, perMessageDeflate: false});
  const errorBody = (error: unknown) => error instanceof RelayError
    ? {code: error.code, message: error.message} : {code: 'INTERNAL_ERROR', message: 'Relay request failed'};

  function send(ws: WebSocket, message: unknown) {
    if (ws.readyState !== WebSocket.OPEN) return;
    if (ws.bufferedAmount > 256 * 1024) { ws.close(1013, 'Control backpressure'); return; }
    ws.send(JSON.stringify(message));
  }
  function directory(accountId: string) {
    return [...hosts.values()].filter(peer => peer.account.id === accountId && peer.snapshot && authorized(peer) && store.now() - peer.publishedAt! < directoryTtl)
      .map(peer => ({hostId: peer.hostId, deviceId: peer.device.id, deviceName: peer.device.name, snapshot: peer.snapshot}));
  }
  function publishDirectory(accountId: string) {
    const message = {type: 'directory.snapshot', abi: RELAY_ABI_ID, hosts: directory(accountId)};
    for (const peer of peers) if (peer.account.id === accountId) send(peer.ws, message);
  }
  function closeTunnel(tunnel: Tunnel, reason: string) {
    if (!tunnels.delete(tunnel.id)) return;
    for (const key of tunnel.tickets) tickets.delete(key);
    for (const pair of Object.values(tunnel.sockets)) for (const ws of pair) ws?.close(1000, reason);
    for (const peer of tunnel.peers) send(peer.ws, {type: 'tunnel.closed', abi: RELAY_ABI_ID, tunnelId: tunnel.id, reason});
  }
  function validPeer(peer: Peer) {
    if (!peers.has(peer) || peer.ws.readyState !== WebSocket.OPEN || store.authenticate(peer.token).id !== peer.account.id) throw new RelayError('UNAUTHORIZED', 'Peer authorization expired', 401);
    return true;
  }
  function authorized(peer: Peer) {
    try { return validPeer(peer); } catch { return false; }
  }
  function offerTunnel(client: Peer, host: Peer, sessionId: string) {
    if (tunnels.size >= 128 || [...tunnels.values()].filter(item => item.peers.includes(client)).length >= 8) {
      throw new RelayError('TUNNEL_LIMIT', 'Tunnel limit reached', 429);
    }
    if (!host.snapshot || host.expired || store.now() - host.publishedAt! >= directoryTtl || !host.snapshot.sessions.some(session => session.id === sessionId)) {
      throw new RelayError('SESSION_UNAVAILABLE', 'Host session is not published', 404);
    }
    const tunnel: Tunnel = {id: randomUUID(), hostId: host.hostId!, sessionId, peers: [client, host], sockets: {control: [], media: []}, expiresAt: store.now() + ticketTtl, tickets: new Set(), phase: 'pending'};
    tunnels.set(tunnel.id, tunnel);
    for (const side of [0, 1] as const) {
      const channels = {} as TunnelOffer['channels'];
      for (const channel of ['control', 'media'] as const) {
        const ticket = randomBytes(32).toString('base64url');
        const key = digest(ticket);
        tunnel.tickets.add(key);
        tickets.set(key, {tunnel, side, channel, expiresAt: tunnel.expiresAt});
        channels[channel] = {path: `/v2/tunnel/${tunnel.id}/${channel}/${side}`, ticket};
      }
      send(tunnel.peers[side].ws, {type: 'tunnel.offer', abi: RELAY_ABI_ID, tunnelId: tunnel.id, hostId: tunnel.hostId, sessionId: tunnel.sessionId, peerDeviceId: tunnel.peers[side === 0 ? 1 : 0].device.id, side, expiresAt: tunnel.expiresAt, channels} satisfies TunnelOffer);
    }
  }

  async function body(req: IncomingMessage) {
    const chunks: Buffer[] = []; let size = 0;
    for await (const chunk of req) {
      size += chunk.length;
      if (size > 16 * 1024) throw new RelayError('BODY_TOO_LARGE', 'Request too large', 413);
      chunks.push(chunk);
    }
    try { return JSON.parse(Buffer.concat(chunks).toString()); }
    catch { throw new RelayError('INVALID_JSON', 'Invalid JSON'); }
  }
  function bearer(req: IncomingMessage) {
    const header = req.headers.authorization;
    if (!header?.startsWith('Bearer ')) throw new RelayError('UNAUTHORIZED', 'Bearer required', 401);
    return text(header.slice(7), 'token', 256);
  }
  function reply(res: ServerResponse, status: number, data: unknown) {
    res.writeHead(status, {'content-type': 'application/json', 'cache-control': 'no-store'});
    res.end(JSON.stringify(data));
  }
  const server = createServer({...options.tls, minVersion: 'TLSv1.2', requestTimeout: 10_000, headersTimeout: 10_000}, async (req, res) => {
    if (inflight >= 16) { reply(res, 429, {error: {code: 'BUSY', message: 'Too many requests'}}); return; }
    inflight++;
    try {
      if (req.url === '/health' && req.method === 'GET') { reply(res, 200, {ok: true}); return; }
      if (req.url === '/v2/login' && req.method === 'POST') {
        const data = object(await body(req), ['username', 'password']);
        const username = text(data.username, 'username', 64);
        if (store.now() - loginWindow >= 60_000) { loginWindow = store.now(); loginAttempts = 0; accountAttempts.clear(); }
        const attempts = accountAttempts.get(username) ?? 0;
        if (loginAttempts >= 160 || attempts >= 20) throw new RelayError('RATE_LIMIT', 'Login attempt limit reached', 429);
        loginAttempts++; accountAttempts.set(username, attempts + 1);
        reply(res, 200, await store.login(username, text(data.password, 'password', 1024))); return;
      }
      const token = bearer(req); const account = store.authenticate(token);
      if (req.url === '/v2/devices' && req.method === 'POST') {
        const data = object(await body(req), ['name', 'publicKey']);
        const device = store.addDevice(account, text(data.name, 'name', 64), text(data.publicKey, 'key', 1024));
        reply(res, 201, {id: device.id}); return;
      }
      if (req.url === '/v2/hosts' && req.method === 'POST') {
        const data = object(await body(req), ['deviceId']);
        reply(res, 201, {id: store.addHost(account, text(data.deviceId, 'deviceId')).id}); return;
      }
      if (req.url === '/v2/directory' && req.method === 'GET') { reply(res, 200, {hosts: directory(account.id)}); return; }
      if (req.url === '/v2/token' && req.method === 'DELETE') { store.revoke(token); reply(res, 200, {revoked: true}); return; }
      throw new RelayError('NOT_FOUND', 'Endpoint not found', 404);
    } catch (error) { reply(res, error instanceof RelayError ? error.status : 500, {error: errorBody(error)}); }
    finally { inflight--; }
  });

  server.on('upgrade', (req, socket, head) => {
    try {
      if (wss.clients.size >= 128) throw new RelayError('BUSY', 'Connection limit', 429);
      const path = req.url ?? '';
      if (path.startsWith('/v2/tunnel/')) {
        const key = digest(bearer(req)); const ticket = tickets.get(key);
        if (!ticket || ticket.expiresAt <= store.now() || path !== `/v2/tunnel/${ticket.tunnel.id}/${ticket.channel}/${ticket.side}` || !ticket.tunnel.peers.every(validPeer)) {
          throw new RelayError('UNAUTHORIZED', 'Invalid tunnel ticket', 401);
        }
        const tunnel = ticket.tunnel;
        tickets.delete(key); tunnel.tickets.delete(key);
        // Consuming any channel ticket starts acceptance; reject_offer is pending-only.
        tunnel.phase = 'active';
        wss.handleUpgrade(req, socket, head, ws => {
          if (tunnels.get(tunnel.id) !== tunnel) {
            ws.close(1000, 'TUNNEL_CLOSED');
            return;
          }
          const {channel, side} = ticket;
          const pair = tunnel.sockets[channel]; pair[side] = ws;
          if (pair[0] && pair[1]) {
            for (const endpoint of pair) send(endpoint!, {type: 'channel.ready', abi: RELAY_ABI_ID, tunnelId: tunnel.id, channel});
          }
          ws.on('message', (raw, binary) => {
            const other = pair[side === 0 ? 1 : 0];
            try {
              if (!tunnel.peers.every(validPeer)) throw new RelayError('UNAUTHORIZED', 'Authorization expired');
              if (!binary || !other || other.readyState !== WebSocket.OPEN) throw new RelayError('CHANNEL_NOT_READY', 'Binary paired channel required');
              const bytes = Buffer.isBuffer(raw) ? raw : Buffer.concat(Array.isArray(raw) ? raw : [Buffer.from(raw)]);
              if (bytes.length > (channel === 'control' ? 64 * 1024 : 1024 * 1024) || other.bufferedAmount + bytes.length > 2 * 1024 * 1024) {
                throw new RelayError('BACKPRESSURE', 'Channel limit reached');
              }
              other.send(bytes, {binary: true});
            } catch (error) { closeTunnel(tunnel, errorBody(error).code); }
          });
          ws.on('close', () => closeTunnel(tunnel, 'PEER_DISCONNECTED'));
          ws.on('error', () => closeTunnel(tunnel, 'TRANSPORT_ERROR'));
        });
        return;
      }
      if (!/^\/v2\/control\/(client|host\/[a-zA-Z0-9-]+)$/.test(path)) throw new RelayError('NOT_FOUND', 'Unknown WS path', 404);
      wss.handleUpgrade(req, socket, head, ws => {
        const nonce = randomBytes(32).toString('base64url');
        let peer: Peer | undefined;
        const authTimeout = setTimeout(() => ws.close(4401, 'Authentication timeout'), 5000);
        send(ws, {type: 'auth.challenge', abi: RELAY_ABI_ID, nonce, path});
        ws.on('message', (raw, binary) => {
          try {
            if (binary || raw.toString().length > 64 * 1024) throw new RelayError('INVALID_MESSAGE', 'Control JSON required');
            let parsed: unknown;
            try { parsed = JSON.parse(raw.toString()); }
            catch { throw new RelayError('INVALID_JSON', 'Invalid JSON'); }
            if (!peer) {
              const auth = envelope(parsed, 'auth.prove', ['token', 'deviceId', 'signature']);
              const token = text(auth.token, 'token', 256); const account = store.authenticate(token);
              const device = store.device(account, text(auth.deviceId, 'deviceId'));
              const signature = Buffer.from(text(auth.signature, 'signature', 128), 'base64url');
              if (!verify(null, authTranscript(nonce, path, device.id, digest(token)), device.publicKey, signature)) throw new RelayError('UNAUTHORIZED', 'Device signature rejected', 401);
              const hostId = path.startsWith('/v2/control/host/') ? path.slice('/v2/control/host/'.length) : undefined;
              if (hostId && (store.host(account, hostId).deviceId !== device.id || hosts.has(hostId))) throw new RelayError('HOST_CONFLICT', 'Host unavailable or already connected', 409);
              peer = {ws, account, device, token, hostId}; peers.add(peer);
              if (hostId) hosts.set(hostId, peer);
              clearTimeout(authTimeout); send(ws, {type: 'auth.ok', abi: RELAY_ABI_ID, deviceId: device.id}); publishDirectory(account.id); return;
            }
            validPeer(peer);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new RelayError('INVALID_MESSAGE', 'Expected control object');
            const type = text((parsed as Record<string, unknown>).type, 'message type', 64);
            switch (type) {
              case 'host.publish': {
                const data = envelope(parsed, 'host.publish', ['hostId', 'snapshot']);
                if (!peer.hostId) throw new RelayError('FORBIDDEN', 'Host required', 403);
                if (text(data.hostId, 'host id') !== peer.hostId) throw new RelayError('FORBIDDEN', 'Host identity mismatch', 403);
                const next = snapshot(data.snapshot);
                if (peer.snapshot && (next.incarnation !== peer.snapshot.incarnation || next.revision <= peer.snapshot.revision)) throw new RelayError('STALE_SNAPSHOT', 'Snapshot revision must increase');
                peer.snapshot = next; peer.publishedAt = store.now(); peer.expired = false; publishDirectory(peer.account.id); break;
              }
              case 'signal.send': {
                const data = envelope(parsed, 'signal.send', ['peerDeviceId', 'data']);
                const deviceId = text(data.peerDeviceId, 'peerDeviceId'); const signal = text(data.data, 'signal', 32 * 1024);
                const targets = [...peers].filter(item => item.account.id === peer!.account.id && item.device.id === deviceId && item !== peer);
                if (targets.length !== 1) throw new RelayError('PEER_UNAVAILABLE', 'Peer missing or ambiguous', 409);
                if (!validPeer(targets[0]!)) throw new RelayError('PEER_UNAVAILABLE', 'Peer expired', 409);
                send(targets[0]!.ws, {type: 'signal.received', abi: RELAY_ABI_ID, peerDeviceId: peer.device.id, data: signal}); break;
              }
              case 'tunnel.open': {
                const data = envelope(parsed, 'tunnel.open', ['hostId', 'sessionId']);
                if (peer.hostId) throw new RelayError('FORBIDDEN', 'Client required', 403);
                const host = hosts.get(text(data.hostId, 'hostId'));
                if (!host || host.account.id !== peer.account.id || !validPeer(host)) throw new RelayError('HOST_UNAVAILABLE', 'Host unavailable', 404);
                offerTunnel(peer, host, text(data.sessionId, 'sessionId')); break;
              }
              case 'tunnel.reject': {
                const data = envelope(parsed, 'tunnel.reject', ['tunnelId', 'reason']);
                if (!peer.hostId) throw new RelayError('FORBIDDEN', 'Host required', 403);
                const tunnelId = text(data.tunnelId, 'tunnel id');
                const reason = tunnelRejectReason(data.reason);
                const tunnel = tunnels.get(tunnelId);
                if (!tunnel) throw new RelayError('TUNNEL_NOT_PENDING', 'Tunnel offer is no longer pending', 409);
                if (tunnel.hostId !== peer.hostId || tunnel.peers[1] !== peer || tunnel.peers[1].device.id !== peer.device.id) {
                  throw new RelayError('FORBIDDEN', 'Host does not own tunnel', 403);
                }
                if (tunnel.phase !== 'pending') throw new RelayError('TUNNEL_NOT_PENDING', 'Tunnel acceptance already started', 409);
                if (tunnel.expiresAt <= store.now()) {
                  closeTunnel(tunnel, 'PAIRING_TIMEOUT');
                  break;
                }
                closeTunnel(tunnel, hostRejectedTunnelReason(reason));
                break;
              }
              default: throw new RelayError('UNKNOWN_MESSAGE', 'Unknown control message');
            }
          } catch (error) {
            send(ws, {type: 'error', abi: RELAY_ABI_ID, ...errorBody(error)});
            if (!peer || (error instanceof RelayError && error.code === 'UNAUTHORIZED')) ws.close(4401, 'Authorization failed');
          }
        });
        ws.on('error', () => ws.close(1011, 'Transport error'));
        ws.on('close', () => {
          clearTimeout(authTimeout);
          if (!peer) return;
          peers.delete(peer);
          if (peer.hostId && hosts.get(peer.hostId) === peer) hosts.delete(peer.hostId);
          for (const tunnel of tunnels.values()) if (tunnel.peers.includes(peer)) closeTunnel(tunnel, 'CONTROL_DISCONNECTED');
          publishDirectory(peer.account.id);
        });
      });
    } catch (error) {
      socket.end(`HTTP/1.1 ${error instanceof RelayError ? error.status : 500} Rejected\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
    }
  });
  const sweep = setInterval(() => {
    for (const peer of peers) {
      if (!authorized(peer)) {
        for (const tunnel of tunnels.values()) if (tunnel.peers.includes(peer)) closeTunnel(tunnel, 'UNAUTHORIZED');
        peer.ws.close(4401, 'Authorization expired');
      }
      if (peer.snapshot && !peer.expired && store.now() - peer.publishedAt! >= directoryTtl) {
        peer.expired = true; publishDirectory(peer.account.id);
      }
    }
    for (const tunnel of tunnels.values()) if (store.now() >= tunnel.expiresAt && Object.values(tunnel.sockets).some(pair => !pair[0] || !pair[1])) closeTunnel(tunnel, 'PAIRING_TIMEOUT');
  }, options.sweepMs ?? 1000);
  sweep.unref();

  return {
    async listen(port = 0, host = '127.0.0.1') {
      await new Promise<void>((resolve, reject) => { server.once('error', reject); server.listen(port, host, () => {server.off('error', reject); resolve();}); });
      const address = server.address();
      if (!address || typeof address === 'string') throw new Error('Invalid listener address');
      return `https://${host}:${address.port}`;
    },
    async close() {
      clearInterval(sweep);
      for (const ws of wss.clients) ws.terminate();
      await new Promise<void>(resolve => wss.close(() => resolve()));
      if (server.listening) await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    }
  };
}

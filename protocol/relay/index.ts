/** Relay ABI owner. Browser operations and page payloads are opaque here. */
export class RelayError extends Error {
  constructor(public readonly code: string, message: string, public readonly status = 400) {
    super(message);
  }
}

export function object(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new RelayError('INVALID_MESSAGE', 'Expected object');
  const result = value as Record<string, unknown>;
  if (Object.keys(result).some(key => !keys.includes(key))) throw new RelayError('UNKNOWN_FIELD', 'Unknown control field');
  return result;
}

export function text(value: unknown, name: string, max = 128): string {
  if (typeof value !== 'string' || !value.length || value.length > max) throw new RelayError('INVALID_FIELD', `Invalid ${name}`);
  return value;
}

export function revision(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) throw new RelayError('INVALID_REVISION', 'Invalid revision');
  return value as number;
}

export interface HostSnapshot {
  incarnation: string;
  revision: number;
  endpoints: Array<{network: 'lan' | 'public' | 'tailscale'; url: string}>;
  sessions: Array<{id: string}>;
}

export function snapshot(value: unknown): HostSnapshot {
  const data = object(value, ['incarnation', 'revision', 'endpoints', 'sessions']);
  if (!Array.isArray(data.endpoints) || data.endpoints.length > 16 || !Array.isArray(data.sessions) || data.sessions.length > 64) {
    throw new RelayError('LIMIT_EXCEEDED', 'Directory limit exceeded');
  }
  const endpoints = data.endpoints.map((value): HostSnapshot['endpoints'][number] => {
    const item = object(value, ['network', 'url']);
    const network = text(item.network, 'network');
    if (network !== 'lan' && network !== 'public' && network !== 'tailscale') throw new RelayError('INVALID_NETWORK', 'Invalid network');
    const address = text(item.url, 'url', 1024);
    let url: URL;
    try { url = new URL(address); } catch { throw new RelayError('INVALID_ENDPOINT', 'Invalid endpoint'); }
    if (!['wss:', 'https:', 'udp:'].includes(url.protocol) || !url.hostname || url.username || url.password || url.search || url.hash) {
      throw new RelayError('INVALID_ENDPOINT', 'Endpoint must not contain credentials, query or fragment');
    }
    return {network, url: address};
  });
  const sessions = data.sessions.map(value => ({id: text(object(value, ['id']).id, 'session id')}));
  if (new Set(sessions.map(item => item.id)).size !== sessions.length) throw new RelayError('DUPLICATE_SESSION', 'Duplicate session');
  return {incarnation: text(data.incarnation, 'incarnation'), revision: revision(data.revision), endpoints, sessions};
}

export type Channel = 'control' | 'media';
export interface TunnelOffer {
  type: 'tunnel.offer';
  tunnelId: string;
  peerDeviceId: string;
  expiresAt: number;
  channels: Record<Channel, {path: string; ticket: string}>;
}

/** Signature binds this connection challenge, endpoint, device and bearer digest. */
export function authTranscript(nonce: string, path: string, deviceId: string, tokenDigest: string): Buffer {
  return Buffer.from(JSON.stringify(['agentbrowser-relay-v1', nonce, path, deviceId, tokenDigest]));
}

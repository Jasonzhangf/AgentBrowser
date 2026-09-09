// Account-directory ABI. Credentials never become part of this projection.
export type AccountState = 'signed_out' | 'signing_in' | 'authenticated' | 'registering_device'
  | 'refreshing_directory' | 'signing_out' | 'expired' | 'error';
export type DirectoryState = 'empty' | 'fresh' | 'expired';
export type DirectoryHostState = 'online' | 'offline' | 'expired';

export type AccountCommand =
  | { op: 'account_status' }
  | { op: 'account_login'; username: string; password: string }
  | { op: 'account_register_device'; name: string }
  | { op: 'account_refresh' }
  | { op: 'account_logout' };

export interface DirectoryHost {
  hostId: string;
  deviceId: string;
  deviceName: string;
  status: DirectoryHostState;
  lastSeenAtMs: number;
  snapshot: {
    incarnation: string;
    revision: number;
    endpoints: Array<{ network: 'lan' | 'public' | 'tailscale'; url: string }>;
    sessions: Array<{ id: string }>;
  };
}

export interface AccountSnapshot {
  accountState: AccountState;
  generation: number;
  pending: boolean;
  error: string | null;
  expiresAtMs: number;
  deviceId: string | null;
  directoryState: DirectoryState;
  hosts: DirectoryHost[];
}

export interface AccountPort {
  request(command: AccountCommand): AccountSnapshot;
}

function text(value: unknown, name: string, max = 1024): string {
  if (typeof value !== 'string' || value.length === 0 || value.length > max) throw new Error(`INVALID_ACCOUNT_${name}`);
  return value;
}

function integer(value: unknown, name: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) throw new Error(`INVALID_ACCOUNT_${name}`);
  return value as number;
}

function parseHost(value: unknown): DirectoryHost {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('INVALID_ACCOUNT_HOST');
  const item = value as Record<string, unknown>;
  const allowed = ['hostId', 'deviceId', 'deviceName', 'status', 'lastSeenAtMs', 'snapshot'];
  if (Object.keys(item).some(key => !allowed.includes(key))) throw new Error('UNKNOWN_ACCOUNT_HOST_FIELD');
  if (item.status !== 'online' && item.status !== 'offline' && item.status !== 'expired') throw new Error('INVALID_ACCOUNT_HOST_STATUS');
  const rawSnapshot = item.snapshot;
  if (!rawSnapshot || typeof rawSnapshot !== 'object' || Array.isArray(rawSnapshot)) throw new Error('INVALID_ACCOUNT_HOST_SNAPSHOT');
  const snapshot = rawSnapshot as Record<string, unknown>;
  if (Object.keys(snapshot).some(key => !['incarnation', 'revision', 'endpoints', 'sessions'].includes(key))) throw new Error('UNKNOWN_ACCOUNT_HOST_SNAPSHOT_FIELD');
  const endpoints = snapshot.endpoints;
  const sessions = snapshot.sessions;
  if (!Array.isArray(endpoints) || !Array.isArray(sessions) || endpoints.length > 16 || sessions.length > 64) throw new Error('INVALID_ACCOUNT_HOST_SNAPSHOT');
  const parsedEndpoints = endpoints.map(endpoint => {
    if (!endpoint || typeof endpoint !== 'object' || Array.isArray(endpoint)) throw new Error('INVALID_ACCOUNT_ENDPOINT');
    const item = endpoint as Record<string, unknown>;
    if (Object.keys(item).some(key => !['network', 'url'].includes(key))) throw new Error('UNKNOWN_ACCOUNT_ENDPOINT_FIELD');
    if (item.network !== 'lan' && item.network !== 'public' && item.network !== 'tailscale') throw new Error('INVALID_ACCOUNT_NETWORK');
    return { network: item.network, url: text(item.url, 'ENDPOINT', 1024) } as DirectoryHost['snapshot']['endpoints'][number];
  });
  const parsedSessions = sessions.map(session => {
    if (!session || typeof session !== 'object' || Array.isArray(session)) throw new Error('INVALID_ACCOUNT_SESSION');
    const item = session as Record<string, unknown>;
    if (Object.keys(item).some(key => key !== 'id')) throw new Error('UNKNOWN_ACCOUNT_SESSION_FIELD');
    return { id: text(item.id, 'SESSION', 128) };
  });
  return {
    hostId: text(item.hostId, 'HOST_ID', 128),
    deviceId: text(item.deviceId, 'DEVICE_ID', 128),
    deviceName: text(item.deviceName, 'DEVICE_NAME', 64),
    status: item.status,
    lastSeenAtMs: integer(item.lastSeenAtMs, 'LAST_SEEN'),
    snapshot: {
      incarnation: text(snapshot.incarnation, 'INCARNATION', 128),
      revision: integer(snapshot.revision, 'REVISION'),
      endpoints: parsedEndpoints,
      sessions: parsedSessions,
    },
  };
}

export function parseAccountSnapshot(raw: string): AccountSnapshot {
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('INVALID_ACCOUNT_RESPONSE');
  const data = value as Record<string, unknown>;
  const serialized = JSON.stringify(value);
  if (serialized.includes('"token"') || serialized.includes('"password"') || serialized.includes('"privateKey"') || serialized.includes('"private_key"')) {
    throw new Error('ACCOUNT_SECRET_IN_PROJECTION');
  }
  const allowed = ['accountState', 'generation', 'pending', 'error', 'expiresAtMs', 'deviceId', 'directoryState', 'hosts'];
  if (Object.keys(data).some(key => !allowed.includes(key))) throw new Error('UNKNOWN_ACCOUNT_RESPONSE_FIELD');
  const states: AccountState[] = ['signed_out', 'signing_in', 'authenticated', 'registering_device', 'refreshing_directory', 'signing_out', 'expired', 'error'];
  if (!states.includes(data.accountState as AccountState)) throw new Error('INVALID_ACCOUNT_STATE');
  if (data.directoryState !== 'empty' && data.directoryState !== 'fresh' && data.directoryState !== 'expired') throw new Error('INVALID_DIRECTORY_STATE');
  if (typeof data.pending !== 'boolean') throw new Error('INVALID_ACCOUNT_PENDING');
  if (data.error !== null && typeof data.error !== 'string') throw new Error('INVALID_ACCOUNT_ERROR');
  if (data.deviceId !== null && typeof data.deviceId !== 'string') throw new Error('INVALID_ACCOUNT_DEVICE');
  if (!Array.isArray(data.hosts) || data.hosts.length > 128) throw new Error('INVALID_ACCOUNT_HOSTS');
  return {
    accountState: data.accountState as AccountState,
    generation: integer(data.generation, 'GENERATION'),
    pending: data.pending,
    error: data.error,
    expiresAtMs: integer(data.expiresAtMs, 'EXPIRY'),
    deviceId: data.deviceId,
    directoryState: data.directoryState,
    hosts: data.hosts.map(parseHost),
  };
}

export function accountPort(bridge: { request(raw: string): string } | undefined): AccountPort {
  if (!bridge) throw new Error('NATIVE_ACCOUNT_BRIDGE_UNAVAILABLE');
  return { request(command) { return parseAccountSnapshot(bridge.request(JSON.stringify(command))); } };
}

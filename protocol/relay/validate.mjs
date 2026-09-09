#!/usr/bin/env node
import {createHash} from 'node:crypto';
import {readFileSync, writeFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const schemaPath = join(root, 'relay-abi.schema.json');
const lockPath = join(root, 'ABI.lock.json');
const ABI_ID = 'agentbrowser-relay-v0';
const channelKinds = new Set(['control', 'media']);
const ids = (value, max = 128) => typeof value === 'string' && value.length > 0 && value.length <= max;

export function loadSchema() {
  const raw = readFileSync(schemaPath, 'utf8');
  const schema = JSON.parse(raw);
  if (schema.abi_id !== ABI_ID) throw new Error('RELAY_ABI_ID_MISMATCH');
  return {raw, schema, sha256: createHash('sha256').update(raw).digest('hex')};
}

function object(value, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('INVALID_MESSAGE');
  const extra = Object.keys(value).filter(key => !keys.includes(key));
  if (extra.length) throw new Error('UNKNOWN_FIELD');
  return value;
}

function envelope(value, type, keys) {
  const result = object(value, ['type', 'abi', ...keys]);
  if (result.type !== type) throw new Error('UNEXPECTED_TYPE');
  if (result.abi !== ABI_ID) throw new Error('RELAY_ABI_ID_MISMATCH');
  return result;
}

function text(value, max = 128) {
  if (!ids(value, max)) throw new Error('INVALID_FIELD');
  return value;
}

function snapshot(value) {
  const data = object(value, ['incarnation', 'revision', 'endpoints', 'sessions']);
  text(data.incarnation);
  if (!Number.isSafeInteger(data.revision) || data.revision < 0) throw new Error('INVALID_REVISION');
  if (!Array.isArray(data.endpoints) || data.endpoints.length > 16) throw new Error('LIMIT_EXCEEDED');
  for (const endpoint of data.endpoints) {
    const item = object(endpoint, ['network', 'url']);
    if (!['lan', 'public', 'tailscale'].includes(item.network)) throw new Error('INVALID_NETWORK');
    text(item.url, 1024);
    let url;
    try { url = new URL(item.url); } catch { throw new Error('INVALID_ENDPOINT'); }
    if (!['wss:', 'https:', 'udp:'].includes(url.protocol) || !url.hostname || url.username || url.password || url.search || url.hash) {
      throw new Error('INVALID_ENDPOINT');
    }
  }
  if (!Array.isArray(data.sessions) || data.sessions.length > 64) throw new Error('LIMIT_EXCEEDED');
  const seen = new Set();
  for (const session of data.sessions) {
    const item = object(session, ['id']);
    text(item.id);
    if (!seen.add(item.id)) throw new Error('DUPLICATE_SESSION');
  }
  return data;
}

function parseChannels(value) {
  const data = object(value, ['control', 'media']);
  for (const name of channelKinds) {
    const item = object(data[name], ['path', 'ticket']);
    text(item.path, 256);
    text(item.ticket);
  }
  return data;
}

function parseHostList(value) {
  if (!Array.isArray(value) || value.length > 128) throw new Error('LIMIT_EXCEEDED');
  for (const host of value) {
    const item = object(host, ['hostId', 'deviceId', 'deviceName', 'snapshot']);
    text(item.hostId); text(item.deviceId); text(item.deviceName, 64); snapshot(item.snapshot);
  }
  return value;
}

const definitions = {
  'auth.challenge': value => { const data = envelope(value, 'auth.challenge', ['nonce', 'path']); text(data.nonce); text(data.path, 256); return data; },
  'auth.prove': value => { const data = envelope(value, 'auth.prove', ['token', 'deviceId', 'signature']); text(data.token, 256); text(data.deviceId); text(data.signature, 128); return data; },
  'auth.ok': value => { const data = envelope(value, 'auth.ok', ['deviceId']); text(data.deviceId); return data; },
  'directory.snapshot': value => { const data = envelope(value, 'directory.snapshot', ['hosts']); parseHostList(data.hosts); return data; },
  'host.publish': value => { const data = envelope(value, 'host.publish', ['hostId', 'snapshot']); text(data.hostId); snapshot(data.snapshot); return data; },
  'signal.send': value => { const data = envelope(value, 'signal.send', ['peerDeviceId', 'data']); text(data.peerDeviceId); text(data.data, 32768); return data; },
  'signal.received': value => { const data = envelope(value, 'signal.received', ['peerDeviceId', 'data']); text(data.peerDeviceId); text(data.data, 32768); return data; },
  'tunnel.open': value => { const data = envelope(value, 'tunnel.open', ['hostId', 'sessionId']); text(data.hostId); text(data.sessionId); return data; },
  'tunnel.offer': value => { const data = envelope(value, 'tunnel.offer', ['tunnelId', 'hostId', 'sessionId', 'peerDeviceId', 'side', 'expiresAt', 'channels']); text(data.tunnelId); text(data.hostId); text(data.sessionId); text(data.peerDeviceId); if (data.side !== 0 && data.side !== 1) throw new Error('INVALID_SIDE'); if (!Number.isSafeInteger(data.expiresAt) || data.expiresAt < 0) throw new Error('INVALID_EXPIRY'); parseChannels(data.channels); return data; },
  'tunnel.reject': value => { const data = envelope(value, 'tunnel.reject', ['tunnelId', 'reason']); text(data.tunnelId); if (!['UNKNOWN_PEER', 'CAPACITY'].includes(data.reason)) throw new Error('INVALID_REJECT_REASON'); return data; },
  'tunnel.closed': value => { const data = envelope(value, 'tunnel.closed', ['tunnelId', 'reason']); text(data.tunnelId); text(data.reason); return data; },
  'channel.ready': value => { const data = envelope(value, 'channel.ready', ['tunnelId', 'channel']); text(data.tunnelId); if (!channelKinds.has(data.channel)) throw new Error('INVALID_CHANNEL'); return data; },
  error: value => { const data = envelope(value, 'error', ['code', 'message']); text(data.code); text(data.message, 1024); return data; },
};

export function parseEnvelope(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('INVALID_MESSAGE');
  const type = text(value.type, 64);
  const parse = definitions[type];
  if (!parse) throw new Error('UNSUPPORTED_TYPE');
  return parse(value);
}

export function writeLock() {
  const {schema, sha256} = loadSchema();
  const lock = {abi_id: schema.abi_id, sha256, owner: 'AgentBrowser protocol/relay'};
  writeFileSync(lockPath, `${JSON.stringify(lock, null, 2)}\n`);
  return lock;
}

export function checkLock() {
  const {schema, sha256} = loadSchema();
  const lock = JSON.parse(readFileSync(lockPath, 'utf8'));
  if (lock.abi_id !== schema.abi_id || lock.sha256 !== sha256 || lock.owner !== 'AgentBrowser protocol/relay') throw new Error('RELAY_ABI_LOCK_DRIFT');
  return lock;
}

const mode = process.argv[2];
if (mode === '--build') {
  console.log(JSON.stringify(writeLock()));
} else if (mode === '--check') {
  console.log(JSON.stringify(checkLock()));
}

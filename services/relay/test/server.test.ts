import {test, after} from 'node:test';
import assert from 'node:assert/strict';
import {generateKeyPairSync, sign, type KeyObject} from 'node:crypto';
import {mkdtempSync, mkdirSync, readFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFileSync, spawn} from 'node:child_process';
import {request} from 'node:https';
import {once} from 'node:events';
import {WebSocket} from 'ws';
import {createRelayServer} from '../src/server.js';
import {RelayStore, digest} from '../src/store.js';
import {authTranscript, type TunnelOffer} from '../../../protocol/relay/index.js';

const dir = mkdtempSync(join(tmpdir(), 'relay-tls-test-'));
const keyFile = join(dir, 'key.pem'), certFile = join(dir, 'cert.pem');
execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', keyFile, '-out', certFile, '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1'], {stdio: 'ignore'});
const cert = readFileSync(certFile), key = readFileSync(keyFile);
after(() => rmSync(dir, {recursive: true, force: true}));

function api(base: string, path: string, method = 'GET', token?: string, body?: unknown): Promise<{status: number; body: any}> {
  return new Promise((resolve, reject) => {
    const req = request(base + path, {method, ca: cert, headers: {...(token ? {authorization: `Bearer ${token}`} : {}), 'content-type': 'application/json'}}, res => {
      let data = ''; res.on('data', chunk => data += chunk); res.on('end', () => resolve({status: res.statusCode!, body: JSON.parse(data)}));
    });
    req.on('error', reject); req.end(body === undefined ? undefined : JSON.stringify(body));
  });
}
class Inbox {
  items: Array<{value: any; binary: boolean}> = [];
  constructor(readonly ws: WebSocket) {
    ws.on('error', () => {}); // individual tests assert rejection/closure through explicit listeners
    ws.on('message', (data, binary) => this.items.push({value: binary ? Buffer.from(data as Buffer) : JSON.parse(data.toString()), binary}));
  }
  async take(type: string | 'binary') {
    const deadline = Date.now() + 5000;
    while (Date.now() < deadline) {
      const index = this.items.findIndex(item => type === 'binary' ? item.binary : !item.binary && item.value.type === type);
      if (index >= 0) return this.items.splice(index, 1)[0]!.value;
      await new Promise(resolve => setTimeout(resolve, 5));
    }
    throw new Error(`Timed out waiting for ${type}: ${JSON.stringify(this.items)}`);
  }
}
interface Identity {id: string; privateKey: KeyObject; token: string}
async function enroll(base: string, token: string, name: string): Promise<Identity> {
  const pair = generateKeyPairSync('ed25519');
  const result = await api(base, '/v2/devices', 'POST', token, {name, publicKey: pair.publicKey.export({type: 'spki', format: 'pem'}).toString()});
  assert.equal(result.status, 201);
  return {id: result.body.id, privateKey: pair.privateKey, token};
}
async function connect(base: string, path: string, device: Identity, wrongSignature = false) {
  const ws = new WebSocket(base.replace('https:', 'wss:') + path, {ca: cert}); const inbox = new Inbox(ws);
  const challenge = await inbox.take('auth.challenge');
  const signature = sign(null, authTranscript(challenge.nonce, path, device.id, digest(device.token)), wrongSignature ? generateKeyPairSync('ed25519').privateKey : device.privateKey).toString('base64url');
  ws.send(JSON.stringify({type: 'auth.prove', token: device.token, deviceId: device.id, signature}));
  return inbox;
}

test('real TLS/WSS: account isolation, signed devices, directory, signal and separate binary tunnels', {timeout: 20000}, async () => {
  const store = new RelayStore(':memory:');
  const relay = createRelayServer({store, tls: {cert, key}});
  const base = await relay.listen();
  try {
    await store.createAccount('alice', 'alice password long'); await store.createAccount('bob', 'bob password long');
    const login = await api(base, '/v2/login', 'POST', undefined, {username: 'alice', password: 'alice password long'});
    assert.equal(login.status, 200);
    const token = login.body.token as string;
    const bobToken = (await store.login('bob', 'bob password long')).token;
    const hostDevice = await enroll(base, token, 'mac'); const clientDevice = await enroll(base, token, 'phone');
    const bobDevice = await enroll(base, bobToken, 'other');
    const hostId = (await api(base, '/v2/hosts', 'POST', token, {deviceId: hostDevice.id})).body.id;
    assert.equal((await api(base, '/v2/hosts', 'POST', bobToken, {deviceId: hostDevice.id})).status, 404);
    const bad = await connect(base, '/v2/control/client', clientDevice, true);
    assert.equal((await bad.take('error')).code, 'UNAUTHORIZED');
    const host = await connect(base, `/v2/control/host/${hostId}`, hostDevice); await host.take('auth.ready');
    const client = await connect(base, '/v2/control/client', clientDevice); await client.take('auth.ready');
    const bob = await connect(base, '/v2/control/client', bobDevice); await bob.take('auth.ready');
    host.ws.send(JSON.stringify({type: 'host.publish', snapshot: {incarnation: 'boot1', revision: 0, endpoints: [], sessions: [{id: 'tab-a'}]}}));
    let result;
    do { result = await client.take('directory.snapshot'); } while (!result.hosts.length);
    assert.equal(result.hosts[0].snapshot.sessions[0].id, 'tab-a');
    assert.deepEqual((await api(base, '/v2/directory', 'GET', bobToken)).body.hosts, []);
    host.ws.send(JSON.stringify({type: 'host.publish', snapshot: {incarnation: 'boot1', revision: 1, endpoints: [], sessions: []}}));
    const replacement = await client.take('directory.snapshot');
    assert.deepEqual(replacement.hosts[0].snapshot.sessions, []);
    host.ws.send(JSON.stringify({type: 'host.publish', snapshot: {incarnation: 'boot1', revision: 0, endpoints: [], sessions: [{id: 'stale'}]}}));
    assert.equal((await host.take('error')).code, 'STALE_SNAPSHOT');
    client.ws.send(JSON.stringify({type: 'signal.send', peerDeviceId: hostDevice.id, data: 'sdp-offer'}));
    assert.equal((await host.take('signal.received')).data, 'sdp-offer');
    bob.ws.send(JSON.stringify({type: 'signal.send', peerDeviceId: hostDevice.id, data: 'cross-account'}));
    assert.equal((await bob.take('error')).code, 'PEER_UNAVAILABLE');
    bob.ws.send(JSON.stringify({type: 'tunnel.open', hostId, sessionId: 'tab-a'})); assert.equal((await bob.take('error')).code, 'HOST_UNAVAILABLE');
    host.ws.send(JSON.stringify({type: 'host.publish', snapshot: {incarnation: 'boot1', revision: 2, endpoints: [], sessions: [{id: 'tab-a'}]}}));
    client.ws.send(JSON.stringify({type: 'tunnel.open', hostId, sessionId: 'tab-a'}));
    const offers = [await client.take('tunnel.offer'), await host.take('tunnel.offer')] as TunnelOffer[];
    const channels: Record<string, Inbox[]> = {};
    for (const name of ['control', 'media'] as const) {
      channels[name] = offers.map(offer => new Inbox(new WebSocket(base.replace('https:', 'wss:') + offer.channels[name].path, {ca: cert, headers: {authorization: `Bearer ${offer.channels[name].ticket}`}})));
      await Promise.all(channels[name]!.map(inbox => inbox.take('channel.ready')));
    }
    const bytes = Buffer.from([0, 1, 255, 13, 10, 2]);
    channels.media![0]!.ws.send(bytes);
    assert.deepEqual(await channels.media![1]!.take('binary'), bytes);
    channels.control![1]!.ws.send(Buffer.from('opaque-operation'));
    assert.equal((await channels.control![0]!.take('binary')).toString(), 'opaque-operation');
    assert.equal(channels.control![1]!.items.filter(item => item.binary).length, 0);
    const used = offers[0]!.channels.media;
    const replay = new WebSocket(base.replace('https:', 'wss:') + used.path, {ca: cert, headers: {authorization: `Bearer ${used.ticket}`}});
    const [error] = await once(replay, 'error'); assert.match(String(error), /401/);
    channels.media![0]!.ws.close();
    assert.equal((await client.take('tunnel.closed')).reason, 'PEER_DISCONNECTED');
    assert.equal((await api(base, '/v2/token', 'DELETE', token)).status, 200);
    assert.equal((await api(base, '/v2/directory', 'GET', token)).status, 401);
  } finally { await relay.close(); store.close(); }
});

test('directory expiry retains revision fencing; token expiry closes connections', {timeout: 10000}, async () => {
  let now = 0;
  const store = new RelayStore(':memory:', {now: () => now, tokenTtlMs: 1000});
  const relay = createRelayServer({store, tls: {cert, key}, directoryTtlMs: 100, sweepMs: 10});
  const base = await relay.listen();
  try {
    await store.createAccount('alice', 'alice password long'); const token = (await store.login('alice', 'alice password long')).token;
    const device = await enroll(base, token, 'mac'); const id = (await api(base, '/v2/hosts', 'POST', token, {deviceId: device.id})).body.id;
    const host = await connect(base, `/v2/control/host/${id}`, device); await host.take('auth.ready');
    host.ws.send(JSON.stringify({type: 'host.publish', snapshot: {incarnation: 'boot', revision: 3, endpoints: [], sessions: []}}));
    let result; do { result = await host.take('directory.snapshot'); } while (!result.hosts.length);
    now = 200;
    assert.deepEqual((await host.take('directory.snapshot')).hosts, []);
    host.ws.send(JSON.stringify({type: 'host.publish', snapshot: {incarnation: 'boot', revision: 2, endpoints: [], sessions: []}}));
    assert.equal((await host.take('error')).code, 'STALE_SNAPSHOT');
    const closed = once(host.ws, 'close'); now = 1000;
    assert.equal((await closed)[0], 4401);
  } finally { await relay.close(); store.close(); }
});

test('expired Host snapshots reject tunnels before the sweep runs', {timeout: 10000}, async () => {
  let now = 0;
  const store = new RelayStore(':memory:', {now: () => now});
  const relay = createRelayServer({store, tls: {cert, key}, directoryTtlMs: 100, sweepMs: 60_000});
  const base = await relay.listen();
  try {
    await store.createAccount('alice', 'alice password long');
    const token = (await store.login('alice', 'alice password long')).token;
    const hostDevice = await enroll(base, token, 'host');
    const clientDevice = await enroll(base, token, 'client');
    const hostId = (await api(base, '/v2/hosts', 'POST', token, {deviceId: hostDevice.id})).body.id;
    const host = await connect(base, `/v2/control/host/${hostId}`, hostDevice); await host.take('auth.ready');
    const client = await connect(base, '/v2/control/client', clientDevice); await client.take('auth.ready');
    host.ws.send(JSON.stringify({type: 'host.publish', snapshot: {incarnation: 'expired-host', revision: 1, endpoints: [], sessions: [{id: 'expired-session'}]}}));
    let directory;
    do { directory = await client.take('directory.snapshot'); } while (!directory.hosts.some((item: {hostId: string}) => item.hostId === hostId));
    now = 101;
    client.ws.send(JSON.stringify({type: 'tunnel.open', hostId, sessionId: 'expired-session'}));
    assert.equal((await client.take('error')).code, 'SESSION_UNAVAILABLE');
  } finally { await relay.close(); store.close(); }
});

test('compiled CLI provisions account and serves real HTTPS entrypoint', {timeout: 10000}, async () => {
  const unpacked = join(dir, 'artifact'); mkdirSync(unpacked);
  execFileSync('tar', ['-xf', '../../generated/modules/relay-service/lib/relay.tar', '-C', unpacked]);
  const entry = join(unpacked, 'dist/services/relay/src/main.js'); const db = join(dir, 'cli.sqlite');
  execFileSync(process.execPath, [entry, 'account-add', db, 'operator'], {input: 'operator password long\n', stdio: ['pipe', 'pipe', 'pipe']});
  const child = spawn(process.execPath, [entry, 'serve', db, certFile, keyFile], {env: {...process.env, RELAY_PORT: '0', RELAY_BIND: '127.0.0.1'}, stdio: ['ignore', 'pipe', 'pipe']});
  try {
    const address = await new Promise<string>((resolve, reject) => {
      let output = '';
      child.stdout.on('data', chunk => {output += chunk; if (output.includes('\n')) resolve(JSON.parse(output.trim()).address);});
      child.once('exit', code => reject(new Error(`CLI exited ${code}`)));
      child.once('error', reject);
    });
    assert.equal((await api(address, '/health')).status, 200);
    const login = await api(address, '/v2/login', 'POST', undefined, {username: 'operator', password: 'operator password long'});
    assert.equal(login.status, 200);
    const token = login.body.token;
    const hd = await enroll(address, token, 'mac'), cd = await enroll(address, token, 'phone');
    const hostId = (await api(address, '/v2/hosts', 'POST', token, {deviceId: hd.id})).body.id;
    const host = await connect(address, `/v2/control/host/${hostId}`, hd); await host.take('auth.ready');
    const client = await connect(address, '/v2/control/client', cd); await client.take('auth.ready');
    host.ws.send(JSON.stringify({type: 'host.publish', snapshot: {incarnation: 'artifact-host', revision: 1, endpoints: [], sessions: [{id: 'artifact-session'}]}}));
    client.ws.send(JSON.stringify({type: 'tunnel.open', hostId, sessionId: 'artifact-session'}));
    const offers = [await client.take('tunnel.offer'), await host.take('tunnel.offer')] as TunnelOffer[];
    for (const name of ['control', 'media'] as const) {
      const pair = offers.map(item => new Inbox(new WebSocket(address.replace('https:', 'wss:') + item.channels[name].path, {ca: cert, headers: {authorization: `Bearer ${item.channels[name].ticket}`}})));
      await Promise.all(pair.map(item => item.take('channel.ready')));
      const bytes = Buffer.from(`artifact-${name}`);
      pair[0]!.ws.send(bytes); assert.deepEqual(await pair[1]!.take('binary'), bytes);
    }
    client.ws.send(JSON.stringify({type: 'tunnel.open', hostId, sessionId: 'artifact-session'}));
    const rejected = [await client.take('tunnel.offer'), await host.take('tunnel.offer')] as TunnelOffer[];
    host.ws.send(JSON.stringify({type: 'tunnel.reject', tunnelId: rejected[1]!.tunnelId, reason: 'CAPACITY'}));
    assert.equal((await client.take('tunnel.closed')).reason, 'HOST_REJECTED_CAPACITY');
    assert.equal((await host.take('tunnel.closed')).reason, 'HOST_REJECTED_CAPACITY');
    assert.equal((await api(address, '/v2/token', 'DELETE', token)).status, 200);
    assert.equal((await client.take('tunnel.closed')).reason, 'UNAUTHORIZED');
  } finally {
    const exited = once(child, 'exit'); child.kill('SIGTERM'); assert.equal((await exited)[0], 0);
  }
});

test('tunnel tickets bind exact paths, expire, reject invalid frames and close on revocation', {timeout: 10000}, async () => {
  let now = 0;
  const store = new RelayStore(':memory:', {now: () => now});
  const relay = createRelayServer({store, tls: {cert, key}, ticketTtlMs: 100, sweepMs: 10});
  const base = await relay.listen();
  try {
    await store.createAccount('alice', 'alice password long');
    const token = (await store.login('alice', 'alice password long')).token;
    const hd = await enroll(base, token, 'host'), cd = await enroll(base, token, 'client');
    const hostId = (await api(base, '/v2/hosts', 'POST', token, {deviceId: hd.id})).body.id;
    const host = await connect(base, `/v2/control/host/${hostId}`, hd); await host.take('auth.ready');
    const client = await connect(base, '/v2/control/client', cd); await client.take('auth.ready');
    host.ws.send(JSON.stringify({type: 'host.publish', snapshot: {incarnation: 'ticket-host', revision: 1, endpoints: [], sessions: [{id: 'ticket-session'}]}}));
    const duplicate = await connect(base, `/v2/control/host/${hostId}`, hd);
    assert.equal((await duplicate.take('error')).code, 'HOST_CONFLICT');
    client.ws.send('{'); assert.equal((await client.take('error')).code, 'INVALID_JSON');
    async function offer() {
      client.ws.send(JSON.stringify({type: 'tunnel.open', hostId, sessionId: 'ticket-session'}));
      return [await client.take('tunnel.offer'), await host.take('tunnel.offer')] as TunnelOffer[];
    }
    function channel(item: TunnelOffer, name: 'control' | 'media') {
      return new Inbox(new WebSocket(base.replace('https:', 'wss:') + item.channels[name].path, {ca: cert, headers: {authorization: `Bearer ${item.channels[name].ticket}`}}));
    }
    const expired = await offer();
    const wrong = new WebSocket(base.replace('https:', 'wss:') + expired[0]!.channels.media.path, {ca: cert, headers: {authorization: `Bearer ${expired[0]!.channels.control.ticket}`}});
    assert.match(String((await once(wrong, 'error'))[0]), /401/);
    now = 101;
    assert.equal((await client.take('tunnel.closed')).reason, 'PAIRING_TIMEOUT');
    await host.take('tunnel.closed');
    const late = channel(expired[0]!, 'control');
    assert.match(String((await once(late.ws, 'error'))[0]), /401/);
    for (const frame of ['text', Buffer.alloc(65537)]) {
      const offers = await offer();
      const pair = offers.map(item => channel(item, 'control'));
      await Promise.all(pair.map(item => item.take('channel.ready')));
      pair[0]!.ws.send(frame);
      assert.equal((await client.take('tunnel.closed')).reason, typeof frame === 'string' ? 'CHANNEL_NOT_READY' : 'BACKPRESSURE');
      await host.take('tunnel.closed');
      assert.equal(pair[1]!.items.filter(item => item.binary).length, 0);
    }
    const offers = await offer();
    const data = offers.flatMap(item => [channel(item, 'control'), channel(item, 'media')]);
    await Promise.all(data.map(item => item.take('channel.ready')));
    const closures = data.map(item => once(item.ws, 'close'));
    await api(base, '/v2/token', 'DELETE', token);
    await Promise.all(closures);
    assert.equal((await client.take('tunnel.closed')).reason, 'UNAUTHORIZED');
  } finally { await relay.close(); store.close(); }
});

test('Host rejection is authenticated, typed, pending-only and tunnel-isolated', {timeout: 20000}, async () => {
  const store = new RelayStore(':memory:');
  const relay = createRelayServer({store, tls: {cert, key}});
  const base = await relay.listen();
  try {
    await store.createAccount('alice', 'alice password long');
    const token = (await store.login('alice', 'alice password long')).token;
    const hostDevice = await enroll(base, token, 'host');
    const otherHostDevice = await enroll(base, token, 'other-host');
    const clientDevice = await enroll(base, token, 'client');
    const hostId = (await api(base, '/v2/hosts', 'POST', token, {deviceId: hostDevice.id})).body.id;
    const otherHostId = (await api(base, '/v2/hosts', 'POST', token, {deviceId: otherHostDevice.id})).body.id;
    const host = await connect(base, `/v2/control/host/${hostId}`, hostDevice);
    const otherHost = await connect(base, `/v2/control/host/${otherHostId}`, otherHostDevice);
    const client = await connect(base, '/v2/control/client', clientDevice);
    await Promise.all([host.take('auth.ready'), otherHost.take('auth.ready'), client.take('auth.ready')]);
    host.ws.send(JSON.stringify({type: 'host.publish', snapshot: {incarnation: 'reject-host', revision: 1, endpoints: [], sessions: [{id: 'reject-session'}]}}));
    while (!(await client.take('directory.snapshot')).hosts.some((item: {hostId: string}) => item.hostId === hostId)) {}

    async function offer() {
      client.ws.send(JSON.stringify({type: 'tunnel.open', hostId, sessionId: 'reject-session'}));
      return [await client.take('tunnel.offer'), await host.take('tunnel.offer')] as TunnelOffer[];
    }
    const rejectedUnknownPeer = await offer();
    host.ws.send(JSON.stringify({type: 'tunnel.reject', tunnelId: rejectedUnknownPeer[1]!.tunnelId, reason: 'UNKNOWN_PEER'}));
    assert.equal((await client.take('tunnel.closed')).reason, 'HOST_REJECTED_UNKNOWN_PEER');
    assert.equal((await host.take('tunnel.closed')).reason, 'HOST_REJECTED_UNKNOWN_PEER');

    host.ws.send(JSON.stringify({type: 'tunnel.reject', tunnelId: 'missing-tunnel', reason: 'CAPACITY'}));
    assert.equal((await host.take('error')).code, 'TUNNEL_NOT_PENDING');

    const rejectedCapacity = await offer();
    otherHost.ws.send(JSON.stringify({type: 'tunnel.reject', tunnelId: rejectedCapacity[1]!.tunnelId, reason: 'CAPACITY'}));
    assert.equal((await otherHost.take('error')).code, 'FORBIDDEN');
    host.ws.send(JSON.stringify({type: 'tunnel.reject', tunnelId: rejectedCapacity[1]!.tunnelId, reason: 'CAPACITY'}));
    assert.equal((await client.take('tunnel.closed')).reason, 'HOST_REJECTED_CAPACITY');
    assert.equal((await host.take('tunnel.closed')).reason, 'HOST_REJECTED_CAPACITY');

    const active = await offer();
    function channel(offer: TunnelOffer, name: 'control' | 'media') {
      return new Inbox(new WebSocket(base.replace('https:', 'wss:') + offer.channels[name].path, {ca: cert, headers: {authorization: `Bearer ${offer.channels[name].ticket}`}}));
    }
    const control = active.map(offer => channel(offer, 'control'));
    const media = active.map(offer => channel(offer, 'media'));
    await Promise.all([...control, ...media].map(item => item.take('channel.ready')));
    host.ws.send(JSON.stringify({type: 'tunnel.reject', tunnelId: active[1]!.tunnelId, reason: 'UNKNOWN_PEER'}));
    assert.equal((await host.take('error')).code, 'TUNNEL_NOT_PENDING');
    const payload = Buffer.from('established tunnel survives rejected request');
    control[0]!.ws.send(payload);
    assert.deepEqual(await control[1]!.take('binary'), payload);
    control[0]!.ws.close();
    await client.take('tunnel.closed');
    await host.take('tunnel.closed');
  } finally { await relay.close(); store.close(); }
});

test('expired tunnel rejection closes only expired pending tunnel', {timeout: 10000}, async () => {
  let now = 0;
  const store = new RelayStore(':memory:', {now: () => now});
  const relay = createRelayServer({store, tls: {cert, key}, ticketTtlMs: 100, sweepMs: 60_000});
  const base = await relay.listen();
  try {
    await store.createAccount('alice', 'alice password long');
    const token = (await store.login('alice', 'alice password long')).token;
    const hostDevice = await enroll(base, token, 'host');
    const clientDevice = await enroll(base, token, 'client');
    const hostId = (await api(base, '/v2/hosts', 'POST', token, {deviceId: hostDevice.id})).body.id;
    const host = await connect(base, `/v2/control/host/${hostId}`, hostDevice);
    const client = await connect(base, '/v2/control/client', clientDevice);
    await Promise.all([host.take('auth.ready'), client.take('auth.ready')]);
    host.ws.send(JSON.stringify({type: 'host.publish', snapshot: {incarnation: 'expired-host', revision: 1, endpoints: [], sessions: [{id: 'expired-session'}]}}));
    while (!(await client.take('directory.snapshot')).hosts.some((item: {hostId: string}) => item.hostId === hostId)) {}
    client.ws.send(JSON.stringify({type: 'tunnel.open', hostId, sessionId: 'expired-session'}));
    const offers = [await client.take('tunnel.offer'), await host.take('tunnel.offer')] as TunnelOffer[];
    now = 101;
    host.ws.send(JSON.stringify({type: 'tunnel.reject', tunnelId: offers[1]!.tunnelId, reason: 'CAPACITY'}));
    assert.equal((await client.take('tunnel.closed')).reason, 'PAIRING_TIMEOUT');
    assert.equal((await host.take('tunnel.closed')).reason, 'PAIRING_TIMEOUT');
  } finally { await relay.close(); store.close(); }
});

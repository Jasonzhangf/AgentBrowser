import { test } from 'node:test';
import assert from 'node:assert/strict';
import { accountPort, parseAccountSnapshot } from './account-directory';

const empty = {accountState: 'signed_out', generation: 0, pending: false, error: null, expiresAtMs: 0, deviceId: null, directoryState: 'empty', hosts: []};

test('account port keeps command plane typed and projection secret-free', () => {
  let request = '';
  const port = accountPort({request(raw) { request = raw; return JSON.stringify(empty); }});
  assert.deepEqual(port.request({op: 'account_status'}), empty);
  assert.equal(request, '{"op":"account_status"}');
});

test('account projection preserves online, offline, and expired host status', () => {
  const snapshot = parseAccountSnapshot(JSON.stringify({...empty, accountState: 'authenticated', generation: 3, directoryState: 'fresh', deviceId: 'device-1', hosts: [{
    hostId: 'host-1', deviceId: 'device-2', deviceName: 'Mac', status: 'online', lastSeenAtMs: 12,
    snapshot: {incarnation: 'boot-1', revision: 4, endpoints: [{network: 'tailscale', url: 'wss://relay.invalid/host'}], sessions: [{id: 'tab-1'}]},
  }, {
    hostId: 'host-2', deviceId: 'device-3', deviceName: 'Old Mac', status: 'expired', lastSeenAtMs: 9,
    snapshot: {incarnation: 'boot-0', revision: 1, endpoints: [], sessions: []},
  }]}));
  assert.deepEqual(snapshot.hosts.map(host => host.status), ['online', 'expired']);
  assert.equal(snapshot.hosts[0].snapshot.endpoints[0].network, 'tailscale');
});

test('account projection rejects credential leakage and malformed states', () => {
  assert.throws(() => parseAccountSnapshot(JSON.stringify({...empty, token: 'secret'})), /SECRET/);
  assert.throws(() => parseAccountSnapshot(JSON.stringify({...empty, accountState: 'ready'})), /STATE/);
  assert.throws(() => parseAccountSnapshot('{"accountState":"signed_out"}'), /INVALID/);
});

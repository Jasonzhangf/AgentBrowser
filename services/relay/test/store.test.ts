import {test} from 'node:test';
import assert from 'node:assert/strict';
import {generateKeyPairSync} from 'node:crypto';
import {mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {RelayStore} from '../src/store.js';

test('accounts, signed-device identities and token isolation persist; expiry rejects', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'relay-store-'));
  let now = 1000;
  let store = new RelayStore(join(dir, 'relay.sqlite'), {now: () => now, tokenTtlMs: 100});
  try {
    await store.createAccount('alice', 'long password alice');
    await store.createAccount('bob', 'long password bob');
    await assert.rejects(store.login('alice', 'wrong password'), /Invalid credentials/);
    const alice = await store.login('alice', 'long password alice');
    const bob = await store.login('bob', 'long password bob');
    const key = generateKeyPairSync('ed25519').publicKey.export({type: 'spki', format: 'pem'}).toString();
    const account = store.authenticate(alice.token);
    const device = store.addDevice(account, 'phone', key);
    const host = store.addHost(account, device.id);
    assert.equal(store.device(account, device.id).publicKey, key);
    assert.throws(() => store.device(store.authenticate(bob.token), device.id), /Device unavailable/);
    assert.throws(() => store.host(store.authenticate(bob.token), host.id), /Host unavailable/);
    store.close();
    store = new RelayStore(join(dir, 'relay.sqlite'), {now: () => now, tokenTtlMs: 100});
    assert.equal(store.host(store.authenticate(alice.token), host.id).deviceId, device.id);
    now = 1100;
    assert.throws(() => store.authenticate(alice.token), /expired/);
  } finally { store.close(); rmSync(dir, {recursive: true, force: true}); }
});

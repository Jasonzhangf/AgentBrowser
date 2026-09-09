import test from 'node:test';
import assert from 'node:assert/strict';
import {checkLock, parseEnvelope} from './validate.mjs';

test('schema lock matches the checked-in ABI without mutating it', () => {
  const checked = checkLock();
  assert.equal(checked.abi_id, 'agentbrowser-relay-v0');
  assert.match(checked.sha256, /^[0-9a-f]{64}$/);
});

test('control and data-plane envelopes are explicit and browser payload stays opaque', () => {
  const envelope = parseEnvelope({
    type: 'tunnel.offer',
    abi: 'agentbrowser-relay-v0',
    tunnelId: 'tun-1',
    hostId: 'host-1',
    sessionId: 'session-1',
    peerDeviceId: 'dev-2',
    side: 0,
    expiresAt: 123,
    channels: {
      control: {path: '/v2/tunnel/tun-1/control/0', ticket: 'control-ticket'},
      media: {path: '/v2/tunnel/tun-1/media/0', ticket: 'media-ticket'},
    },
  });
  assert.equal(envelope.type, 'tunnel.offer');
  const opaque = parseEnvelope({
    type: 'signal.send',
    abi: 'agentbrowser-relay-v0',
    peerDeviceId: 'dev-2',
    data: JSON.stringify({kind: 'sdp', browserOperation: {type: 'click', x: 1}}),
  });
  assert.match(opaque.data, /browserOperation/);
});

test('unknown fields and wrong ABI fail closed', () => {
  assert.throws(() => parseEnvelope({
    type: 'auth.ok', abi: 'agentbrowser-relay-v0', deviceId: 'dev-1', extra: true,
  }), /UNKNOWN_FIELD/);
  assert.throws(() => parseEnvelope({
    type: 'auth.ok', abi: 'other', deviceId: 'dev-1',
  }), /RELAY_ABI_ID_MISMATCH/);
  assert.throws(() => parseEnvelope({
    type: 'channel.ready', abi: 'agentbrowser-relay-v0', tunnelId: 'tun-1', channel: 'audio',
  }), /INVALID_CHANNEL/);
});

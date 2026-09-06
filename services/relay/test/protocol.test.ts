import {test} from 'node:test';
import assert from 'node:assert/strict';
import {hostRejectedTunnelReason, snapshot, tunnelRejectReason} from '../../../protocol/relay/index.js';

test('directory rejects unknown control fields, embedded tokens and duplicate sessions', () => {
  const value = {incarnation: 'boot', revision: 0, endpoints: [], sessions: [{id: 's1'}]};
  assert.deepEqual(snapshot(value), value);
  assert.throws(() => snapshot({...value, metadata: {route: 'direct'}}), /Unknown/);
  assert.throws(() => snapshot({...value, endpoints: [{network: 'tailscale', url: 'wss://host/?token=secret'}]}), /credentials/);
  assert.throws(() => snapshot({...value, sessions: [{id: 's1'}, {id: 's1'}]}), /Duplicate/);
});

test('tunnel rejection reason is a closed-set control value', () => {
  assert.equal(tunnelRejectReason('UNKNOWN_PEER'), 'UNKNOWN_PEER');
  assert.equal(tunnelRejectReason('CAPACITY'), 'CAPACITY');
  assert.equal(hostRejectedTunnelReason('UNKNOWN_PEER'), 'HOST_REJECTED_UNKNOWN_PEER');
  assert.throws(() => tunnelRejectReason('PEER_DISCONNECTED'), /Invalid tunnel rejection reason/);
});

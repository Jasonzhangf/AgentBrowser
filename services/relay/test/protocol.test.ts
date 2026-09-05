import {test} from 'node:test';
import assert from 'node:assert/strict';
import {snapshot} from '../../../protocol/relay/index.js';

test('directory rejects unknown control fields, embedded tokens and duplicate sessions', () => {
  const value = {incarnation: 'boot', revision: 0, endpoints: [], sessions: [{id: 's1'}]};
  assert.deepEqual(snapshot(value), value);
  assert.throws(() => snapshot({...value, metadata: {route: 'direct'}}), /Unknown/);
  assert.throws(() => snapshot({...value, endpoints: [{network: 'tailscale', url: 'wss://host/?token=secret'}]}), /credentials/);
  assert.throws(() => snapshot({...value, sessions: [{id: 's1'}, {id: 's1'}]}), /Duplicate/);
});

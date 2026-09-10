import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';

import {
  NETWORK_EVIDENCE_SCHEMA,
  loadNetworkPathEvidence,
  projectNetworkMatrix,
  validateNetworkPathEvidence,
  validateNetworkPathEvidenceSet,
} from './network-matrix-contract.mjs';

function completeRecord(overrides = {}) {
  const record = {
    schema: NETWORK_EVIDENCE_SCHEMA,
    path_id: 'local-direct',
    network_path: 'local',
    transport: 'IPC',
    session_id: 'session-1',
    generation: 3,
    identity: {account_id: 'alice', host_id: 'host-1', device_id: 'device-1'},
    signaling: {status: 'proved', evidence: ['signaling.log']},
    media: {
      status: 'proved',
      decoded_frames: 2,
      displayed: true,
      native_surface: true,
      evidence: ['frame.png'],
    },
    operation_receipts: [{
      operation_id: 'op-click-1',
      operation: 'click',
      generation: 3,
      session_id: 'session-1',
      outcome: 'applied',
      evidence: ['operation.json'],
    }],
    transition: {
      previous_generation: 2,
      current_generation: 3,
      switched: true,
      operation_replayed: false,
      stale_generation_drops: 1,
      generation_events: [{generation: 2, stale: true, applied: false}],
    },
    ...overrides,
  };
  if (overrides.session_id !== undefined && overrides.operation_receipts === undefined) {
    record.operation_receipts = record.operation_receipts.map(receipt => ({...receipt, session_id: record.session_id}));
  }
  return record;
}

function errorCode(action) {
  try {
    action();
  } catch (error) {
    return error.code;
  }
  return undefined;
}

test('canonical local, relay, WebRTC, and Tailscale records preserve path/transport separation', () => {
  const records = [
    completeRecord(),
    completeRecord({path_id: 'relay', network_path: 'relay', transport: 'WSS', session_id: 'session-relay', operation_receipts: [{operation_id: 'op-relay-1', operation: 'click', generation: 3, session_id: 'session-relay', outcome: 'applied', evidence: ['operation.json']}]}),
    completeRecord({path_id: 'udp-webrtc', network_path: 'public', transport: 'WebRTC', session_id: 'session-public', operation_receipts: [{operation_id: 'op-public-1', operation: 'click', generation: 3, session_id: 'session-public', outcome: 'applied', evidence: ['operation.json']}]}),
    completeRecord({
      path_id: 'tailscale-direct',
      network_path: 'tailscale',
      transport: 'WebRTC',
      session_id: 'session-tailscale',
      underlay: {status: 'unknown', reason: 'Tailscale control did not expose direct-vs-DERP route'},
      operation_receipts: [{operation_id: 'op-tailscale-1', operation: 'click', generation: 3, session_id: 'session-tailscale', outcome: 'applied', evidence: ['operation.json']}],
    }),
  ];
  const checked = validateNetworkPathEvidenceSet(records);
  assert.deepEqual(checked.map(item => item.result), ['PASS', 'PASS', 'PASS', 'PASS']);
  assert.equal(checked.at(-1).record.underlay.status, 'unknown');
  const rows = projectNetworkMatrix(records);
  assert.deepEqual(rows.map(row => row.path_id), ['local-direct', 'udp-webrtc', 'tailscale-direct', 'relay']);
  assert.deepEqual(rows.map(row => row.result), ['PASS', 'PASS', 'PASS', 'PASS']);
  assert.equal(rows.find(row => row.path_id === 'tailscale-direct').transport, 'WebRTC');
});

test('signaling-only evidence remains UNPROVEN', () => {
  const checked = validateNetworkPathEvidence({
    schema: NETWORK_EVIDENCE_SCHEMA,
    path_id: 'relay',
    network_path: 'relay',
    transport: 'WSS',
    session_id: 'session-signaling',
    signaling: {status: 'proved', evidence: ['signaling.log']},
    media: {status: 'unknown', reason: 'No decoded frame was observed', evidence: []},
    operation_receipts: [],
  });
  assert.equal(checked.result, 'UNPROVEN');
  assert.equal(checked.proof.signaling, true);
  assert.equal(checked.proof.media, false);
  assert.equal(checked.proof.operations, false);
});

test('transport acknowledgements cannot claim a displayed frame', () => {
  const record = completeRecord({
    result: 'UNPROVEN',
    media: {
      status: 'unknown',
      decoded_frames: 0,
      displayed: false,
      transport_acknowledgements: 3,
      reason: 'Only transport acknowledgements were observed',
      evidence: ['transport-ack.log'],
    },
    operation_receipts: [],
  });
  const checked = validateNetworkPathEvidence(record);
  assert.equal(checked.result, 'UNPROVEN');
  assert.equal(checked.proof.media, false);
  assert.match(checked.proof.reasons.join(' '), /acknowledgements do not prove/);
  assert.equal(errorCode(() => validateNetworkPathEvidence({...record, result: 'PASS'})), 'PASS_CLAIM_UNPROVEN');
});

test('duplicate operation ids are rejected across path records', () => {
  const first = completeRecord();
  const second = completeRecord({path_id: 'relay', network_path: 'relay', transport: 'WSS', session_id: 'session-2'});
  assert.equal(errorCode(() => validateNetworkPathEvidenceSet([first, second])), 'OPERATION_ID_DUPLICATE');
});

test('stale generation updates and replayed operations are rejected', () => {
  const stale = completeRecord({transition: {stale_generation_updates: 1}});
  assert.equal(errorCode(() => validateNetworkPathEvidence(stale)), 'STALE_GENERATION_UPDATE');
  const replayed = completeRecord({transition: {operation_replayed: true}});
  assert.equal(errorCode(() => validateNetworkPathEvidence(replayed)), 'OPERATION_REPLAYED');
});

test('cross-account evidence and path/transport mismatch fail closed', () => {
  const foreign = completeRecord({peer_identity: {account_id: 'bob'}});
  assert.equal(errorCode(() => validateNetworkPathEvidence(foreign)), 'CROSS_ACCOUNT_EVIDENCE');
  const wrongTransport = completeRecord({transport: 'WebRTC'});
  assert.equal(errorCode(() => validateNetworkPathEvidence(wrongTransport)), 'PATH_TRANSPORT_MISMATCH');
  const tailscaleUnderlayMissing = completeRecord({
    path_id: 'tailscale-direct',
    network_path: 'tailscale',
    transport: 'WSS',
    underlay: undefined,
  });
  assert.equal(errorCode(() => validateNetworkPathEvidence(tailscaleUnderlayMissing)), 'UNDERLAY_STATUS_MISSING');
});

test('evidence loader ignores unrelated replay JSON and validates contract records', () => {
  const root = mkdtempSync(join(tmpdir(), 'agentbrowser-network-contract-'));
  try {
    const record = completeRecord({
      path_id: 'relay',
      network_path: 'relay',
      transport: 'WSS',
      operation_receipts: [{operation_id: 'op-loader-1', operation: 'click', generation: 3, session_id: 'session-1', outcome: 'applied', evidence: ['operation.json']}],
      source: {root, files: ['signaling.log', 'frame.png', 'operation.json'], producer: 'contract-test'},
    });
    for (const name of ['signaling.log', 'frame.png', 'operation.json']) writeFileSync(join(root, name), 'evidence');
    writeFileSync(join(root, 'network-path-evidence.json'), JSON.stringify(record));
    writeFileSync(join(root, 'network-result.json'), JSON.stringify({frameAck: {displayed: true}}));
    const loaded = loadNetworkPathEvidence(root);
    assert.equal(loaded.records.length, 1);
    assert.equal(loaded.sources[0].path_id, 'relay');
    assert.equal(loaded.records[0].result, 'PASS');
  } finally {
    rmSync(root, {recursive: true, force: true});
  }
});

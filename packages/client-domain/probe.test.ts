import { test } from 'node:test';
import assert from 'node:assert/strict';
import { androidPort, parseSnapshot } from './probe';

const base = {
  state: 'idle' as const,
  generation: 0,
  renderedFrames: 0,
  released: true,
  codec: '',
  error: null,
};

test('missing native capability fails explicitly', () => assert.throws(() => androidPort(undefined), /UNAVAILABLE/));

test('malformed native response cannot become ready', () => {
  assert.throws(() => parseSnapshot('{"state":"playing"}'), /INVALID/);
  assert.throws(() => parseSnapshot('not json'));
});

test('only typed chrome commands cross the native port', () => {
  let request = '';
  const port = androidPort({ request(raw) { request = raw; return JSON.stringify(base); } });
  assert.deepEqual(port.request({op: 'status'}), base);
  assert.equal(request, '{"op":"status"}');
  port.request({op: 'navigate', epoch: 4, url: 'https://example.invalid/path?q=1'});
  assert.equal(request, '{"op":"navigate","epoch":4,"url":"https://example.invalid/path?q=1"}');
});

test('remote-owned chrome projections parse without inventing local state', () => {
  const snapshot = parseSnapshot(JSON.stringify({
    ...base,
    source: 'network',
    connectionState: 'connected',
    controlMode: 'control',
    networkConfigured: true,
    sessionId: 'session-1',
    inputReady: true,
    epoch: 7,
    documentRevision: 12,
    viewportRevision: 3,
    navigation: {url: 'https://example.com', title: 'Example', canGoBack: true, canGoForward: false, loading: false},
    tabs: {available: true, activeTabId: 'tab-1', items: [{id: 'tab-1', title: 'Example', url: 'https://example.com', active: true}]},
    profiles: {available: true, selectedProfileId: 'profile-1', items: [{id: 'profile-1', name: '默认', active: true}]},
    connectionSetup: {state: 'configured', error: null},
    capabilities: {navigation: true, tabs: true, profiles: true, connectionSetup: true, quality: true},
    videoQuality: {
      requested: {profile: 'high', lowLatency: true, generation: 2},
      effective: {profile: 'balanced', lowLatency: false, generation: 1},
      metrics: {rttMs: 18, jitterMs: null, lossPercent: 0, queueMs: null},
    },
  }));
  assert.equal(snapshot.navigation?.url, 'https://example.com');
  assert.equal(snapshot.tabs?.items[0].active, true);
  assert.equal(snapshot.profiles?.selectedProfileId, 'profile-1');
  assert.equal(snapshot.videoQuality?.requested.generation, 2);
  assert.equal(snapshot.videoQuality?.effective.generation, 1);
});

test('native rejection is preserved and unknown capability remains unavailable', () => {
  assert.throws(() => parseSnapshot('{"rejection":"HOST_REJECTED"}'), /HOST_REJECTED/);
  const snapshot = parseSnapshot(JSON.stringify({...base, source: 'network', connectionState: 'connected'}));
  assert.equal(snapshot.navigation, undefined);
  assert.equal(snapshot.tabs, undefined);
  assert.equal(snapshot.capabilities, undefined);
});

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

test('connectionSetup admits unresolved and unavailable typed projections', () => {
  const unresolved = parseSnapshot(JSON.stringify({
    ...base,
    source: 'network',
    connectionState: 'stopped',
    networkConfigured: true,
    connectionSetup: {state: 'unresolved', error: 'CONFIG_ACTIVE_COMMIT_UNRESOLVED', label: null, repair_import_allowed: false},
    capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
  }));
  assert.equal(unresolved.connectionSetup?.state, 'unresolved');
  assert.equal(unresolved.connectionSetup?.repair_import_allowed, false);

  const unavailable = parseSnapshot(JSON.stringify({
    ...base,
    source: 'network',
    connectionState: 'stopped',
    networkConfigured: true,
    connectionSetup: {state: 'unavailable', error: 'CONFIG_ACTIVE_REFERENCE_INVALID', label: 'Phone', repair_import_allowed: true, active_revision: 2},
    capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
  }));
  assert.equal(unavailable.connectionSetup?.label, 'Phone');
  assert.equal(unavailable.connectionSetup?.repair_import_allowed, true);
  assert.equal(unavailable.connectionSetup?.active_revision, 2);
});

test('connectionSetup rejects sensitive, overlength, control, and malformed fields', () => {
  for (const projection of [
    {state: 'configured', error: null, endpoint: 'wss://host'},
    {state: 'configured', error: null, transfer_code: 'SECRET'},
    {state: 'configured', error: null, path: '/private/active.json'},
    {state: 'configured', error: null, label: 'x'.repeat(65)},
    {state: 'configured', error: null, label: 'bad\nlabel'},
    {state: 'configured', error: null, label: '\u202Eevil'},
    {state: 'configured', error: null, repair_import_allowed: 'yes'},
    {state: 'configured', error: null, active_revision: -1},
  ]) {
    assert.throws(() => parseSnapshot(JSON.stringify({...base, connectionSetup: projection})), /INVALID/);
  }
});

test('retry and repair commands are the frozen native reconciliation names', () => {
  const commands: unknown[] = [];
  const port = androidPort({
    request(raw) {
      commands.push(JSON.parse(raw));
      return JSON.stringify(base);
    },
  });
  port.request({op: 'retry_connection_setup'});
  port.request({op: 'repair_connection_import'});
  assert.deepEqual(commands, [
    {op: 'retry_connection_setup'},
    {op: 'repair_connection_import'},
  ]);
});

test('native rejection is preserved and unknown capability remains unavailable', () => {
  assert.throws(() => parseSnapshot('{"rejection":"HOST_REJECTED"}'), /HOST_REJECTED/);
  const snapshot = parseSnapshot(JSON.stringify({...base, source: 'network', connectionState: 'connected'}));
  assert.equal(snapshot.navigation, undefined);
  assert.equal(snapshot.tabs, undefined);
  assert.equal(snapshot.capabilities, undefined);
});

const layout = {
  drawableWindowRect: {x: 0, y: 0, width: 390, height: 844},
  safeContentRect: {x: 0, y: 24, width: 390, height: 796},
  advertisedViewportRect: {x: 0, y: 80, width: 390, height: 740},
  presentedContentRect: {x: 0, y: 80, width: 390, height: 740},
  chromeRect: {x: 0, y: 24, width: 390, height: 56},
  webRect: {x: 0, y: 0, width: 390, height: 844},
  visibleContent: 'native-video' as const,
  displayedFrame: {
    generation: 1,
    sessionId: 'session-1',
    tabId: 'tab-1',
    documentRevision: 3,
    viewportRevision: 2,
  },
  currentTabId: 'tab-1',
  presentationViewport: {x: 0, y: 80, width: 390, height: 740},
  geometryMatches: true,
  presentation: 'browsing' as const,
  revision: 7,
};

test('layout projection parses only the accepted native-owned shape', () => {
  const snapshot = parseSnapshot(JSON.stringify({...base, layout}));
  assert.deepEqual(snapshot.layout, layout);
  assert.throws(() => parseSnapshot(JSON.stringify({
    ...base,
    layout: {...layout, drawableWindowRect: {x: -1, y: 0, width: 390, height: 844}},
  })), /INVALID/);
  assert.throws(() => parseSnapshot(JSON.stringify({
    ...base,
    layout: {...layout, visibleContent: 'video'},
  })), /INVALID/);
  assert.throws(() => parseSnapshot(JSON.stringify({
    ...base,
    layout: {...layout, displayedFrame: {...layout.displayedFrame, viewportRevision: -1}},
  })), /INVALID/);
});

test('set_presentation forwards the caller id and state and preserves applied/rejected receipts', () => {
  const commands: unknown[] = [];
  const applied = {
    id: 'presentation-1',
    op: 'set_presentation',
    status: 'applied',
    geometry: {webRect: {x: 0, y: 0, width: 390, height: 844}, revision: 8},
  };
  const port = androidPort({
    request(raw) {
      commands.push(JSON.parse(raw));
      return JSON.stringify(applied);
    },
  });
  assert.deepEqual(port.request({id: 'presentation-1', op: 'set_presentation', state: 'overlay'}), applied);
  assert.deepEqual(commands, [{id: 'presentation-1', op: 'set_presentation', state: 'overlay'}]);

  const rejected = {
    id: 'presentation-2',
    op: 'set_presentation',
    status: 'rejected',
    reason: 'NO_AUTHORITY',
  };
  const rejectedPort = androidPort({
    request() {
      return JSON.stringify(rejected);
    },
  });
  assert.deepEqual(rejectedPort.request({id: 'presentation-2', op: 'set_presentation', state: 'overlay'}), rejected);
});

test('presentation receipts reject mismatched ids and unknown rejection reasons', () => {
  const port = androidPort({
    request() {
      return JSON.stringify({
        id: 'other-id',
        op: 'set_presentation',
        status: 'applied',
        geometry: {webRect: {x: 0, y: 0, width: 1, height: 1}, revision: 1},
      });
    },
  });
  assert.throws(() => port.request({id: 'presentation-1', op: 'set_presentation', state: 'overlay'}), /INVALID/);

  const invalidReason = androidPort({
    request() {
      return JSON.stringify({
        id: 'presentation-1',
        op: 'set_presentation',
        status: 'rejected',
        reason: 'REMOTE_CONTROL_REQUIRED',
      });
    },
  });
  assert.throws(() => invalidReason.request({id: 'presentation-1', op: 'set_presentation', state: 'overlay'}), /INVALID/);
});

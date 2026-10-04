// Minimal public NativePort consumer for the built chrome UI.
// This is the native side of the public `androidPort`/`NativePort` boundary and
// holds no UI state; it only projects remote-owned snapshots and records the
// typed commands the chrome emits.
type HarnessScenario =
  | 'setup'
  | 'rejected'
  | 'configured'
  | 'connected'
  | 'connected-no-frame'
  | 'connected-video'
  | 'presentation-rejected'
  | 'presentation-open-status-error'
  | 'presentation-close-status-error'
  | 'waiting'
  | 'operation-pending'
  | 'control'
  | 'quality'
  | 'quality-fail'
  | 'command-reject'
  | 'status-recover'
  | 'tabs-missing-epoch'
  | 'tabs-valid'
  | 'tabs-context-revoke-on-send'
  | 'tabs-control-revoke-on-send'
  | 'tabs-capability-revoke-on-send'
  | 'tabs-status-error-on-send'
  | 'capability-missing'
  | 'capability-missing-setup'
  | 'capability-false'
  | 'unresolved-legacy-true'
  | 'unresolved-still'
  | 'configured-retry'
  | 'unavailable-repair'
  | 'unavailable-no-repair'
  | 'unavailable-repair-cancel'
  | 'configured-repair'
  | 'configured-repaired'
  | 'connected-unresolved'
  | 'connected-unavailable'
  | 'configured-long-label'
  | 'opening'
  | 'importing'
  | 'error-legacy';
type QualityProfile = 'low' | 'balanced' | 'high';
type ProbeCommand = Record<string, unknown> & {op: string};
type CapabilityMode = 'true' | 'missing' | 'false';
type PresentationState = 'browsing' | 'overlay';

const rect = {x: 0, y: 0, width: 390, height: 844};
const safeRect = {x: 0, y: 24, width: 390, height: 796};
const contentRect = {x: 0, y: 80, width: 390, height: 740};
const chromeRect = {x: 0, y: 24, width: 390, height: 56};

const state = {
  scenario: 'setup' as HarnessScenario,
  generation: 0,
  commands: [] as ProbeCommand[],
  presentation: 'browsing' as PresentationState,
  capabilityMode: 'true' as CapabilityMode,
  commandRejected: false,
  presentationRejected: false,
  presentationRevision: 7,
  presentationStatusBlocked: false,
  statusFailuresRemaining: 0,
  dropEpochOnNextStatus: false,
  revokeControlOnNextStatus: false,
  revokeCapabilityOnNextStatus: false,
  statusErrorsRemaining: 0,
  repairCancelledOnce: false,
};

// Request counter and public-boundary trace for black-box evidence.
type TraceEntry = {
  op: string;
  id?: string;
  state?: PresentationState;
  status?: string;
  revision?: number;
  webRect?: {x: number; y: number; width: number; height: number};
  reason?: string;
  error?: string;
};
const stats: {total: number; events: TraceEntry[]} = {total: 0, events: []};
(window as unknown as {__w4Stats: typeof stats}).__w4Stats = stats;

function qualityEntry(profile: QualityProfile, lowLatency: boolean, generation: number | null) {
  return {profile, lowLatency, generation};
}

function layout(visibleContent: 'webview' | 'native-video' = 'webview') {
  const nativeVideo = visibleContent === 'native-video';
  return {
    drawableWindowRect: rect,
    safeContentRect: safeRect,
    advertisedViewportRect: contentRect,
    presentedContentRect: contentRect,
    chromeRect,
    webRect: state.presentation === 'overlay' || !nativeVideo ? rect : chromeRect,
    visibleContent,
    displayedFrame: nativeVideo
      ? {
        generation: state.generation,
        sessionId: 'session-1',
        tabId: 'tab-1',
        documentRevision: 3,
        viewportRevision: 2,
      }
      : undefined,
    currentTabId: nativeVideo ? 'tab-1' : null,
    presentationViewport: contentRect,
    geometryMatches: nativeVideo,
    presentation: state.presentation,
    revision: state.presentationRevision,
  };
}

function connectedBase(visibleContent: 'webview' | 'native-video' = 'native-video') {
  return {
    state: 'playing',
    generation: state.generation,
    renderedFrames: 12,
    released: false,
    codec: 'H264',
    error: null,
    source: 'network',
    connectionState: 'connected',
    controlMode: 'observe',
    pending: null,
    networkConfigured: true,
    sessionId: 'session-1',
    inputReady: true,
    transport: 'wss',
    path: 'direct',
    epoch: 4,
    documentRevision: 3,
    viewportRevision: 2,
    displayedDocumentRevision: 3,
    displayedViewportRevision: 2,
    connectionSetup: {state: 'configured', error: null},
    capabilities: {navigation: true, tabs: true, profiles: true, connectionSetup: true, quality: true},
    navigation: {url: 'https://example.com', title: 'Example', canGoBack: true, canGoForward: false, loading: false},
    tabs: {available: true, activeTabId: 'tab-1', items: [{id: 'tab-1', title: 'Example', url: 'https://example.com', active: true}]},
    profiles: {available: true, selectedProfileId: 'profile-1', items: [{id: 'profile-1', name: '默认 Profile', active: true}]},
    videoQuality: {
      requested: qualityEntry('balanced', false, 1),
      effective: qualityEntry('low', false, 1),
      metrics: {rttMs: 18, jitterMs: 2, lossPercent: 0, queueMs: 0},
    },
    layout: layout(visibleContent),
  };
}

function stoppedBase() {
  return {
    state: 'idle',
    generation: state.generation,
    renderedFrames: 0,
    released: true,
    codec: '',
    error: null,
    source: 'network',
    connectionState: 'stopped',
    controlMode: 'observe',
    pending: null,
    networkConfigured: true,
  };
}

function withCapabilityMode<T extends Record<string, unknown>>(snapshot: T, mode: CapabilityMode): T {
  if (mode === 'true') return snapshot;
  if (mode === 'missing') {
    const next = {...snapshot};
    delete next.capabilities;
    return next;
  }
  return {
    ...snapshot,
    capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: false, quality: false},
  };
}

function snapshot() {
  switch (state.scenario) {
    case 'setup':
      return {
        state: 'idle',
        generation: state.generation,
        renderedFrames: 0,
        released: true,
        codec: '',
        error: null,
        source: 'network',
        connectionState: 'idle',
        controlMode: 'observe',
        pending: null,
        networkConfigured: false,
        connectionSetup: {state: 'required', error: null},
        capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
        layout: layout(),
      };
    case 'rejected':
      return {
        ...connectedBase(),
        state: 'error',
        released: true,
        error: 'HOST_REJECTED',
        connectionState: 'error',
        controlMode: 'observe',
        connectionSetup: {state: 'error', error: 'Host 拒绝连接'},
        layout: layout(),
      };
    case 'configured':
      return {
        state: 'idle',
        generation: state.generation,
        renderedFrames: 0,
        released: true,
        codec: '',
        error: null,
        source: 'network',
        connectionState: 'stopped',
        controlMode: 'observe',
        pending: null,
        networkConfigured: true,
        connectionSetup: {state: 'configured', error: null},
        capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
        layout: layout(),
      };
    case 'unresolved-legacy-true':
      return {
        state: 'idle',
        generation: state.generation,
        renderedFrames: 0,
        released: true,
        codec: '',
        error: null,
        source: 'network',
        connectionState: 'stopped',
        controlMode: 'observe',
        pending: null,
        networkConfigured: true,
        connectionSetup: {state: 'unresolved', error: 'CONFIG_ACTIVE_COMMIT_UNRESOLVED', label: null, repair_import_allowed: false},
        capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
        layout: layout(),
      };
    case 'unresolved-still':
      return {
        ...stoppedBase(),
        connectionSetup: {state: 'unresolved', error: 'CONFIG_ACTIVE_COMMIT_UNRESOLVED', label: null, repair_import_allowed: false},
        capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
        layout: layout(),
      };
    case 'configured-retry':
      return {
        state: 'idle',
        generation: state.generation,
        renderedFrames: 0,
        released: true,
        codec: '',
        error: null,
        source: 'network',
        connectionState: 'stopped',
        controlMode: 'observe',
        pending: null,
        networkConfigured: true,
        connectionSetup: {state: 'configured', error: null, label: 'Recovered phone', repair_import_allowed: false, active_revision: 4},
        capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
        layout: layout(),
      };
    case 'unavailable-repair':
      return {
        state: 'idle',
        generation: state.generation,
        renderedFrames: 0,
        released: true,
        codec: '',
        error: null,
        source: 'network',
        connectionState: 'stopped',
        controlMode: 'observe',
        pending: null,
        networkConfigured: true,
        connectionSetup: {state: 'unavailable', error: 'CONFIG_ACTIVE_REFERENCE_INVALID', label: 'Damaged phone', repair_import_allowed: true, active_revision: 2},
        capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
        layout: layout(),
      };
    case 'unavailable-no-repair':
      return {
        ...stoppedBase(),
        connectionSetup: {state: 'unavailable', error: 'CONFIG_ACTIVE_REFERENCE_INVALID', label: null, repair_import_allowed: false, active_revision: 2},
        capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
        layout: layout(),
      };
    case 'unavailable-repair-cancel':
      return {
        ...stoppedBase(),
        connectionSetup: {state: 'unavailable', error: 'CONFIG_ACTIVE_REFERENCE_INVALID', label: 'Cancelled phone', repair_import_allowed: true, active_revision: 2},
        capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
        layout: layout(),
      };
    case 'configured-repair':
      return {
        state: 'idle',
        generation: state.generation,
        renderedFrames: 0,
        released: true,
        codec: '',
        error: null,
        source: 'network',
        connectionState: 'stopped',
        controlMode: 'observe',
        pending: null,
        networkConfigured: true,
        connectionSetup: {state: 'configured', error: null, label: 'Configured phone', repair_import_allowed: false, active_revision: 3},
        capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
        layout: layout(),
      };
    case 'configured-repaired':
      return {
        state: 'idle',
        generation: state.generation,
        renderedFrames: 0,
        released: true,
        codec: '',
        error: null,
        source: 'network',
        connectionState: 'stopped',
        controlMode: 'observe',
        pending: null,
        networkConfigured: true,
        connectionSetup: {state: 'configured', error: null, label: 'Repaired phone', repair_import_allowed: false, active_revision: 6},
        capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
        layout: layout(),
      };
    case 'connected-unresolved':
      return {
        ...connectedBase(),
        connectionSetup: {state: 'unresolved', error: 'CONFIG_ACTIVE_COMMIT_UNRESOLVED', label: null, repair_import_allowed: false},
      };
    case 'connected-unavailable':
      return {
        ...connectedBase(),
        connectionSetup: {state: 'unavailable', error: 'CONFIG_ACTIVE_REFERENCE_INVALID', label: 'Current phone', repair_import_allowed: true, active_revision: 2},
      };
    case 'configured-long-label':
      return {
        state: 'idle',
        generation: state.generation,
        renderedFrames: 0,
        released: true,
        codec: '',
        error: null,
        source: 'network',
        connectionState: 'stopped',
        controlMode: 'observe',
        pending: null,
        networkConfigured: true,
        connectionSetup: {state: 'configured', error: null, label: `PhoneLabel${'x'.repeat(54)}`, repair_import_allowed: false, active_revision: 5},
        capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
        layout: layout(),
      };
    case 'opening':
      return {
        state: 'idle',
        generation: state.generation,
        renderedFrames: 0,
        released: true,
        codec: '',
        error: null,
        source: 'network',
        connectionState: 'stopped',
        controlMode: 'observe',
        pending: null,
        networkConfigured: false,
        connectionSetup: {state: 'opening', error: null, label: null, repair_import_allowed: false},
        capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
        layout: layout(),
      };
    case 'importing':
      return {
        state: 'idle',
        generation: state.generation,
        renderedFrames: 0,
        released: true,
        codec: '',
        error: null,
        source: 'network',
        connectionState: 'stopped',
        controlMode: 'observe',
        pending: null,
        networkConfigured: false,
        connectionSetup: {state: 'importing', error: null, label: null, repair_import_allowed: false},
        capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
        layout: layout(),
      };
    case 'error-legacy':
      return {
        state: 'idle',
        generation: state.generation,
        renderedFrames: 0,
        released: true,
        codec: '',
        error: null,
        source: 'network',
        connectionState: 'stopped',
        controlMode: 'observe',
        pending: null,
        networkConfigured: true,
        connectionSetup: {state: 'error', error: 'CONNECTION_SETUP_FAILED', label: null, repair_import_allowed: false},
        capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: true, quality: false},
        layout: layout(),
      };
    case 'connected-no-frame':
      return connectedBase('webview');
    case 'connected-video':
      return connectedBase();
    case 'presentation-rejected':
    case 'presentation-open-status-error':
    case 'presentation-close-status-error':
      return connectedBase();
    case 'waiting':
      return {...connectedBase(), controlMode: 'waiting', pending: 'navigate'};
    case 'operation-pending':
      return {...connectedBase(), pending: 'navigate'};
    case 'control':
      return {...connectedBase(), controlMode: 'control'};
    case 'quality':
    case 'quality-fail':
      return {
        ...connectedBase(),
        videoQuality: {
          requested: qualityEntry('high', true, 3),
          effective: qualityEntry('balanced', false, 2),
          metrics: {rttMs: 22, jitterMs: null, lossPercent: 1, queueMs: null},
        },
      };
    case 'command-reject':
      return {...connectedBase(), controlMode: 'control'};
    case 'status-recover':
      return {...connectedBase(), controlMode: 'control'};
    case 'tabs-missing-epoch': {
      const current = connectedBase();
      const {epoch: _epoch, ...withoutEpoch} = current;
      return {
        ...withoutEpoch,
        controlMode: 'control',
        tabs: {
          available: true,
          activeTabId: 'tab-1',
          items: [
            {id: 'tab-1', title: 'Example', url: 'https://example.com', active: true},
            {id: 'tab-2', title: 'Docs', url: 'https://example.com/docs', active: false},
          ],
        },
      };
    }
    case 'tabs-context-revoke-on-send':
    case 'tabs-control-revoke-on-send':
    case 'tabs-capability-revoke-on-send':
    case 'tabs-status-error-on-send':
      return {...connectedBase(), controlMode: 'control'};
    case 'tabs-valid':
      return {
        ...connectedBase(),
        controlMode: 'control',
        tabs: {
          available: true,
          activeTabId: 'tab-1',
          items: [
            {id: 'tab-1', title: 'Example', url: 'https://example.com', active: true},
            {id: 'tab-2', title: 'Docs', url: 'https://example.com/docs', active: false},
          ],
        },
      };
    case 'capability-missing':
      return {...connectedBase(), controlMode: 'control', capabilities: undefined};
    case 'capability-missing-setup':
      return {
        state: 'idle',
        generation: state.generation,
        renderedFrames: 0,
        released: true,
        codec: '',
        error: null,
        source: 'network',
        connectionState: 'stopped',
        controlMode: 'observe',
        pending: null,
        networkConfigured: false,
        connectionSetup: {state: 'required', error: null},
        profiles: {available: true, selectedProfileId: 'profile-1', items: [{id: 'profile-1', name: '默认 Profile', active: true}]},
        layout: layout(),
      };
    case 'capability-false':
      return {
        ...connectedBase(),
        controlMode: 'control',
        capabilities: {navigation: false, tabs: false, profiles: false, connectionSetup: false, quality: false},
      };
    case 'connected':
    default:
      return connectedBase();
  }
}

function applyCommand(command: ProbeCommand) {
  switch (command.op) {
    case 'set_presentation': {
      const next = command.state as PresentationState;
      if (state.scenario === 'presentation-rejected' && !state.presentationRejected) {
        state.presentationRejected = true;
        return {id: command.id, op: 'set_presentation', status: 'rejected', reason: 'NO_AUTHORITY'};
      }
      state.presentation = next;
      state.presentationRevision += 1;
      if (state.scenario === 'presentation-open-status-error'
          || state.scenario === 'presentation-close-status-error') {
        state.presentationStatusBlocked = true;
      }
      const current = snapshot() as {layout?: {webRect: typeof rect}};
      return {
        id: command.id,
        op: 'set_presentation',
        status: 'applied',
        geometry: {webRect: next === 'overlay' ? rect : current.layout?.webRect ?? rect, revision: state.presentationRevision},
      };
    }
    case 'connect':
      if (state.scenario === 'rejected') return snapshot();
      state.scenario = 'connected';
      state.generation += 1;
      return snapshot();
    case 'disconnect':
      if (state.scenario === 'connected-unresolved') state.scenario = 'unresolved-still';
      else if (state.scenario === 'connected-unavailable') state.scenario = 'unavailable-repair';
      else state.scenario = 'configured';
      state.generation += 1;
      return snapshot();
    case 'open_connection_setup':
    case 'import_connection':
    case 'repair_connection_import':
      if (state.scenario === 'unavailable-repair-cancel' && !state.repairCancelledOnce) {
        state.repairCancelledOnce = true;
        state.generation += 1;
        return snapshot();
      }
      if (['unavailable-repair', 'unavailable-repair-cancel', 'configured-repair'].includes(state.scenario)) {
        state.scenario = 'configured-repaired';
        state.generation += 1;
        return snapshot();
      }
      state.scenario = 'configured';
      state.generation += 1;
      return snapshot();
    case 'retry_connection_setup':
      if (state.scenario === 'unresolved-still') return snapshot();
      if (state.scenario === 'unresolved-legacy-true') {
        state.scenario = 'configured-retry';
        state.generation += 1;
        return snapshot();
      }
      if (state.scenario === 'configured-retry') return snapshot();
      return snapshot();
    case 'takeover':
      state.scenario = 'control';
      return snapshot();
    case 'release':
      state.scenario = 'connected';
      return snapshot();
    case 'navigate': {
      const current = snapshot() as ReturnType<typeof connectedBase>;
      return {...current, navigation: {...current.navigation, url: command.url, title: 'Loaded'}};
    }
    case 'reload':
      if (
        ['command-reject', 'tabs-valid', 'tabs-context-revoke-on-send',
          'tabs-control-revoke-on-send', 'tabs-capability-revoke-on-send', 'tabs-status-error-on-send']
          .includes(state.scenario) && !state.commandRejected
      ) {
        state.commandRejected = true;
        return {rejection: 'COMMAND_REJECTED'};
      }
      if (state.scenario === 'tabs-missing-epoch' && !state.commandRejected) {
        state.commandRejected = true;
        const current = connectedBase();
        const {epoch: _epoch, ...withoutEpoch} = current;
        return {...withoutEpoch, controlMode: 'control'};
      }
      return snapshot();
    case 'set_video_quality':
      if (state.scenario === 'quality-fail') return {rejection: 'QUALITY_REJECTED'};
      return {
        ...snapshot(),
        videoQuality: {
          requested: qualityEntry(command.profile as QualityProfile, command.lowLatency as boolean, 3),
          effective: qualityEntry('balanced', false, 2),
          metrics: {rttMs: 22, jitterMs: null, lossPercent: 1, queueMs: null},
        },
      };
    default:
      return snapshot();
  }
}

function request(command: ProbeCommand) {
  stats.total += 1;
  state.commands.push(command);
  if (command.op !== 'status') {
    window.parent.postMessage({type: 'command', command}, window.location.origin);
  }
  if (command.op === 'status' && state.presentationStatusBlocked) {
    stats.events.push({op: 'status', status: 'error', error: 'STATUS_FAILED'});
    throw new Error('STATUS_FAILED');
  }
  if (command.op === 'status' && state.scenario === 'status-recover' && state.statusFailuresRemaining > 0) {
    state.statusFailuresRemaining -= 1;
    return {rejection: 'STATUS_FAILED'};
  }
  if (command.op === 'status' && state.scenario === 'tabs-context-revoke-on-send' && state.dropEpochOnNextStatus) {
    state.dropEpochOnNextStatus = false;
    const {epoch: _epoch, ...withoutEpoch} = connectedBase();
    return {...withoutEpoch, controlMode: 'control'};
  }
  if (command.op === 'status' && state.scenario === 'tabs-control-revoke-on-send' && state.revokeControlOnNextStatus) {
    state.revokeControlOnNextStatus = false;
    return {...snapshot(), controlMode: 'observe'};
  }
  if (command.op === 'status' && state.scenario === 'tabs-capability-revoke-on-send' && state.revokeCapabilityOnNextStatus) {
    state.revokeCapabilityOnNextStatus = false;
    return {
      ...snapshot(),
      capabilities: {...snapshot().capabilities, tabs: false},
    };
  }
  if (command.op === 'status' && state.scenario === 'tabs-status-error-on-send' && state.statusErrorsRemaining > 0) {
    state.statusErrorsRemaining -= 1;
    throw new Error('STATUS_FAILED');
  }
  const applied = applyCommand(command) as Record<string, unknown>;
  const result = command.op === 'set_presentation'
    ? applied
    : withCapabilityMode(applied, state.capabilityMode);
  if (command.op === 'set_presentation') {
    const receipt = result as {
      id: string;
      status: string;
      geometry?: {webRect: {x: number; y: number; width: number; height: number}; revision: number};
      reason?: string;
    };
    stats.events.push({
      op: 'set_presentation',
      id: receipt.id,
      state: command.state as PresentationState,
      status: receipt.status,
      revision: receipt.geometry?.revision,
      webRect: receipt.geometry?.webRect,
      reason: receipt.reason,
    });
  }
  window.parent.postMessage({type: 'state', scenario: state.scenario}, window.location.origin);
  return result;
}

window.ProbeNative = {
  request(raw: string) {
    return JSON.stringify(request(JSON.parse(raw) as ProbeCommand));
  },
};

document.addEventListener('click', event => {
  if (state.scenario !== 'tabs-context-revoke-on-send') return;
  if (event.target instanceof Element && event.target.closest('#new-tab')) state.dropEpochOnNextStatus = true;
}, true);

document.addEventListener('click', event => {
  if (!(event.target instanceof Element) || !event.target.closest('#new-tab')) return;
  if (state.scenario === 'tabs-control-revoke-on-send') state.revokeControlOnNextStatus = true;
  if (state.scenario === 'tabs-capability-revoke-on-send') state.revokeCapabilityOnNextStatus = true;
  if (state.scenario === 'tabs-status-error-on-send') state.statusErrorsRemaining = 1;
}, true);

const app = document.createElement('script');
app.src = '/app.js';
app.onload = () => window.parent.postMessage({type: 'ready'}, window.location.origin);
document.body.append(app);

window.addEventListener('message', event => {
  if (event.origin !== window.location.origin) return;
  if (event.data?.type === 'scenario') {
    state.scenario = event.data.scenario as HarnessScenario;
    state.presentation = state.scenario === 'presentation-close-status-error' ? 'overlay' : 'browsing';
    state.capabilityMode = 'true';
    state.commandRejected = false;
    state.presentationRejected = false;
    state.presentationRevision = state.scenario === 'presentation-close-status-error' ? 8 : 7;
    state.presentationStatusBlocked = false;
    state.statusFailuresRemaining = state.scenario === 'status-recover' ? 1 : 0;
    state.dropEpochOnNextStatus = false;
    state.revokeControlOnNextStatus = false;
    state.revokeCapabilityOnNextStatus = false;
    state.statusErrorsRemaining = 0;
    state.repairCancelledOnce = false;
    state.commands.length = 0;
    stats.events.length = 0;
    state.presentation = 'browsing';
    (window as unknown as {__w4Scenario?: HarnessScenario}).__w4Scenario = state.scenario;
    return;
  }
  if (event.data?.type === 'presentation-status-recover') {
    state.presentationStatusBlocked = false;
    return;
  }
  if (event.data?.type === 'capabilities' && (event.data.mode === 'true' || event.data.mode === 'missing' || event.data.mode === 'false')) {
    state.capabilityMode = event.data.mode;
    state.commands.length = 0;
  }
});

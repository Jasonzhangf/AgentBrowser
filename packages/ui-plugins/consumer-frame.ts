// Minimal public NativePort consumer for the built chrome UI.
// This is the native side of the public `androidPort`/`NativePort` boundary and
// holds no UI state; it only projects remote-owned snapshots and records the
// typed commands the chrome emits.
type HarnessScenario =
  | 'setup'
  | 'rejected'
  | 'configured'
  | 'connected'
  | 'waiting'
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
  | 'capability-false';
type QualityProfile = 'low' | 'balanced' | 'high';
type ProbeCommand = Record<string, unknown> & {op: string};
type CapabilityMode = 'true' | 'missing' | 'false';

const state = {
  scenario: 'setup' as HarnessScenario,
  generation: 0,
  commands: [] as ProbeCommand[],
  capabilityMode: 'true' as CapabilityMode,
  commandRejected: false,
  statusFailuresRemaining: 0,
  dropEpochOnNextStatus: false,
  revokeControlOnNextStatus: false,
  revokeCapabilityOnNextStatus: false,
  statusErrorsRemaining: 0,
};

// Request counter for the dispose/reload subscription check.
const stats = {total: 0};
(window as unknown as {__w4Stats: {total: number}}).__w4Stats = stats;

function qualityEntry(profile: QualityProfile, lowLatency: boolean, generation: number | null) {
  return {profile, lowLatency, generation};
}

function connectedBase() {
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
      };
    case 'waiting':
      return {...connectedBase(), controlMode: 'waiting', pending: 'navigate'};
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
    case 'connect':
      if (state.scenario === 'rejected') return snapshot();
      state.scenario = 'connected';
      state.generation += 1;
      return snapshot();
    case 'open_connection_setup':
    case 'import_connection':
      state.scenario = 'configured';
      state.generation += 1;
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
  const result = withCapabilityMode(applyCommand(command) as Record<string, unknown>, state.capabilityMode);
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
    state.capabilityMode = 'true';
    state.commandRejected = false;
    state.statusFailuresRemaining = state.scenario === 'status-recover' ? 1 : 0;
    state.dropEpochOnNextStatus = false;
    state.revokeControlOnNextStatus = false;
    state.revokeCapabilityOnNextStatus = false;
    state.statusErrorsRemaining = 0;
    state.commands.length = 0;
    return;
  }
  if (event.data?.type === 'capabilities' && (event.data.mode === 'true' || event.data.mode === 'missing' || event.data.mode === 'false')) {
    state.capabilityMode = event.data.mode;
    state.commands.length = 0;
  }
});

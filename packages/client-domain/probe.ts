// Browser chrome/native port ABI. This is not the Obscura wire protocol.
export type ProbeState = 'idle' | 'starting' | 'playing' | 'stopping' | 'stopped' | 'completed' | 'error';
export type VideoQualityProfile = 'low' | 'balanced' | 'high';
export type PresentationState = 'browsing' | 'overlay';
export type PresentationRejectionReason = 'NO_AUTHORITY' | 'ACTIVITY_NOT_RESUMED' | 'GEOMETRY_UNAVAILABLE';

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface NativeDisplayedFrameIdentity {
  generation: number;
  sessionId: string;
  tabId: string;
  documentRevision: number;
  viewportRevision: number;
}

export interface NativeLayoutProjection {
  drawableWindowRect: Rect;
  safeContentRect: Rect;
  advertisedViewportRect: Rect;
  presentedContentRect: Rect;
  chromeRect: Rect;
  webRect: Rect;
  visibleContent: 'webview' | 'native-video';
  displayedFrame?: NativeDisplayedFrameIdentity;
  currentTabId: string | null;
  presentationViewport: Rect;
  geometryMatches: boolean;
  presentation: PresentationState;
  revision: number;
}

export type PresentationResult =
  | {
    id: string;
    op: 'set_presentation';
    status: 'applied';
    geometry: {webRect: Rect; revision: number};
  }
  | {
    id: string;
    op: 'set_presentation';
    status: 'rejected';
    reason: PresentationRejectionReason;
  };

export interface VideoQualityMetrics {
  rttMs: number | null;
  jitterMs: number | null;
  lossPercent: number | null;
  queueMs: number | null;
}

export interface VideoQualityProjection {
  requested: { profile: VideoQualityProfile; lowLatency: boolean; generation: number };
  effective: { profile: VideoQualityProfile; lowLatency: boolean; generation: number | null };
  metrics: VideoQualityMetrics;
}

export interface BrowserNavigationProjection {
  url: string;
  title: string | null;
  canGoBack: boolean;
  canGoForward: boolean;
  loading: boolean;
}

export interface BrowserTabProjection {
  id: string;
  title: string;
  url: string;
  active: boolean;
}

export interface BrowserTabsProjection {
  available: boolean;
  activeTabId: string | null;
  items: BrowserTabProjection[];
}

export interface BrowserProfileProjection {
  id: string;
  name: string;
  active: boolean;
}

export interface BrowserProfilesProjection {
  available: boolean;
  selectedProfileId: string | null;
  items: BrowserProfileProjection[];
}

export type ConnectionSetupState = 'unconfigured' | 'configured';

export interface ConnectionSetupProjection {
  state: ConnectionSetupState;
  error: string | null;
  label?: string | null;
}

export interface BrowserCapabilities {
  navigation: boolean;
  tabs: boolean;
  profiles: boolean;
  connectionSetup: boolean;
  quality: boolean;
}

export type ProbeCommand =
  | { op: 'status' }
  | { id: string; op: 'set_presentation'; state: PresentationState }
  | { op: 'connect' }
  | { op: 'disconnect' }
  | { op: 'takeover'; epoch: number }
  | { op: 'release'; epoch: number }
  | { op: 'navigate'; epoch: number; url: string }
  | { op: 'go_back'; epoch: number }
  | { op: 'go_forward'; epoch: number }
  | { op: 'reload'; epoch: number }
  | { op: 'new_tab'; epoch: number }
  | { op: 'select_tab'; epoch: number; tabId: string }
  | { op: 'close_tab'; epoch: number; tabId: string }
  | { op: 'open_connection_setup' }
  | { op: 'import_connection' }
  | { op: 'select_profile'; profileId: string }
  | { op: 'set_video_quality'; sessionId: string; generation: number; profile: VideoQualityProfile; lowLatency: boolean };

export type SnapshotCommand = {
  [Command in ProbeCommand as Command extends {op: 'set_presentation'} ? never : Command['op']]: Command;
}[Exclude<ProbeCommand['op'], 'set_presentation'>];
export type PresentationCommand = Extract<ProbeCommand, {op: 'set_presentation'}>;

export interface ProbeSnapshot {
  state: ProbeState;
  generation: number;
  renderedFrames: number;
  released: boolean;
  codec: string;
  error: string | null;
  source?: 'mp4' | 'annexb' | 'network';
  connectionState?: string;
  controlMode?: string;
  pending?: string | null;
  networkConfigured?: boolean;
  sessionId?: string;
  inputReady?: boolean;
  transport?: string;
  path?: string;
  epoch?: number;
  documentRevision?: number;
  viewportRevision?: number;
  displayedPtsUs?: number;
  displayedTicket?: number;
  displayedDocumentRevision?: number;
  displayedViewportRevision?: number;
  navigation?: BrowserNavigationProjection;
  tabs?: BrowserTabsProjection;
  profiles?: BrowserProfilesProjection;
  connectionSetup?: ConnectionSetupProjection;
  capabilities?: BrowserCapabilities;
  videoQuality?: VideoQualityProjection;
  layout?: NativeLayoutProjection;
}

export interface NativePort {
  request(command: SnapshotCommand): ProbeSnapshot;
  request(command: PresentationCommand): ProbeSnapshot | PresentationResult;
}

const probeStates: ProbeState[] = ['idle', 'starting', 'playing', 'stopping', 'stopped', 'completed', 'error'];
const qualityProfiles: VideoQualityProfile[] = ['low', 'balanced', 'high'];
const connectionSetupStates: ConnectionSetupState[] = ['unconfigured', 'configured'];
const connectionSetupKeys = new Set(['state', 'error', 'label']);
const presentationRejectionReasons: PresentationRejectionReason[] = ['NO_AUTHORITY', 'ACTIVITY_NOT_RESUMED', 'GEOMETRY_UNAVAILABLE'];

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function isNonNegativeNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

function validRect(value: unknown): value is Rect {
  return isRecord(value)
    && isNonNegativeNumber(value.x)
    && isNonNegativeNumber(value.y)
    && isNonNegativeNumber(value.width)
    && isNonNegativeNumber(value.height);
}

function validDisplayedFrame(value: unknown): value is NativeDisplayedFrameIdentity {
  return isRecord(value)
    && isNonNegativeInteger(value.generation)
    && typeof value.sessionId === 'string'
    && typeof value.tabId === 'string'
    && isNonNegativeInteger(value.documentRevision)
    && isNonNegativeInteger(value.viewportRevision);
}

function validLayout(value: unknown): value is NativeLayoutProjection {
  return isRecord(value)
    && validRect(value.drawableWindowRect)
    && validRect(value.safeContentRect)
    && validRect(value.advertisedViewportRect)
    && validRect(value.presentedContentRect)
    && validRect(value.chromeRect)
    && validRect(value.webRect)
    && (value.visibleContent === 'webview' || value.visibleContent === 'native-video')
    && (value.displayedFrame === undefined || validDisplayedFrame(value.displayedFrame))
    && (value.currentTabId === null || typeof value.currentTabId === 'string')
    && validRect(value.presentationViewport)
    && typeof value.geometryMatches === 'boolean'
    && (value.presentation === 'browsing' || value.presentation === 'overlay')
    && isNonNegativeInteger(value.revision);
}

function isNullableNonNegativeNumber(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isFinite(value) && value >= 0);
}

function isLossPercent(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100);
}

function validQualityEntry(value: unknown, nullableGeneration: boolean): boolean {
  if (!isRecord(value)) return false;
  if (!qualityProfiles.includes(value.profile as VideoQualityProfile) || typeof value.lowLatency !== 'boolean') return false;
  return nullableGeneration
    ? value.generation === null || isNonNegativeInteger(value.generation)
    : isNonNegativeInteger(value.generation);
}

function validQuality(value: unknown): value is VideoQualityProjection {
  if (!isRecord(value) || !isRecord(value.requested) || !isRecord(value.effective) || !isRecord(value.metrics)) return false;
  return validQualityEntry(value.requested, false)
    && validQualityEntry(value.effective, true)
    && isNullableNonNegativeNumber(value.metrics.rttMs)
    && isNullableNonNegativeNumber(value.metrics.jitterMs)
    && isLossPercent(value.metrics.lossPercent)
    && isNullableNonNegativeNumber(value.metrics.queueMs);
}

function validNavigation(value: unknown): value is BrowserNavigationProjection {
  return isRecord(value)
    && typeof value.url === 'string'
    && (value.title === null || typeof value.title === 'string')
    && typeof value.canGoBack === 'boolean'
    && typeof value.canGoForward === 'boolean'
    && typeof value.loading === 'boolean';
}

function validTab(value: unknown): value is BrowserTabProjection {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.title === 'string'
    && typeof value.url === 'string'
    && typeof value.active === 'boolean';
}

function validTabs(value: unknown): value is BrowserTabsProjection {
  return isRecord(value)
    && typeof value.available === 'boolean'
    && (value.activeTabId === null || typeof value.activeTabId === 'string')
    && Array.isArray(value.items)
    && value.items.length <= 64
    && value.items.every(validTab);
}

function validProfile(value: unknown): value is BrowserProfileProjection {
  return isRecord(value)
    && typeof value.id === 'string'
    && typeof value.name === 'string'
    && typeof value.active === 'boolean';
}

function validProfiles(value: unknown): value is BrowserProfilesProjection {
  return isRecord(value)
    && typeof value.available === 'boolean'
    && (value.selectedProfileId === null || typeof value.selectedProfileId === 'string')
    && Array.isArray(value.items)
    && value.items.length <= 32
    && value.items.every(validProfile);
}

const printableLabelPattern = /^[^\p{C}\p{Zl}\p{Zp}]{1,64}$/u;

function isPrintableLabel(value: unknown): value is string {
  return typeof value === 'string' && printableLabelPattern.test(value);
}

function validConnectionSetup(value: unknown): value is ConnectionSetupProjection {
  if (!isRecord(value)
      || !Object.keys(value).every(key => connectionSetupKeys.has(key))
      || !connectionSetupStates.includes(value.state as ConnectionSetupState)
      || !(value.error === null || typeof value.error === 'string')
      || !(value.label === undefined || value.label === null || isPrintableLabel(value.label))) {
    return false;
  }
  return true;
}

function validCapabilities(value: unknown): value is BrowserCapabilities {
  return isRecord(value)
    && typeof value.navigation === 'boolean'
    && typeof value.tabs === 'boolean'
    && typeof value.profiles === 'boolean'
    && typeof value.connectionSetup === 'boolean'
    && typeof value.quality === 'boolean';
}

export function parseSnapshot(raw: string): ProbeSnapshot {
  const value: unknown = JSON.parse(raw);
  if (isRecord(value) && typeof value.rejection === 'string') throw new Error(value.rejection);
  if (!isRecord(value)
      || !probeStates.includes(value.state as ProbeState)
      || !isNonNegativeInteger(value.generation)
      || !isNonNegativeInteger(value.renderedFrames)
      || typeof value.released !== 'boolean'
      || typeof value.codec !== 'string'
      || !(value.source === undefined || value.source === 'mp4' || value.source === 'annexb' || value.source === 'network')
      || !(value.connectionState === undefined || typeof value.connectionState === 'string')
      || !(value.controlMode === undefined || typeof value.controlMode === 'string')
      || !(value.pending === undefined || value.pending === null || typeof value.pending === 'string')
      || !(value.networkConfigured === undefined || typeof value.networkConfigured === 'boolean')
      || !(value.sessionId === undefined || typeof value.sessionId === 'string')
      || !(value.inputReady === undefined || typeof value.inputReady === 'boolean')
      || !(value.transport === undefined || typeof value.transport === 'string')
      || !(value.path === undefined || typeof value.path === 'string')
      || !(value.epoch === undefined || Number.isSafeInteger(value.epoch))
      || !(value.documentRevision === undefined || Number.isSafeInteger(value.documentRevision))
      || !(value.viewportRevision === undefined || Number.isSafeInteger(value.viewportRevision))
      || !(value.displayedPtsUs === undefined || Number.isSafeInteger(value.displayedPtsUs))
      || !(value.displayedTicket === undefined || Number.isSafeInteger(value.displayedTicket))
      || !(value.displayedDocumentRevision === undefined || Number.isSafeInteger(value.displayedDocumentRevision))
      || !(value.displayedViewportRevision === undefined || Number.isSafeInteger(value.displayedViewportRevision))
      || !(value.navigation === undefined || validNavigation(value.navigation))
      || !(value.tabs === undefined || validTabs(value.tabs))
      || !(value.profiles === undefined || validProfiles(value.profiles))
      || !(value.connectionSetup === undefined || validConnectionSetup(value.connectionSetup))
      || !(value.capabilities === undefined || validCapabilities(value.capabilities))
      || !(value.videoQuality === undefined || validQuality(value.videoQuality))
      || !(value.layout === undefined || validLayout(value.layout))
      || !(value.error === null || typeof value.error === 'string')) {
    throw new Error('INVALID_NATIVE_RESPONSE');
  }
  return value as unknown as ProbeSnapshot;
}

export function parsePresentationResult(raw: string): PresentationResult {
  const value: unknown = JSON.parse(raw);
  if (isRecord(value) && typeof value.rejection === 'string') throw new Error(value.rejection);
  if (!isRecord(value)
      || typeof value.id !== 'string'
      || value.op !== 'set_presentation') {
    throw new Error('INVALID_NATIVE_RESPONSE');
  }
  if (value.status === 'applied') {
    if (!isRecord(value.geometry)
        || !validRect(value.geometry.webRect)
        || !isNonNegativeInteger(value.geometry.revision)) {
      throw new Error('INVALID_NATIVE_RESPONSE');
    }
    return value as unknown as PresentationResult;
  }
  if (value.status === 'rejected'
      && presentationRejectionReasons.includes(value.reason as PresentationRejectionReason)) {
    return value as unknown as PresentationResult;
  }
  throw new Error('INVALID_NATIVE_RESPONSE');
}

export function isPresentationResult(value: ProbeSnapshot | PresentationResult): value is PresentationResult {
  return 'op' in value && value.op === 'set_presentation';
}

export function androidPort(bridge: { request(raw: string): string } | undefined): NativePort {
  if (!bridge) throw new Error('NATIVE_BRIDGE_UNAVAILABLE');
  return {
    request(command: ProbeCommand): ProbeSnapshot | PresentationResult {
      const raw = bridge.request(JSON.stringify(command));
      if (command.op === 'set_presentation') {
        const result = parsePresentationResult(raw);
        if (result.id !== command.id) throw new Error('INVALID_NATIVE_RESPONSE');
        return result;
      }
      return parseSnapshot(raw);
    },
  } as NativePort;
}

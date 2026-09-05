// Local media probe ABI. This is not the BrowserSession or relay protocol.
export type ProbeState = 'idle' | 'starting' | 'playing' | 'stopping' | 'stopped' | 'completed' | 'error';
export type ProbeCommand =
  | { op: 'play'; sample: 'portrait' | 'broken' } | { op: 'stop' } | { op: 'status' }
  | { op: 'connect' } | { op: 'disconnect' } | { op: 'observe' } | { op: 'takeover'; epoch: number } | { op: 'release'; epoch: number }
  | { op: 'click'; epoch: number; x: number; y: number } | { op: 'input_text'; epoch: number; text: string }
  | { op: 'scroll'; epoch: number; x: number; y: number; dx: number; dy: number };
export interface ProbeSnapshot {
  state: ProbeState; generation: number; renderedFrames: number; released: boolean;
  codec: string; error: string | null;
  source?: 'mp4' | 'annexb' | 'network'; connectionState?: string; controlMode?: string;
  pending?: string | null; networkConfigured?: boolean; sessionId?: string;
  inputReady?: boolean;
  epoch?: number; documentRevision?: number; viewportRevision?: number; displayedPtsUs?: number; displayedTicket?: number;
}
export interface NativePort { request(command: ProbeCommand): ProbeSnapshot }
export function parseSnapshot(raw: string): ProbeSnapshot {
  const value = JSON.parse(raw);
  if (typeof value?.rejection === 'string') throw new Error(value.rejection);
  if (!value || !['idle','starting','playing','stopping','stopped','completed','error'].includes(value.state)
      || !Number.isSafeInteger(value.generation) || value.generation < 0
      || !Number.isSafeInteger(value.renderedFrames) || value.renderedFrames < 0
      || typeof value.released !== 'boolean' || typeof value.codec !== 'string'
      || !(value.source === undefined || value.source === 'mp4' || value.source === 'annexb' || value.source === 'network')
      || !(value.connectionState === undefined || typeof value.connectionState === 'string')
      || !(value.controlMode === undefined || typeof value.controlMode === 'string')
      || !(value.pending === undefined || value.pending === null || typeof value.pending === 'string')
      || !(value.networkConfigured === undefined || typeof value.networkConfigured === 'boolean')
      || !(value.inputReady === undefined || typeof value.inputReady === 'boolean')
      || !(value.sessionId === undefined || typeof value.sessionId === 'string')
      || !(value.epoch === undefined || Number.isSafeInteger(value.epoch))
      || !(value.documentRevision === undefined || Number.isSafeInteger(value.documentRevision))
      || !(value.viewportRevision === undefined || Number.isSafeInteger(value.viewportRevision))
      || !(value.displayedPtsUs === undefined || Number.isSafeInteger(value.displayedPtsUs))
      || !(value.displayedTicket === undefined || Number.isSafeInteger(value.displayedTicket))
      || !(value.error === null || typeof value.error === 'string')) throw new Error('INVALID_NATIVE_RESPONSE');
  return value;
}
export function androidPort(bridge: { request(raw: string): string } | undefined): NativePort {
  if (!bridge) throw new Error('NATIVE_BRIDGE_UNAVAILABLE');
  return { request(command) { return parseSnapshot(bridge.request(JSON.stringify(command))); } };
}

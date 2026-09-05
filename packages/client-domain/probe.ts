// Local media probe ABI. This is not the BrowserSession or relay protocol.
export type ProbeState = 'idle' | 'starting' | 'playing' | 'stopping' | 'stopped' | 'completed' | 'error';
export type ProbeCommand = { op: 'play'; sample: 'portrait' | 'broken' } | { op: 'stop' } | { op: 'status' };
export interface ProbeSnapshot {
  state: ProbeState; generation: number; renderedFrames: number; released: boolean;
  codec: string; error: string | null;
}
export interface NativePort { request(command: ProbeCommand): ProbeSnapshot }
export function parseSnapshot(raw: string): ProbeSnapshot {
  const value = JSON.parse(raw);
  if (typeof value?.rejection === 'string') throw new Error(value.rejection);
  if (!value || !['idle','starting','playing','stopping','stopped','completed','error'].includes(value.state)
      || !Number.isSafeInteger(value.generation) || value.generation < 0
      || !Number.isSafeInteger(value.renderedFrames) || value.renderedFrames < 0
      || typeof value.released !== 'boolean' || typeof value.codec !== 'string'
      || !(value.error === null || typeof value.error === 'string')) throw new Error('INVALID_NATIVE_RESPONSE');
  return value;
}
export function androidPort(bridge: { request(raw: string): string } | undefined): NativePort {
  if (!bridge) throw new Error('NATIVE_BRIDGE_UNAVAILABLE');
  return { request(command) { return parseSnapshot(bridge.request(JSON.stringify(command))); } };
}

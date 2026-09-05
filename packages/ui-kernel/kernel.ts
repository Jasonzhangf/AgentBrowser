import { Context } from '@cordisjs/core';
import type { NativePort } from '../client-domain/probe';

declare module '@cordisjs/core' { interface Context { probe: NativePort } }

export async function createKernel(port: NativePort, mount: (ctx: Context) => void) {
  if (!port) throw new Error('REQUIRED_PROBE_PROVIDER_MISSING');
  const ctx = new Context();
  ctx.provide('probe', port);
  let mounted = false;
  const plugin = { name: 'local-media-probe', inject: ['probe'], apply(scope: Context) {
    mount(scope);
    mounted = true;
    scope.on('dispose', () => { mounted = false; });
  } };
  ctx.plugin(plugin);
  await ctx.start();
  if (!mounted) { await ctx.stop(); throw new Error('REQUIRED_PROBE_PLUGIN_FAILED'); }
  return { async dispose() { await ctx.stop(); }, get mounted() { return mounted; } };
}

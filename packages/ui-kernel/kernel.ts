import { Context } from '@cordisjs/core';
import type { NativePort } from '../client-domain/probe';
import type { AccountPort } from '../client-domain/account-directory';

declare module '@cordisjs/core' { interface Context { probe: NativePort; accountDirectory: AccountPort } }

export async function createKernel(port: NativePort, accountDirectory: AccountPort, mount: (ctx: Context) => void) {
  if (!port) throw new Error('REQUIRED_PROBE_PROVIDER_MISSING');
  if (!accountDirectory) throw new Error('REQUIRED_ACCOUNT_DIRECTORY_PROVIDER_MISSING');
  const ctx = new Context();
  ctx.provide('probe', port);
  ctx.provide('accountDirectory', accountDirectory);
  ctx.plugin({ name: 'account-directory', inject: ['accountDirectory'], apply() {} });
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

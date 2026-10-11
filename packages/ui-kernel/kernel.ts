import { Context } from '@cordisjs/core';
import type { NativePort } from '../client-domain/probe';

declare module '@cordisjs/core' {
  interface Context {
    probe: NativePort;
  }
}

export async function createKernel(port: NativePort, mount: (ctx: Context) => void) {
  const global = globalThis as typeof globalThis & {__kernelTrace?: string[]};
  global.__kernelTrace = [];
  const trace = (message: string) => {
    global.__kernelTrace!.push(message);
    console.error(`UI_KERNEL ${message}`);
  };
  trace(`provider=${Boolean(port)}`);
  if (!port) throw new Error('REQUIRED_PROBE_PROVIDER_MISSING');
  const ctx = new Context();
  ctx.provide('probe', port);
  trace('probe_provided');
  let mounted = false;
  const plugin = {
    name: 'browser-chrome',
    inject: ['probe'],
    apply(scope: Context) {
      trace('plugin_apply');
      mount(scope);
      mounted = true;
      trace('mount_reached');
      scope.on('dispose', () => {
        mounted = false;
      });
    },
  };
  try {
    ctx.plugin(plugin);
    trace('plugin_registered');
    await ctx.start();
    trace(`started mounted=${mounted}`);
  } catch (error) {
    trace(`error=${String(error)}`);
    throw error;
  }

  if (!mounted) {
    await ctx.stop();
    throw new Error('REQUIRED_PROBE_PLUGIN_FAILED');
  }
  return { async dispose() { await ctx.stop(); }, get mounted() { return mounted; } };
}

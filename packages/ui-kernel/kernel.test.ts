import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createKernel } from './kernel';
import type { NativePort } from '../client-domain/probe';
test('Cordis injected provider mounts once and disposes subscription once', async () => {
  let mounted = 0, disposed = 0;
  let calls = 0;
  const port: NativePort = { request() { calls++; return {state:'idle',generation:0,renderedFrames:0,released:true,codec:'',error:null}; } };
  const kernel = await createKernel(port, ctx => {
    assert.equal(ctx.probe.request({op:'status'}).state, 'idle');
    mounted++;
    ctx.on('dispose', () => { disposed++; });
  });
  assert.equal(kernel.mounted, true);
  await kernel.dispose();
  assert.equal(kernel.mounted, false);
  assert.equal(mounted, 1);
  assert.equal(disposed, 1);
  assert.equal(calls, 1);
});

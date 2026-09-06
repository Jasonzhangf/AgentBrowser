import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createKernel } from './kernel';
import type { NativePort } from '../client-domain/probe';
import type { AccountPort } from '../client-domain/account-directory';
test('Cordis injected provider mounts once and disposes subscription once', async () => {
  let mounted = 0, disposed = 0;
  let calls = 0;
  const port: NativePort = { request() { calls++; return {state:'idle',generation:0,renderedFrames:0,released:true,codec:'',error:null}; } };
  const accountDirectory: AccountPort = { request() { return {accountState:'signed_out',generation:0,pending:false,error:null,expiresAtMs:0,deviceId:null,directoryState:'empty',hosts:[]}; } };
  const kernel = await createKernel(port, accountDirectory, ctx => {
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

import {test} from 'node:test';
import {spawn} from 'node:child_process';
import {mkdirSync, writeFileSync} from 'node:fs';
import {join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root = resolve(fileURLToPath(new URL('../../..', import.meta.url)));
const fixture = fileURLToPath(new URL('./account-fixture.mjs', import.meta.url));
const loader = join(root, 'services/relay/node_modules/tsx/dist/loader.mjs');
const evidenceDir = '/tmp/agentbrowser-m1-account-fixture-bind-20260909';

test('account fixture authenticates Host on bind host while advertising separately', async () => {
  mkdirSync(evidenceDir, {recursive: true});
  const child = spawn(process.execPath, ['--import', loader, fixture], {
    cwd: root,
    env: {
      ...process.env,
      ACCOUNT_RELAY_BIND_HOST: '100.66.1.82',
      ACCOUNT_RELAY_ADVERTISE_HOST: '127.0.0.1',
    },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const stdout = [];
  const stderr = [];
  child.stdout.on('data', chunk => stdout.push(chunk));
  child.stderr.on('data', chunk => stderr.push(chunk));

  let ready;
  try {
    ready = await new Promise((resolveReady, rejectReady) => {
      let buffer = '';
      const timer = setTimeout(() => rejectReady(new Error('Account Relay fixture startup deadline')), 30_000);
      child.stdout.on('data', chunk => {
        buffer += chunk.toString();
        const newline = buffer.indexOf('\n');
        if (newline < 0) return;
        clearTimeout(timer);
        resolveReady(JSON.parse(buffer.slice(0, newline)));
      });
      child.once('error', rejectReady);
      child.once('exit', (code, signal) => {
        rejectReady(new Error(`Account Relay fixture exited before ready: code=${code} signal=${signal}`));
      });
    });
  } catch (error) {
    if (child.exitCode === null) {
      child.kill('SIGTERM');
      if (child.exitCode === null) await new Promise(resolveExit => child.once('exit', resolveExit));
    }
    throw error;
  }

  try {
    assert.equal(ready.event, 'ready');
    assert.equal(new URL(ready.origin).hostname, '127.0.0.1');
    assert.equal(new URL(ready.controlUrl).hostname, '127.0.0.1');
    assert.equal(child.exitCode, null);
  } finally {
    child.stdin.end('{"command":"shutdown"}\n');
    await new Promise((resolveExit, rejectExit) => {
      const timer = setTimeout(() => rejectExit(new Error('Account Relay fixture shutdown deadline')), 10_000);
      child.once('exit', (code, signal) => {
        clearTimeout(timer);
        if (code !== 0) {
          rejectExit(new Error(`Account Relay fixture shutdown failed: code=${code} signal=${signal}`));
          return;
        }
        resolveExit();
      });
    });
  }

  writeFileSync(join(evidenceDir, 'account-fixture-bind-regression.stdout'), Buffer.concat(stdout));
  writeFileSync(join(evidenceDir, 'account-fixture-bind-regression.stderr'), Buffer.concat(stderr));
  writeFileSync(join(evidenceDir, 'account-fixture-bind-regression.json'), `${JSON.stringify({
    bindHost: '100.66.1.82',
    advertiseHost: '127.0.0.1',
    ready,
    shutdown: 'exit-0',
  }, null, 2)}\n`);
});

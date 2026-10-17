// Real replay of the compiled client-connection acceptance consumers.
// This adapter builds the module, then runs the exact emitted consumers against
// a real Host/media/endpoint fixture. It writes no lifecycle records and binds
// no artifact, candidate or external-input identities.
import {spawnSync} from 'node:child_process';
import {mkdirSync, writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';

const moduleId = 'client-connection';
function run(program, args, log) {
  const result = spawnSync(program, args, {maxBuffer: 32 * 1024 * 1024, timeout: 120000});
  if (log) writeFileSync(log, Buffer.concat([result.stdout ?? Buffer.alloc(0), result.stderr ?? Buffer.alloc(0)]));
  assert(!result.error && result.status === 0, `${program} failed: ${result.error ?? result.stderr}`);
  return result.stdout;
}
const git = (...args) => run('git', args).toString().trim();
assert(!['main', 'master'].includes(git('branch', '--show-current')), 'Owner branch required');
assert.equal(git('status', '--porcelain'), '', 'Commit the complete candidate before replay');
assert(process.env.OBSCURA_PROTOCOL_ROOT && process.env.OBSCURA_BIN_DIR && process.env.AGENTBROWSER_RELAY_HOST_BIN,
  'Explicit protocol, Obscura binaries, and relay-host adapter required');
const directory = `evidence/connection-replay-${git('rev-parse', '--short=12', 'HEAD')}-${Date.now()}`;
mkdirSync(directory, {recursive: true});

run('appsdk', ['compile-module', '--module', moduleId], `${directory}/compile.log`);
const output = `generated/modules/${moduleId}/lib`;
run('python3', ['scripts/connection.py', 'test'], `${directory}/whitebox.log`);
const replays = [
  ['direct', 'connection-acceptance', 'real_host_observe_input_and_reconnect', []],
  ['webrtc', 'webrtc-acceptance', 'real_host_webrtc_observe_takeover_input_and_reconnect', []],
  ['relay', 'relay-acceptance', 'authenticated_relay_directory_tunnel_isolated_and_generation_fenced', []],
  ['relay-reject', 'relay-acceptance', 'host_role_rejects_offer_with_typed_reason_and_next_tunnel_succeeds', []],
  ['relay-connection', 'relay-connection-acceptance', 'real_relay_connection_uses_shared_pump_and_reconnects', ['--ignored']],
];
for (const [name, consumer, acceptanceTest, extra] of replays) {
  const result = run(`${output}/${consumer}`, [...extra, '--exact', acceptanceTest, '--nocapture'],
    `${directory}/${name}-blackbox.log`);
  assert.match(result.toString(), /test result: ok\. 1 passed; 0 failed/,
    `${name} consumer must execute its real acceptance test`);
}
console.log(JSON.stringify({directory, replays: replays.map(([name]) => name), result: 'pass'}, null, 2));

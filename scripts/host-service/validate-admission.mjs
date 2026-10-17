// Real compiled-bundle install, launchd restart and Host attachment receipts.
// This adapter binds no candidate, artifact or external-input identity and
// writes no AppSDK lifecycle records.
import {spawnSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {mkdirSync, mkdtempSync, appendFileSync, existsSync, copyFileSync, chmodSync, writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {createConnection} from 'node:net';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';

const moduleId = 'host-service';
const now = () => new Date().toISOString();
function run(program, args, log) {
  const result = spawnSync(program, args, {maxBuffer: 32 * 1024 * 1024, timeout: 120000});
  if (log) writeFileSync(log, Buffer.concat([result.stdout ?? Buffer.alloc(0), result.stderr ?? Buffer.alloc(0)]));
  assert(!result.error && result.status === 0, `${program} failed (${result.status}): ${result.error ?? result.stderr}; ${result.stdout}`);
  return result.stdout;
}
function persist(path, value) { writeFileSync(path, JSON.stringify(value, null, 2) + '\n', {flag: 'wx'}); }
const git = (...args) => run('git', args).toString().trim();
assert.equal(process.platform, 'darwin', 'Real user launchd required');
assert(!['main', 'master'].includes(git('branch', '--show-current')), 'Owner branch required');
assert.equal(git('status', '--porcelain'), '', 'Commit the complete candidate before replay');
assert(process.env.OBSCURA_HOST_BINARY, 'Explicit validated Host binary required');
const protocolText = process.env.OBSCURA_HOST_PROTOCOL_VERSION ?? '';
assert(/^[1-9][0-9]*$/.test(protocolText), 'Explicit positive u32 Host protocol version required');
const expectedProtocolVersion = Number(protocolText);
assert(Number.isSafeInteger(expectedProtocolVersion) && expectedProtocolVersion <= 0xffffffff,
  'Explicit positive u32 Host protocol version required');
const binary = resolve(process.env.OBSCURA_HOST_BINARY);
const directory = `evidence/host-service-replay-${git('rev-parse', '--short=12', 'HEAD')}-${Date.now()}`;
mkdirSync(directory, {recursive: true});
run('appsdk', ['compile-module', '--module', moduleId], `${directory}/compile.log`);
const compiled = `generated/modules/${moduleId}/lib`;
const files = ['host-service', 'host_service.py'];
const white = run('bash', ['scripts/host-service/verify-host-service'], `${directory}/whitebox.log`);
assert.match(white.toString(), /HOST_SERVICE_REAL_SUBPROCESS_PASS/);
// Install the compiled files separately; launchd never invokes workspace source.
const installed = resolve(directory, 'installed'); mkdirSync(installed, {mode: 0o700});
files.forEach(name => { copyFileSync(`${compiled}/${name}`, `${installed}/${name}`); chmodSync(`${installed}/${name}`, 0o700); });
const token = randomUUID().replaceAll('-', '');
const root = `/private/tmp/abhs-admit-${token}`;
const runtime = mkdtempSync('/private/tmp/abhr-');
const label = `com.agentbrowser.admission.${token}`, service = `gui/${process.getuid()}/${label}`;
const manager = (...args) => JSON.parse(run(`${installed}/host-service`, [...args, '--root', root]));
async function ready(previous) {
  let last;
  for (let attempt = 0; attempt < 100; attempt++) {
    const result = spawnSync(`${installed}/host-service`, ['status', '--root', root], {encoding: 'utf8', timeout: 10000});
    appendFileSync(`${directory}/readiness.jsonl`, JSON.stringify({at: now(), status: result.status,
      stdout: result.stdout, stderr: result.stderr, error: result.error?.message}) + '\n');
    assert(!result.error && [0, 1].includes(result.status), 'Host status process failed');
    last = JSON.parse(result.stdout);
    // The manager deliberately exits 1 for the dead PID between kickstart and
    // replacement. Only that declared transient state may continue polling.
    assert.equal(result.status, last.state === 'stale' ? 1 : 0, JSON.stringify(last));
    if (last.state === 'running') {
      assert.equal(last.protocol_version, expectedProtocolVersion);
      assert(Number.isSafeInteger(last.pid) && last.pid > 0);
      assert.equal(typeof last.session_id, 'string');
      assert(last.session_id.length > 0);
      if (!previous || (last.pid !== previous.pid && last.session_id !== previous.session_id)) return last;
    } else assert(['stopped', 'starting', 'stale'].includes(last.state), JSON.stringify(last));
    await delay(100);
  }
  throw new Error(`Host readiness deadline: ${JSON.stringify(last)}`);
}
async function observe(state) {
  const replies = await new Promise((accept, reject) => {
    const socket = createConnection(`${state.socket_dir}/host.sock`);
    let pending = '', records = [];
    socket.setTimeout(5000, () => socket.destroy(new Error('Host reply deadline')));
    socket.on('error', reject);
    socket.on('close', hadError => {
      if (hadError || records.length !== 2 || pending.length !== 0) reject(new Error('Host closed without complete attachment receipt'));
      else accept(records);
    });
    socket.on('data', bytes => {
      try {
        pending += bytes.toString('utf8'); assert(pending.length < 1024 * 1024, 'Bounded Host reply');
        let end;
        while ((end = pending.indexOf('\n')) >= 0) {
          records.push(JSON.parse(pending.slice(0, end))); pending = pending.slice(end + 1);
          if (records.length === 1) socket.write('{"id":1,"command":{"type":"attach","mode":"observe"}}\n');
          assert(records.length <= 2, 'Unexpected Host reply');
          if (records.length === 2) socket.end();
        }
      } catch (error) { socket.destroy(); reject(error); }
    });
  });
  assert.equal(replies[0].type, 'ready'); assert.equal(replies[0].version, expectedProtocolVersion);
  assert.equal(replies[0].session_id, state.session_id);
  assert.equal(replies[1].id, 1); assert.equal(replies[1].type, 'result');
  assert.equal(replies[1].value.session_id, state.session_id);
  assert.equal(replies[1].value.mode, 'observe');
  assert(Number.isSafeInteger(replies[1].value.attachment_id));
  return replies;
}
let loadAttempted = false;
try {
  const installation = manager('install', '--binary', binary, '--runtime-root', runtime, '--service-label', label,
    '--protocol-version', protocolText);
  assert.equal(installation.launchd_loaded, false);
  persist(`${directory}/install.json`, {installation, installed});
  loadAttempted = true;
  run('launchctl', ['bootstrap', `gui/${process.getuid()}`, installation.launchd_plist], `${directory}/bootstrap.log`);
  const first = await ready(), attachment = await observe(first), detached = await ready();
  assert.equal(detached.pid, first.pid); assert.equal(detached.session_id, first.session_id);
  run('launchctl', ['kickstart', '-k', service], `${directory}/kickstart.log`);
  const restarted = await ready(first);
  assert.notEqual(restarted.pid, first.pid); assert.notEqual(restarted.session_id, first.session_id);
  persist(`${directory}/restart.json`, {first, restarted});
  const reattached = await observe(restarted);
  persist(`${directory}/blackbox.json`, {first, attachment, detached, restarted, reattached});
} finally {
  // A unique task label avoids touching any existing user service. Preserve
  // config and receipts; only the manager cleans its verified runtime/PID.
  if (loadAttempted) {
    const listed = spawnSync('launchctl', ['print', service], {encoding: 'utf8', timeout: 10000});
    assert(!listed.error, `Cannot establish launchd cleanup state: ${listed.error}`);
    if (listed.status === 0) run('launchctl', ['bootout', service], `${directory}/bootout.log`);
    else assert.match(listed.stderr, /Could not find service/, 'Unknown launchd cleanup outcome');
  }
  if (existsSync(`${root}/config.json`)) {
    const stopped = manager('stop', '--service-label', label);
    assert.equal(stopped.state, 'stopped'); persist(`${directory}/cleanup.json`, stopped);
  }
}
console.log(JSON.stringify({directory, result: 'pass'}, null, 2));

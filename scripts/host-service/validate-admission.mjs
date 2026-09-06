// Real compiled-bundle install, launchd restart and Host attachment receipts.
import {spawnSync} from 'node:child_process';
import {createHash, randomUUID} from 'node:crypto';
import {mkdirSync, mkdtempSync, readFileSync, writeFileSync, existsSync, copyFileSync, chmodSync} from 'node:fs';
import {hostname, userInfo} from 'node:os';
import {resolve} from 'node:path';
import {createConnection} from 'node:net';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';

const moduleId = 'host-service';
const hash = bytes => `sha256:${createHash('sha256').update(bytes).digest('hex')}`;
const now = () => new Date().toISOString();
function run(program, args, log) {
  const result = spawnSync(program, args, {maxBuffer: 32 * 1024 * 1024, timeout: 120000});
  if (log) writeFileSync(log, Buffer.concat([result.stdout ?? Buffer.alloc(0), result.stderr ?? Buffer.alloc(0)]));
  assert(!result.error && result.status === 0, `${program} failed: ${result.error ?? result.stderr}`);
  return result.stdout;
}
function persist(path, value) { writeFileSync(path, JSON.stringify(value, null, 2) + '\n', {flag: 'wx'}); }
const git = (...args) => run('git', args).toString().trim();
assert.equal(process.platform, 'darwin', 'Real user launchd required');
assert(!['main', 'master'].includes(git('branch', '--show-current')), 'Owner branch required');
assert.equal(git('status', '--porcelain'), '', 'Commit complete candidate before admission');
assert(process.env.OBSCURA_HOST_BINARY, 'Explicit validated Host binary required');
const binary = resolve(process.env.OBSCURA_HOST_BINARY), binaryHash = hash(readFileSync(binary));
const head = git('rev-parse', 'HEAD'), tree = git('rev-parse', 'HEAD^{tree}');
const base = git('merge-base', 'HEAD', 'origin/main'), prefix = head.slice(0, 12);
const paths = git('diff', '--name-only', base, head).split('\n').filter(Boolean);
assert(paths.length, 'Candidate delta required');
const scopeHash = hash(JSON.stringify(paths)), candidateTime = now();
const directory = `evidence/host-service-admission-${prefix}-${Date.now()}`;
const records = `.appsdk/records/evidence/${moduleId}`;
const candidatePath = `.appsdk/records/fix-candidate-record-${moduleId}.json`;
const validationPath = `.appsdk/records/pre-review-validation-record-${moduleId}.json`;
assert(!existsSync(candidatePath) && !existsSync(validationPath), 'Preserve existing admission graph');
mkdirSync(directory, {recursive: true}); mkdirSync(records, {recursive: true});
const identity = `${hostname()}/${userInfo().username}/${process.version}`;
const environment = `darwin/${process.arch}/gui:${process.getuid()}/${identity}`;
const entrypoint = 'host-service:launchd';
const whiteProducer = {adapter: 'scripts/host-service/validate-admission.mjs:subprocess', identity};
const blackProducer = {adapter: 'scripts/host-service/validate-admission.mjs:installed-launchd', identity};
run('appsdk', ['compile-module', '--module', moduleId], `${directory}/compile.log`);
const manifestPath = `generated/modules/${moduleId}/module.compiled.json`;
const manifestBytes = readFileSync(manifestPath), artifact = JSON.parse(manifestBytes);
const compiled = `generated/modules/${moduleId}/lib`;
const files = ['host-service', 'host_service.py'];
const hashes = files.map(name => hash(readFileSync(`${compiled}/${name}`)));
for (let i = 0; i < files.length; i++) assert(artifact.artifacts.some(a => a.path === files[i] && a.hash === hashes[i]));
const white = run('bash', ['scripts/host-service/verify-host-service'], `${directory}/whitebox.log`);
assert.match(white.toString(), /HOST_SERVICE_REAL_SUBPROCESS_PASS/);
const whiteTime = now();
// Install the compiled files separately; launchd never invokes workspace source.
const installed = resolve(directory, 'installed'); mkdirSync(installed, {mode: 0o700});
files.forEach(name => { copyFileSync(`${compiled}/${name}`, `${installed}/${name}`); chmodSync(`${installed}/${name}`, 0o700); });
assert.deepEqual(files.map(name => hash(readFileSync(`${installed}/${name}`))), hashes);
const token = randomUUID().replaceAll('-', '');
const root = `/private/tmp/abhs-admit-${token}`;
const runtime = mkdtempSync('/private/tmp/abhr-');
const label = `com.agentbrowser.admission.${token}`, service = `gui/${process.getuid()}/${label}`;
const manager = (...args) => JSON.parse(run(`${installed}/host-service`, [...args, '--root', root]));
async function ready(previous) {
  let last;
  for (let attempt = 0; attempt < 100; attempt++) {
    last = manager('status');
    if (last.state === 'running') {
      assert.equal(last.protocol_version, 4);
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
  assert.equal(replies[0].type, 'ready'); assert.equal(replies[0].version, 4);
  assert.equal(replies[0].session_id, state.session_id);
  assert.equal(replies[1].id, 1); assert.equal(replies[1].type, 'result');
  assert.equal(replies[1].value.session_id, state.session_id);
  assert.equal(replies[1].value.mode, 'observe');
  assert(Number.isSafeInteger(replies[1].value.attachment_id));
  return replies;
}
let installTime, restartTime, blackTime, loadAttempted = false;
try {
  const installation = manager('install', '--binary', binary, '--runtime-root', runtime, '--service-label', label);
  assert.equal(installation.launchd_loaded, false);
  assert.equal(`sha256:${installation.binary_sha256}`, binaryHash);
  persist(`${directory}/install.json`, {installation, installed, hashes}); installTime = now();
  loadAttempted = true;
  run('launchctl', ['bootstrap', `gui/${process.getuid()}`, installation.launchd_plist], `${directory}/bootstrap.log`);
  const first = await ready(), attachment = await observe(first), detached = await ready();
  assert.equal(detached.pid, first.pid); assert.equal(detached.session_id, first.session_id);
  run('launchctl', ['kickstart', '-k', service], `${directory}/kickstart.log`);
  const restarted = await ready(first);
  assert.notEqual(restarted.pid, first.pid); assert.notEqual(restarted.session_id, first.session_id);
  persist(`${directory}/restart.json`, {first, restarted}); restartTime = now();
  const reattached = await observe(restarted);
  persist(`${directory}/blackbox.json`, {first, attachment, detached, restarted, reattached}); blackTime = now();
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
assert.equal(git('rev-parse', 'HEAD'), head); assert.equal(git('status', '--porcelain'), '');
assert.equal(hash(readFileSync(binary)), binaryHash, 'External Host drift');
assert.deepEqual(readFileSync(manifestPath), manifestBytes, 'Compiled manifest drift');
assert.deepEqual(files.map(name => hash(readFileSync(`${compiled}/${name}`))), hashes);
assert.deepEqual(files.map(name => hash(readFileSync(`${installed}/${name}`))), hashes);
const ids = {white: `whitebox-${prefix}`, install: `install-${prefix}`, restart: `restart-${prefix}`, black: `blackbox-${prefix}`};
function evidence(id, phase, kind, time, producer, log, surface) {
  persist(`${records}/${id}.json`, {evidence_id: id, issue_id: moduleId, experiment_id: `${moduleId}-${prefix}`, phase, kind,
    source_commit: head, artifact_hash: artifact.artifact_hash, execution_surface: surface, environment_id: environment, entrypoint,
    scope: {module_id: moduleId, feature_id: moduleId, entrypoint}, producer, result: 'pass', created_at: time,
    expires_at: new Date(Date.now() + 86400000).toISOString(), input_hashes: [tree, binaryHash, ...hashes, hash(readFileSync(log))],
    scope_hash: scopeHash, raw_evidence: log});
}
evidence(ids.white, 'development_whitebox', 'gate', whiteTime, whiteProducer, `${directory}/whitebox.log`, 'development_whitebox');
evidence(ids.install, 'deployment_install', 'install', installTime, blackProducer, `${directory}/install.json`, 'deployed_blackbox');
evidence(ids.restart, 'deployment_restart', 'restart', restartTime, blackProducer, `${directory}/restart.json`, 'deployed_blackbox');
evidence(ids.black, 'deployed_blackbox', 'sample_replay', blackTime, blackProducer, `${directory}/blackbox.json`, 'deployed_blackbox');
persist(candidatePath, {fix_candidate_id: `candidate-${prefix}`, issue_id: moduleId, module_id: moduleId,
  worktree_id: git('rev-parse', '--show-toplevel'), base_commit: base, head_commit: head, tree_hash: tree,
  diff_hash: hash(run('git', ['diff', '--binary', base, head])), design_id: 'docs/host-service.md', owner: identity,
  scope_hash: scopeHash, changed_paths: paths, verification_evidence_ids: Object.values(ids), created_at: candidateTime});
persist(validationPath, {validation_id: `validation-${prefix}`, issue_id: moduleId, module_id: moduleId,
  fix_candidate_id: `candidate-${prefix}`, candidate_commit: head, candidate_tree_hash: tree, artifact_hash: artifact.artifact_hash,
  whitebox_producer: whiteProducer, whitebox_evidence_ids: [ids.white], blackbox_evidence_ids: [ids.black],
  deployment: {environment_id: environment, entrypoint, producer: blackProducer, install_receipt_id: ids.install,
    restart_receipt_id: ids.restart, observed_at: blackTime}, source_unchanged: true, result: 'pass', created_at: now()});
run('appsdk', ['verify', '--review-admission', '--module', moduleId], `${directory}/admission.log`);
console.log(JSON.stringify({candidate: head, artifactHash: artifact.artifact_hash, directory, admission: 'pass'}));

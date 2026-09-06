// Exact candidate/artifact replay. This adapter does not publish or freeze.
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdirSync, readFileSync, writeFileSync, existsSync} from 'node:fs';
import {hostname, userInfo} from 'node:os';
import {resolve, join} from 'node:path';
import assert from 'node:assert/strict';

const moduleId = 'client-connection';
const hash = bytes => `sha256:${createHash('sha256').update(bytes).digest('hex')}`;
const now = () => new Date().toISOString();
function run(program, args, log) {
  const result = spawnSync(program, args, {maxBuffer: 32 * 1024 * 1024, timeout: 120000});
  if (log) writeFileSync(log, Buffer.concat([result.stdout ?? Buffer.alloc(0), result.stderr ?? Buffer.alloc(0)]));
  assert(!result.error && result.status === 0, `${program} failed: ${result.error ?? result.stderr}`);
  return result.stdout;
}
const git = (...args) => run('git', args).toString().trim();
assert(!['main', 'master'].includes(git('branch', '--show-current')), 'Owner branch required');
assert.equal(git('status', '--porcelain'), '', 'Complete candidate commit required');
assert(process.env.OBSCURA_PROTOCOL_ROOT && process.env.OBSCURA_BIN_DIR, 'Explicit protocol and validated binaries required');
const protocol = resolve(process.env.OBSCURA_PROTOCOL_ROOT);
const binaries = resolve(process.env.OBSCURA_BIN_DIR);
const externalPaths = [join(protocol, 'Cargo.toml'), join(protocol, 'src/lib.rs'),
  join(protocol, '../../Cargo.toml'), ...['obscura-host', 'obscura-endpoint', 'obscura-media'].map(name => join(binaries, name))];
const externalHashes = externalPaths.map(path => hash(readFileSync(path)));
const head = git('rev-parse', 'HEAD'), tree = git('rev-parse', 'HEAD^{tree}');
const base = git('merge-base', 'HEAD', 'origin/main');
const paths = git('diff', '--name-only', base, head).split('\n').filter(Boolean);
assert(paths.length, 'Candidate has no delta');
const candidateTime = now(), scopeHash = hash(JSON.stringify(paths)), prefix = head.slice(0, 12);
const directory = `evidence/connection-admission-${prefix}-${Date.now()}`;
const records = `.appsdk/records/evidence/${moduleId}`;
const candidatePath = `.appsdk/records/fix-candidate-record-${moduleId}.json`;
const validationPath = `.appsdk/records/pre-review-validation-record-${moduleId}.json`;
assert(!existsSync(candidatePath) && !existsSync(validationPath), 'Preserve prior graph before a new admission');
mkdirSync(directory, {recursive: true}); mkdirSync(records, {recursive: true});
const identity = `${hostname()}/${userInfo().username}/${process.version}`;
const environment = `${process.platform}/${process.arch}/${identity}`;
const entrypoint = 'connection-acceptance:real_host_observe_input_and_reconnect';
const whiteProducer = {adapter: 'scripts/connection-admission.mjs:nextest', identity};
const blackProducer = {adapter: 'scripts/connection-admission.mjs:compiled-consumer', identity};
run('appsdk', ['compile-module', '--module', moduleId], `${directory}/compile.log`);
const artifactFile = `generated/modules/${moduleId}/module.compiled.json`;
const artifactBytes = readFileSync(artifactFile), artifact = JSON.parse(artifactBytes);
const output = `generated/modules/${moduleId}/lib`;
const artifactPaths = ['libagentbrowser_connection.rlib', 'connection-acceptance'].map(name => `${output}/${name}`);
const artifactHashes = artifactPaths.map(path => hash(readFileSync(path)));
for (const digest of artifactHashes) assert(artifact.artifacts.some(item => item.hash === digest), 'Compiled artifact identity missing');
run('python3', ['scripts/connection.py', 'test'], `${directory}/whitebox.log`);
const whiteTime = now();
run(`${output}/connection-acceptance`, ['--exact', 'real_host_observe_input_and_reconnect', '--nocapture'], `${directory}/blackbox.log`);
const blackTime = now();
assert.equal(git('rev-parse', 'HEAD'), head);
assert.equal(git('status', '--porcelain'), '', 'Source drift');
assert.deepEqual(artifactPaths.map(path => hash(readFileSync(path))), artifactHashes, 'Artifact drift');
assert.deepEqual(externalPaths.map(path => hash(readFileSync(path))), externalHashes, 'External owner drift');
assert.deepEqual(readFileSync(artifactFile), artifactBytes, 'Artifact record drift');
function persist(path, value) { writeFileSync(path, JSON.stringify(value, null, 2) + '\n', {flag: 'wx'}); }
persist(`${directory}/external-inputs.json`, externalPaths.map((path, i) => ({path, hash: externalHashes[i]})));
const ids = {white: `whitebox-${prefix}`, black: `blackbox-${prefix}`};
function evidence(id, phase, producer, time, log) {
  persist(`${records}/${id}.json`, {evidence_id: id, issue_id: moduleId, experiment_id: `${moduleId}-${prefix}`,
    phase, kind: phase === 'development_whitebox' ? 'gate' : 'sample_replay', source_commit: head,
    artifact_hash: artifact.artifact_hash, execution_surface: phase, environment_id: environment, entrypoint,
    scope: {module_id: moduleId, feature_id: moduleId, entrypoint}, producer, result: 'pass', created_at: time,
    expires_at: new Date(Date.now() + 86400000).toISOString(), input_hashes: [tree, ...artifactHashes, ...externalHashes, hash(readFileSync(log))],
    scope_hash: scopeHash, raw_evidence: log});
}
evidence(ids.white, 'development_whitebox', whiteProducer, whiteTime, `${directory}/whitebox.log`);
evidence(ids.black, 'deployed_blackbox', blackProducer, blackTime, `${directory}/blackbox.log`);
persist(candidatePath, {fix_candidate_id: `candidate-${prefix}`, issue_id: moduleId, module_id: moduleId,
  worktree_id: git('rev-parse', '--show-toplevel'), base_commit: base, head_commit: head, tree_hash: tree,
  diff_hash: hash(run('git', ['diff', '--binary', base, head])), design_id: 'docs/client-connection.md', owner: identity,
  scope_hash: scopeHash, changed_paths: paths, verification_evidence_ids: Object.values(ids), created_at: candidateTime});
persist(validationPath, {validation_id: `validation-${prefix}`, issue_id: moduleId, module_id: moduleId,
  fix_candidate_id: `candidate-${prefix}`, candidate_commit: head, candidate_tree_hash: tree, artifact_hash: artifact.artifact_hash,
  whitebox_producer: whiteProducer, whitebox_evidence_ids: [ids.white], blackbox_evidence_ids: [ids.black],
  deployment: {environment_id: environment, entrypoint, producer: blackProducer, observed_at: blackTime},
  source_unchanged: true, result: 'pass', created_at: now()});
run('appsdk', ['verify', '--review-admission', '--module', moduleId], `${directory}/admission.log`);
console.log(JSON.stringify({candidate: head, artifactHash: artifact.artifact_hash, directory, admission: 'pass'}));

// Project admission adapter: derives identities and receipts from real commands.
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, existsSync, copyFileSync } from 'node:fs';
import { hostname, userInfo } from 'node:os';
import assert from 'node:assert/strict';

const moduleId = 'android-probe';
const issueId = 'android-network';
const serial = process.env.ANDROID_SERIAL;
assert(serial, 'ANDROID_SERIAL must identify the authorized device');
assert(process.env.JAVA_HOME && process.env.ANDROID_HOME, 'JAVA_HOME and ANDROID_HOME required');
assert(process.env.OBSCURA_PROTOCOL_ROOT && process.env.OBSCURA_BIN_DIR && process.env.OBSCURA_ENDPOINT_BIND_IP, 'Explicit real protocol/Host fixture required');
const hash = value => `sha256:${createHash('sha256').update(value).digest('hex')}`;
const now = () => new Date().toISOString();
function command(program, args, log) {
  const result = spawnSync(program, args, {maxBuffer:64*1024*1024, timeout:300000});
  if (log) writeFileSync(log, Buffer.concat([result.stdout ?? Buffer.alloc(0), result.stderr ?? Buffer.alloc(0)]));
  assert(!result.error && result.status === 0, `${program} ${args.join(' ')} failed: ${result.error ?? result.stderr}`);
  return result.stdout;
}
const git = (...args) => command('git', args).toString().trim();
const head = git('rev-parse','HEAD');
assert(!['main','master'].includes(git('branch','--show-current')), 'Owner branch required');
assert.equal(git('status','--porcelain'), '', 'Commit the complete candidate before admission');
const tree = git('rev-parse','HEAD^{tree}');
const base = git('merge-base','HEAD','origin/main');
const paths = git('diff','--name-only',base,head).split('\n').filter(Boolean);
assert(paths.length, 'No candidate source delta');
const scopeHash = hash(JSON.stringify(paths));
const candidateTime = now();
const identity = `${hostname()}/${userInfo().username}/node-${process.version}`;
const whiteProducer = {adapter:'scripts/validate-android.mjs:host',identity};
const deviceProducer = {adapter:'scripts/validate-android.mjs:adb',identity:`${identity}/${serial}`};
const environment = `android:${serial}:${command('adb',['-s',serial,'shell','getprop','ro.build.fingerprint']).toString().trim()}`;
const entrypoint = 'com.agentbrowser.probe/.MainActivity';
const directory = `evidence/admission-${head.slice(0,12)}-${Date.now()}`;
mkdirSync(directory, {recursive:true});
const moduleDir = `.appsdk/records/evidence/${moduleId}`;
mkdirSync(moduleDir, {recursive:true});
const candidatePath = `.appsdk/records/fix-candidate-record-${moduleId}.json`;
const validationPath = `.appsdk/records/pre-review-validation-record-${moduleId}.json`;
// Never overwrite an existing admission graph with a second run's evidence.
assert(!existsSync(candidatePath) && !existsSync(validationPath), 'Admission records already exist; preserve them and use a new candidate evidence archive');

command('npm',['run','verify:ci'],`${directory}/whitebox.log`);
command('bash',['scripts/android.sh','assembleDebugAndroidTest'],`${directory}/test-apk.log`);
command('appsdk',['compile-module','--module',moduleId],`${directory}/module.log`);
command('appsdk',['compile'],`${directory}/compile.log`);
const artifactFile = `generated/modules/${moduleId}/module.compiled.json`;
const artifact = JSON.parse(readFileSync(artifactFile,'utf8'));
const apk = 'apps/android/app/build/outputs/apk/debug/app-debug.apk';
const apkHash = hash(readFileSync(apk));
assert.equal(hash(readFileSync(`generated/modules/${moduleId}/lib/app-debug.apk`)), apkHash);
assert.equal(artifact.artifacts[0].hash, apkHash);
const artifactHash = artifact.artifact_hash;
const whiteTime = now();
command('bash',['scripts/device.sh','install'],`${directory}/install.log`);
const installedPath = command('adb',['-s',serial,'shell','pm','path','com.agentbrowser.probe']).toString().trim();
assert.match(installedPath, /^package:\/data\/app\/[^\n]+\/base\.apk$/);
const installedHash = hash(command('adb',['-s',serial,'exec-out','cat',installedPath.slice(8)]));
assert.equal(installedHash, apkHash, 'Installed APK differs from candidate');
const installTime = now();
command('bash',['scripts/device.sh','restart'],`${directory}/restart.log`);
assert.match(readFileSync(`${directory}/restart.log`,'utf8'), /Status: ok/);
const restartTime = now();
command('bash',['scripts/device.sh','replay'],`${directory}/blackbox.log`);
const device = JSON.parse(readFileSync('evidence/device-result.json','utf8'));
const annex = JSON.parse(readFileSync('evidence/annexb-result.json','utf8'));
const network = JSON.parse(readFileSync('evidence/network-result.json','utf8'));
const dom = JSON.parse(readFileSync('evidence/network-dom.json','utf8'));
assert(network.networkFrames && network.observerTouchIgnored && network.takeoverPixels && network.reconnectPreservesDocument && network.backgroundRelease && network.staleCallbacksFenced);
assert.deepEqual(dom.dom, {clicked:1,text:'native-network-proof'});
assert.equal(network.sessionId,dom.session);
copyFileSync('evidence/network-result.json',`${directory}/network-result.json`);
copyFileSync('evidence/network-dom.json',`${directory}/network-dom.json`);
copyFileSync('evidence/network-screen.png',`${directory}/network-screen.png`);
copyFileSync('evidence/network-test.log',`${directory}/network-test.log`);
assert(annex.cropVerified && annex.generationRejected && annex.corruptDataRejected && annex.codedMismatchRejected && annex.surfaceRelease && annex.activityRelease && annex.inputLimitsRejected);
assert(annex.changedPixels>1000 && annex.visibleWidth===391 && annex.visibleHeight===845);
writeFileSync(`${directory}/annexb-result.json`,JSON.stringify(annex,null,2));
copyFileSync('evidence/annexb-before.png',`${directory}/annexb-before.png`);
copyFileSync('evidence/annexb-after.png',`${directory}/annexb-after.png`);
copyFileSync('evidence/annexb-coded.png',`${directory}/annexb-coded.png`);
copyFileSync('evidence/annexb-visible.png',`${directory}/annexb-visible.png`);
assert(device.surfacePixelCopy && device.stopRelease && device.badMediaError && device.cordisUnloadReload && device.activityStopRelease && device.naturalEosRelease && device.untrustedCommandsRejected);
assert(device.changedPixels > device.sampledPixels/50 && device.nonblackPixels > device.sampledPixels/4);
assert(device.completedFrames >= 350);
writeFileSync(`${directory}/device-result.json`, JSON.stringify(device,null,2));
copyFileSync('evidence/frame-a.png', `${directory}/frame-a.png`);
copyFileSync('evidence/frame-b.png', `${directory}/frame-b.png`);
const blackTime = now();
assert.equal(git('rev-parse','HEAD'),head);
assert.equal(git('status','--porcelain'),'', 'Source drift during validation');
assert.equal(hash(readFileSync(apk)), apkHash, 'Artifact drift during validation');
const prefix = head.slice(0,12);
const ids = {white:`whitebox-${prefix}`,install:`install-${prefix}`,restart:`restart-${prefix}`,black:`blackbox-${prefix}`};
function persist(path, value) { assert(!existsSync(path), `Refuse to overwrite ${path}`); writeFileSync(path,JSON.stringify(value,null,2)+'\n',{flag:'wx'}); }
function evidence(id, phase, kind, time, producer, log, surface) {
  const record = {evidence_id:id,issue_id:issueId,experiment_id:`${issueId}-${prefix}`,phase,kind,
    source_commit:head,artifact_hash:artifactHash,execution_surface:surface,environment_id:environment,entrypoint,
    scope:{module_id:moduleId,feature_id:issueId,entrypoint},producer,result:'pass',created_at:time,
    expires_at:new Date(Date.now()+24*60*60*1000).toISOString(),input_hashes:[tree,apkHash,hash(readFileSync(log))],scope_hash:scopeHash,
    raw_evidence:log,installed_apk_hash:installedHash};
  persist(`${moduleDir}/${id}.json`,record);
}
evidence(ids.white,'development_whitebox','gate',whiteTime,whiteProducer,`${directory}/whitebox.log`,'development_whitebox');
evidence(ids.install,'deployment_install','install',installTime,deviceProducer,`${directory}/install.log`,'deployed_blackbox');
evidence(ids.restart,'deployment_restart','restart',restartTime,deviceProducer,`${directory}/restart.log`,'deployed_blackbox');
evidence(ids.black,'deployed_blackbox','sample_replay',blackTime,deviceProducer,`${directory}/blackbox.log`,'deployed_blackbox');
persist(candidatePath,{fix_candidate_id:`candidate-${prefix}`,issue_id:issueId,module_id:moduleId,
  worktree_id:git('rev-parse','--show-toplevel'),base_commit:base,head_commit:head,tree_hash:tree,
  diff_hash:hash(command('git',['diff','--binary',base,head])),design_id:'docs/android-probe.md',owner:identity,
  scope_hash:scopeHash,changed_paths:paths,verification_evidence_ids:Object.values(ids),created_at:candidateTime});
persist(validationPath,{validation_id:`validation-${prefix}`,issue_id:issueId,module_id:moduleId,
  fix_candidate_id:`candidate-${prefix}`,candidate_commit:head,candidate_tree_hash:tree,artifact_hash:artifactHash,
  whitebox_producer:whiteProducer,whitebox_evidence_ids:[ids.white],blackbox_evidence_ids:[ids.black],
  deployment:{environment_id:environment,entrypoint,producer:deviceProducer,install_receipt_id:ids.install,restart_receipt_id:ids.restart,observed_at:now()},
  source_unchanged:true,result:'pass',created_at:now()});
command('appsdk',['verify','--review-admission','--module',moduleId],`${directory}/admission.log`);
console.log(JSON.stringify({candidate:head,apkHash,installedHash,artifactHash,directory,admission:'pass'},null,2));

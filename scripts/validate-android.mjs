// Real Android device replay: derive receipts from the installed product entry.
// This adapter binds no candidate, artifact or external-input identity and
// writes no AppSDK lifecycle records.
import { spawnSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync, existsSync, copyFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const moduleId = 'android-probe';
const serial = process.env.ANDROID_SERIAL;
assert(serial, 'ANDROID_SERIAL must identify the authorized device');
assert(process.env.JAVA_HOME && process.env.ANDROID_HOME, 'JAVA_HOME and ANDROID_HOME required');
assert(process.env.OBSCURA_PROTOCOL_ROOT && process.env.OBSCURA_BIN_DIR && process.env.OBSCURA_ENDPOINT_BIND_IP, 'Explicit real protocol/Host fixture required');
assert(process.env.ACCOUNT_RELAY_BIND_HOST && process.env.ACCOUNT_RELAY_ADVERTISE_HOST, 'Explicit real account Relay fixture bind and advertise hosts required');
const now = () => new Date().toISOString();
function command(program, args, log) {
  const result = spawnSync(program, args, {maxBuffer:64*1024*1024, timeout:300000});
  if (log) writeFileSync(log, Buffer.concat([result.stdout ?? Buffer.alloc(0), result.stderr ?? Buffer.alloc(0)]));
  assert(!result.error && result.status === 0, `${program} ${args.join(' ')} failed: ${result.error ?? result.stderr}`);
  return result.stdout;
}
const git = (...args) => command('git', args).toString().trim();
assert(!['main','master'].includes(git('branch','--show-current')), 'Owner branch required');
assert.equal(git('status','--porcelain'), '', 'Commit the complete candidate before replay');
const directory = `evidence/android-replay-${git('rev-parse','--short=12','HEAD')}-${Date.now()}`;
mkdirSync(directory, {recursive:true});

command('npm',['run','verify:ci'],`${directory}/whitebox.log`);
command('bash',['scripts/android.sh','assembleDebugAndroidTest'],`${directory}/test-apk.log`);
// Project compilation builds the declared connection dependency before Android.
command('appsdk',['compile'],`${directory}/compile.log`);
const apk = 'apps/android/app/build/outputs/apk/debug/app-debug.apk';
command('bash',['scripts/device.sh','install'],`${directory}/install.log`);
const installedPath = command('adb',['-s',serial,'shell','pm','path','com.agentbrowser.probe']).toString().trim();
assert.match(installedPath, /^package:\/data\/app\/[^\n]+\/base\.apk$/);
command('bash',['scripts/device.sh','restart'],`${directory}/restart.log`);
assert.match(readFileSync(`${directory}/restart.log`,'utf8'), /Status: ok/);
process.env.NETWORK_EVIDENCE_DIR = `${directory}/network`;
command('bash',['scripts/device.sh','replay'],`${directory}/blackbox.log`);
const device = JSON.parse(readFileSync('evidence/device-result.json','utf8'));
const annex = JSON.parse(readFileSync('evidence/annexb-result.json','utf8'));
const network = JSON.parse(readFileSync(`${directory}/network/network-result.json`,'utf8'));
const dom = JSON.parse(readFileSync(`${directory}/network/network-dom.json`,'utf8'));
const installedNetwork = JSON.parse(readFileSync(`${directory}/network/installed-apks.json`,'utf8'));
assert.equal(network.runId, installedNetwork.runId);
assert(network.networkFrames && network.observerTouchIgnored && network.takeoverPixels && network.reconnectPreservesDocument && network.backgroundRelease && network.staleCallbacksFenced);
assert(network.busyViewportCoalesced);
assert(network.hostRejectionKeepsConnection);
assert(network.displayedRevisionFenced);
assert(network.swipeDoesNotClick && network.scrollPixels);
assert(network.cancelledGestureIgnored && network.staleViewportGestureIgnored);
assert(network.rotationPreservesState);
assert(network.addressNavigation, 'Real address-bar navigation must reach a displayed Host document');
assert(network.keyboardVisible && network.compositionStarted && network.compositionSendDisabled);
assert(network.compositionCancelled && network.compositionCommitted && network.unfinishedCompositionDropped);
assert(network.disconnectCompositionCancelled);
assert(network.imeTextEvidence && network.imeTextPainted && network.chineseInkPixels > 30);
assert(existsSync(`${directory}/network/network-ime-text.png`));
for(const view of [network.landscapeViewport,network.portraitViewport]) {
  assert.equal(view.cssWidth,view.sourceWidth);
  assert.equal(view.cssHeight,view.sourceHeight);
}
assert(network.landscapeViewport.cssWidth>network.landscapeViewport.cssHeight);
assert(network.portraitViewport.cssWidth<network.portraitViewport.cssHeight);
assert(network.textPainted && network.inputGlyphsAfter > network.inputGlyphsBefore + 30);
assert.equal(network.viewport.cssWidth, network.viewport.sourceWidth);
assert.equal(network.viewport.cssHeight, network.viewport.sourceHeight);
const {maxScroll,...retainedDom}=dom.dom;
assert.deepEqual(retainedDom, {clicked:1,text:'native-network-proof中文',scrollY:0});
assert(Number.isFinite(maxScroll) && maxScroll>=240);
assert.equal(network.sessionId,dom.session);
process.env.ACCOUNT_EVIDENCE_DIR = `${directory}/account`;
command('bash',['scripts/device.sh','account-replay'],`${directory}/account-blackbox.log`);
const account = JSON.parse(readFileSync(`${directory}/account/summary.json`,'utf8'));
assert.equal(account.serial,serial);
assert.equal(account.model,'PLZ110');
const accountResults = Object.fromEntries(account.scenarios.map(item => [item.scenario,item.result]));
assert(accountResults.directory?.directoryOnline && accountResults.directory.directoryOffline && accountResults.directory.directoryExpired);
assert(accountResults.directory.wrongCaRejected && accountResults.directory.wrongCredentialsRejected && accountResults.directory.deviceRegistered);
assert(accountResults.directory.staleCallbacksFenced && accountResults.directory.closeReleasedHandle && accountResults.directory.closeFailureVisible);
assert.equal(accountResults.directory.accountStateAfterCloseFailure,'error');
assert(accountResults['revoke-failure']?.localLogout && accountResults['revoke-failure'].remoteRevokeUnconfirmed && accountResults['revoke-failure'].warningVisible);
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
console.log(JSON.stringify({directory, entrypoint:'com.agentbrowser.probe/.MainActivity', apk, result:'pass'},null,2));

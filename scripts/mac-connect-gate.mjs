// Mac client connect gate against the task6 lifecycle Host.
//
// This harness is fail-loud by construction: every step records its own receipt
// only after its own success is verified, and the run ends with either a typed
// PASS or the first failing gate plus raw evidence. It never runs a shell
// pipeline, so a backgrounded or skipped step cannot masquerade as a receipt.
//
// Scope: ordinary installed app + task6 lifecycle Host only. The LAN root and
// any other worker's Host are refused.
import {spawn, spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createWriteStream, existsSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync} from 'node:fs';
import {hostname, userInfo} from 'node:os';
import {join, resolve} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';

const root = resolve(process.cwd());
const serviceRoot = join(userInfo().homedir, 'Library/Application Support/AgentBrowser/host-service');
const launcher = join(root, 'scripts/host-service/host-service');
const appExecutable = join(root, 'apps/macos/build/AgentBrowserMac.app/Contents/MacOS/AgentBrowserMac');
const bridgeExecutable = join(root, 'apps/macos/build/AgentBrowserMac.app/Contents/MacOS/AgentBrowserMacBridge');
const pairingDirectory = process.env.AGENTBROWSER_MAC_PAIRING ?? '/tmp/abn-task6-mac-pairing';
const expectedHostBinary = process.env.MAC_GATE_HOST_BINARY ?? null;
const serviceLabel = 'com.agentbrowser.task6';
const observationSeconds = Number(process.env.MAC_GATE_OBSERVATION_SECONDS ?? '40');
const pollMilliseconds = 2_000;

const hash = bytes => `sha256:${createHash('sha256').update(bytes).digest('hex')}`;
const realPath = value => {
  try { return realpathSync(value); } catch { return resolve(value); }
};
const now = () => new Date().toISOString();
const sleep = milliseconds => delay(milliseconds);

const runId = new Date().toISOString().replace(/[-:]/g, '').replace(/\..+$/, '');
const directory = join(root, 'evidence/r2-mac-client', runId);
mkdirSync(directory, {recursive: true, mode: 0o700});

const steps = [];
let failedGate = null;
let failureDetail = null;
let certifiedCommit = null;

function persist(name, value) {
  const path = join(directory, name);
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`);
  return path;
}

function record(step, status, detail) {
  steps.push({step, status, detail, at: now()});
  return status;
}

function run(program, args, options = {}) {
  const result = spawnSync(program, args, {
    cwd: options.cwd ?? root,
    encoding: 'buffer',
    maxBuffer: 32 * 1024 * 1024,
    timeout: options.timeout ?? 120_000,
  });
  const output = Buffer.concat([result.stdout ?? Buffer.alloc(0), result.stderr ?? Buffer.alloc(0)]).toString();
  if (options.log) writeFileSync(join(directory, options.log), output);
  return {status: result.status, error: result.error?.message ?? null, output, stdout: (result.stdout ?? Buffer.alloc(0)).toString()};
}

function required(step, program, args, options = {}) {
  const result = run(program, args, options);
  if (result.error || result.status !== 0) {
    throw fail(step, `${program} ${args.join(' ')} exited ${result.status}: ${result.error ?? result.output.trim()}`);
  }
  return result;
}

function fail(gate, detail) {
  if (!failedGate) {
    failedGate = gate;
    failureDetail = detail;
  }
  return new Error(`${gate}: ${detail}`);
}

function assertStep(condition, gate, detail) {
  if (!condition) throw fail(gate, detail);
}

// --- AX helpers -------------------------------------------------------------

function swift(step, source) {
  const path = join(directory, `ax-${step}.swift`);
  writeFileSync(path, source);
  return required(step, 'swift', [path], {log: `ax-${step}.log`}).stdout;
}

function pressButtonSource(pid, title) {
  return `
import ApplicationServices
import Foundation
let pid: pid_t = ${pid}
let target = ${JSON.stringify(title)}
let app = AXUIElementCreateApplication(pid)
func value(_ element: AXUIElement, _ attribute: String) -> CFTypeRef? {
    var result: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute as CFString, &result)
    return error == .success ? result : nil
}
func find(_ element: AXUIElement) -> AXUIElement? {
    let role = value(element, kAXRoleAttribute) as? String
    let labels = [value(element, kAXTitleAttribute), value(element, kAXDescriptionAttribute), value(element, kAXValueAttribute)].compactMap { $0 as? String }
    if role == kAXButtonRole && labels.contains(target) { return element }
    let children = value(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
    for child in children {
        if let match = find(child) { return match }
    }
    return nil
}
guard let button = find(app) else { exit(2) }
if (value(button, kAXEnabledAttribute) as? Bool) == false { exit(4) }
guard AXUIElementPerformAction(button, kAXPressAction as CFString) == .success else { exit(3) }
`;
}

async function pressAccessibilityButton(pid, title, step) {
  let last = null;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const result = run('swift', ['-e', pressButtonSource(pid, title)], {timeout: 30_000});
    last = result;
    if (!result.error && result.status === 0) {
      persist(`ax-${step}.json`, {target: title, attempts: attempt + 1, status: 'pressed'});
      return;
    }
    if (result.error || ![2, 4].includes(result.status)) {
      persist(`ax-${step}.json`, {target: title, attempts: attempt + 1, status: 'failed', exit: result.status, output: result.output});
      throw fail(`AX_PRESS_${title}`, `AX button action failed for ${title} (exit ${result.status}): ${result.output.trim()}`);
    }
    await sleep(250);
  }
  persist(`ax-${step}.json`, {target: title, attempts: 40, status: 'unavailable', output: last?.output ?? ''});
  throw fail(`AX_PRESS_${title}`, `AX button did not become available: ${title}`);
}

function statusSurfaceSource(pid) {
  return `
import ApplicationServices
import Foundation
let pid: pid_t = ${pid}
let app = AXUIElementCreateApplication(pid)
func value(_ element: AXUIElement, _ attribute: String) -> CFTypeRef? {
    var result: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute as CFString, &result)
    return error == .success ? result : nil
}
var found: [String] = []
var visited = 0
func walk(_ element: AXUIElement, _ depth: Int) {
    if depth > 16 || visited > 20000 { return }
    visited += 1
    let text = [value(element, kAXValueAttribute), value(element, kAXTitleAttribute), value(element, kAXDescriptionAttribute)]
        .compactMap { $0 as? String }
        .joined(separator: " ")
    if text.contains("Connection:") { found.append(text) }
    let children = value(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
    for child in children { walk(child, depth + 1) }
}
walk(app, 0)
for line in found { print(line) }
`;
}

function readStatusSurface(pid) {
  const result = run('swift', ['-e', statusSurfaceSource(pid)], {timeout: 60_000});
  // A failed reader is a harness failure, never a product verdict.
  if (result.error || result.status !== 0) {
    throw fail('AX_READER', `AX status reader failed (exit ${result.status}, ${result.error ?? 'no error'}): ${result.output.trim().slice(0, 2000)}`);
  }
  const lines = result.stdout.split('\n').map(line => line.trim()).filter(Boolean);
  if (!lines.length) return {available: false, output: ''};
  const line = lines[0];
  const field = name => {
    const match = line.match(new RegExp(`${name}: ([^|]+)`));
    return match ? match[1].trim() : null;
  };
  return {
    available: true,
    raw: line,
    connectionState: field('Connection'),
    transport: field('Transport'),
    path: field('Path'),
    error: field('Error'),
  };
}

function dumpAxTree(pid, step) {
  const source = `
import ApplicationServices
import Foundation
let pid: pid_t = ${pid}
let app = AXUIElementCreateApplication(pid)
func value(_ element: AXUIElement, _ attribute: String) -> CFTypeRef? {
    var result: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute as CFString, &result)
    return error == .success ? result : nil
}
func walk(_ element: AXUIElement, _ depth: Int) {
    if depth > 12 { return }
    let role = value(element, kAXRoleAttribute) as? String ?? "?"
    let title = value(element, kAXTitleAttribute) as? String ?? ""
    let desc = value(element, kAXDescriptionAttribute) as? String ?? ""
    let text = value(element, kAXValueAttribute) as? String ?? ""
    print(String(repeating: "  ", count: depth) + role + " title=" + title + " desc=" + desc + " value=" + text)
    let children = value(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
    for child in children { walk(child, depth + 1) }
}
walk(app, 0)
`;
  const result = run('swift', ['-e', source], {timeout: 30_000});
  writeFileSync(join(directory, `ax-${step}.txt`), result.output);
  return result.output;
}

// --- main -------------------------------------------------------------------

let appChild = null;
let serviceStarted = false;

function launchApp(suffix, pairingOverride = pairingDirectory) {
  const stderrStream = createWriteStream(join(directory, `app${suffix}.stderr`), {flags: 'w'});
  const stdoutStream = createWriteStream(join(directory, `app${suffix}.stdout`), {flags: 'w'});
  const child = spawn(appExecutable, ['--pairing-dir', pairingOverride], {
    cwd: root,
    env: {...process.env, AGENTBROWSER_MAC_PAIRING: pairingOverride},
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.pipe(stdoutStream);
  child.stderr.pipe(stderrStream);
  appChild = child;
  return child;
}

/// Materialize a private pairing directory whose only defect is a wrong token.
/// Used only for the negative path: the endpoint is correct, so any error the
/// app surfaces is the Host's token comparison and nothing else.
function materializeWrongTokenPairing(target, endpoint) {
  mkdirSync(target, {recursive: true, mode: 0o700});
  writeFileSync(join(target, 'endpoint.txt'), endpoint, {mode: 0o600});
  const wrong = createHash('sha256').update(`wrong-${Date.now()}-${Math.random()}`).digest('hex');
  writeFileSync(join(target, 'token'), wrong, {mode: 0o600});
}

/// The session identity the app itself observed, read from its own native trace.
/// The `sessionId` projection is the product-owned signal: it carries the Host's
/// `ready` session id and is omitted entirely until a session exists. An earlier
/// version of this reader also required a `TASK19_RAW_CONTROL_FRAME` debug trace,
/// which is no longer emitted, so the requirement could never be met again.
function observedSessionId(suffix) {
  const path = join(directory, `app${suffix}.stderr`);
  if (!existsSync(path)) return null;
  const text = readFileSync(path, 'utf8');
  const snapshot = [...text.matchAll(/"sessionId":"([^"]+)"/g)].map(match => match[1]);
  return {snapshot: snapshot.at(-1) ?? null, snapshot_frames: snapshot.length};
}

async function stopApp(child) {
  if (!child || child.exitCode !== null) return;
  process.kill(child.pid, 'SIGTERM');
  for (let attempt = 0; attempt < 60 && child.exitCode === null; attempt += 1) await sleep(250);
}

async function connectThroughVisibleUi(child, suffix, seconds) {
  let seen = false;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (readStatusSurface(child.pid).available) { seen = true; break; }
    await sleep(500);
  }
  if (!seen) {
    const tree = dumpAxTree(child.pid, `${suffix}-launch`);
    throw fail('OBSERVABILITY', `no AX status surface after launch (${suffix}); AX tree:\n${tree.slice(0, 3000)}`);
  }
  await pressAccessibilityButton(child.pid, '打开菜单', `${suffix}-open-menu`);
  await sleep(1_000);
  await pressAccessibilityButton(child.pid, '连接', `${suffix}-connect`);
  const observed = [];
  const started = Date.now();
  while (Date.now() - started < seconds * 1_000) {
    const surface = readStatusSurface(child.pid);
    observed.push({at: now(), pid_alive: child.exitCode === null, ...surface});
    await sleep(pollMilliseconds);
  }
  return observed;
}

function cleanup(message) {
  if (appChild && appChild.pid) {
    try { process.kill(appChild.pid, 'SIGTERM'); } catch { /* already gone */ }
  }
  // The negative-path pairing copy holds a token; never retain it.
  rmSync(join(directory, 'negative-pairing'), {recursive: true, force: true});
  if (serviceStarted) {
    const stop = run(launcher, ['stop', '--root', serviceRoot], {timeout: 60_000});
    persist('stop.json', {service_label: serviceLabel, exit: stop.status, output: stop.output.trim(), at: now()});
  }
  persist('gate-summary.txt', [
    `run: ${runId}`,
    `directory: evidence/r2-mac-client/${runId}`,
    `commit: ${certifiedCommit ?? 'unknown (failed before the revision gate)'}`,
    `service_root: ${serviceRoot}`,
    `service_label: ${serviceLabel}`,
    failedGate ? `result: FAIL` : `result: ${message}`,
    failedGate ? `first_failing_gate: ${failedGate}` : '',
    failedGate ? `detail: ${failureDetail}` : '',
    '',
  ].join('\n'));
  persist('steps.json', {run_id: runId, steps, failed_gate: failedGate, detail: failureDetail});
}

try {
  assert.equal(process.platform, 'darwin', 'Mac connect gate requires Darwin');
  assert.equal(serviceLabel, 'com.agentbrowser.task6', 'Only the task6 lifecycle root is allowed');
  assert(!serviceRoot.includes('lan'), 'Refusing a non-task6 service root');
  assert(existsSync(launcher), `Lifecycle entrypoint missing: ${launcher}`);
  assert(existsSync(appExecutable), `Installed app executable missing: ${appExecutable}`);
  assert(existsSync(bridgeExecutable), `Bridge executable missing: ${bridgeExecutable}`);
  assert(existsSync(pairingDirectory), `Pairing directory missing: ${pairingDirectory}`);
  // This harness writes both pairing files: `endpoint.txt` from the refreshed
  // endpoint receipt, and `token` from the service-owned access token. Those two
  // files are the whole pairing state (`docs/m1/connection-minimum.md` §7.5).
  const serviceTokenPath = join(serviceRoot, 'trust', 'token');
  assert(existsSync(serviceTokenPath), `Service access token missing: ${serviceTokenPath}`);
  persist('build-hashes.txt', [
    `${hash(readFileSync(appExecutable)).replace('sha256:', '')}  ${appExecutable}`,
    `${hash(readFileSync(bridgeExecutable)).replace('sha256:', '')}  ${bridgeExecutable}`,
  ].join('\n') + '\n');
  persist('identity.json', {
    run_id: runId,
    host: hostname(),
    user: userInfo().username,
    node: process.version,
    service_root: serviceRoot,
    service_label: serviceLabel,
    pairing_directory: pairingDirectory,
    app_executable_hash: hash(readFileSync(appExecutable)),
    bridge_executable_hash: hash(readFileSync(bridgeExecutable)),
  });

  // The acceptance must name the exact revision it ran on. An earlier run
  // recorded a worktree hash that is not a git object, so this gate records the
  // commit plus every dirty path, and refuses to certify a run whose connection
  // path itself is uncommitted.
  const revision = run('git', ['rev-parse', 'HEAD'], {timeout: 30_000});
  assertStep(revision.status === 0, 'REVISION', 'cannot read the git revision of this worktree');
  const head = revision.stdout.trim();
  const dirtyPaths = run('git', ['status', '--porcelain'], {timeout: 30_000}).stdout.trim().split('\n').filter(Boolean);
  const connectionPath = /^(apps\/macos\/|packages\/client-connection\/|packages\/client-domain\/|scripts\/host-service\/|scripts\/mac-connect-gate\.mjs)/;
  const dirtyConnection = dirtyPaths.filter(line => connectionPath.test(line.slice(3)));
  persist('revision.json', {commit: head, dirty_paths: dirtyPaths, dirty_connection_paths: dirtyConnection});
  assertStep(dirtyConnection.length === 0, 'REVISION',
    `the connection path is uncommitted, so this run cannot certify revision ${head}:\n${dirtyConnection.join('\n')}`);
  certifiedCommit = head;
  record('revision', 'pass', `commit ${head}, connection path clean`);

  // The one credential on the connection path is the service-owned access
  // token. It must be the exact token the running Host will compare.
  const trustStatus = JSON.parse(required('trust_status', launcher, ['trust', 'status', '--root', serviceRoot], {log: 'trust-status.log'}).stdout);
  const token = readFileSync(serviceTokenPath, 'utf8').trim();
  assertStep(token.length > 0, 'PAIRING_TOKEN', 'the service access token is empty');
  const tokenDigest = createHash('sha256').update(token, 'utf8').digest('hex');
  assertStep(trustStatus.token_sha256 === tokenDigest, 'PAIRING_TOKEN',
    `service token digest ${tokenDigest} is not the digest trust status reports ${trustStatus.token_sha256}`);
  persist('pairing-token.json', {token_sha256: `sha256:${tokenDigest}`, token_source: serviceTokenPath});
  record('preconditions', 'pass', 'entrypoint, app, bridge, pairing directory present');

  // 1. Lifecycle: observe, start if stopped, then refresh the endpoint.
  const before = required('status_before', launcher, ['status', '--root', serviceRoot], {log: 'status-before.log'});
  persist('status-before.json', JSON.parse(before.stdout));
  const beforeState = JSON.parse(before.stdout).state;
  if (expectedHostBinary) {
    const same = realPath(JSON.parse(before.stdout).host_binary ?? '') === realPath(expectedHostBinary);
    assertStep(same, 'HOST_BINARY',
      `deployed Host ${realPath(JSON.parse(before.stdout).host_binary ?? '')} is not the expected render-enabled Host ${realPath(expectedHostBinary)}; install and restart through the lifecycle entrypoint`);
  }
  if (beforeState !== 'running') {
    required('start', launcher, ['start', '--root', serviceRoot], {timeout: 180_000, log: 'start.log'});
    serviceStarted = true;
    const after = required('status_after_start', launcher, ['status', '--root', serviceRoot], {log: 'status-after-start.log'});
    persist('start.json', JSON.parse(after.stdout));
    assertStep(JSON.parse(after.stdout).state === 'running', 'LIFECYCLE_START', 'Host did not report running after start');
  } else {
    serviceStarted = true;
    persist('start.json', {state: 'already_running'});
  }
  record('lifecycle_start', 'pass', 'task6 Host running');

  const refresh = required('endpoint_refresh', launcher, ['trust', 'endpoint-refresh', '--root', serviceRoot, '--timeout', '30'], {timeout: 120_000, log: 'endpoint-refresh.log'});
  const refreshReceipt = JSON.parse(refresh.stdout);
  persist('refresh.json', refreshReceipt);
  assertStep(refreshReceipt.state === 'refreshed' && Number.isInteger(refreshReceipt.endpoint_pid) && refreshReceipt.endpoint_pid > 0,
    'ENDPOINT_REFRESH', `endpoint refresh did not report a running endpoint: ${refresh.stdout.trim()}`);
  record('endpoint_refresh', 'pass', `endpoint_pid ${refreshReceipt.endpoint_pid} bound ${refreshReceipt.bound_address}`);

  // 2. Pairing endpoint must match the refreshed endpoint exactly.
  const statusNow = JSON.parse(required('status_refreshed', launcher, ['status', '--root', serviceRoot], {log: 'status-refreshed.log'}).stdout);
  persist('status-refreshed.json', statusNow);
  const boundAddress = refreshReceipt.bound_address;
  // The bundle endpoint is exactly `<host>:<port>`; the transport owns the
  // scheme (`docs/m1/connection-minimum.md` §7.1).
  const endpointText = boundAddress;
  writeFileSync(join(pairingDirectory, 'endpoint.txt'), endpointText, {mode: 0o600});
  writeFileSync(join(pairingDirectory, 'token'), token, {mode: 0o600});
  persist('pairing.json', {
    directory: pairingDirectory,
    endpoint: endpointText,
    endpoint_source: 'trust endpoint-refresh bound_address',
    token_sha256: `sha256:${tokenDigest}`,
    files: ['endpoint.txt', 'token'],
  });
  record('pairing', 'pass', `pairing endpoint set to ${endpointText}`);

  // 3. Launch the ordinary app with its own stdio captured.
  const firstApp = launchApp('');
  persist('app.pid', {pid: firstApp.pid, at: now()});
  await sleep(3_000);
  assertStep(firstApp.exitCode === null, 'APP_LAUNCH', `app exited during launch with code ${firstApp.exitCode}`);
  record('app_launch', 'pass', `pid ${firstApp.pid}`);

  const initial = readStatusSurface(firstApp.pid);
  if (!initial.available) {
    const tree = dumpAxTree(firstApp.pid, 'launch');
    throw fail('OBSERVABILITY', `the app exposed no AX connection status surface; AX tree:\n${tree.slice(0, 3000)}`);
  }
  dumpAxTree(firstApp.pid, 'before');
  record('observability', 'pass', `AX status surface visible: ${initial.raw}`);

  // 4-5. Visible UI connect plus a timestamped observation series.
  const series = await connectThroughVisibleUi(firstApp, '', observationSeconds);
  persist('status-series.json', series);
  dumpAxTree(firstApp.pid, 'after-connect');
  record('ax_connect', 'pass', 'pressed 打开菜单 then 连接');

  const last = series.filter(entry => entry.available).at(-1) ?? null;
  persist('final-status.json', last ?? {available: false});
  const connected = series.some(entry => entry.connectionState === 'connected');
  record('connect_observation', connected ? 'pass' : 'fail', last ? last.raw : 'no readable status surface');

  if (!connected) {
    throw fail('CONNECT', `app never reported connected; last surface: ${last ? last.raw : 'unavailable'}`);
  }

  // 6. Independent Host-side evidence.
  const hostStatus = JSON.parse(required('host_status', launcher, ['status', '--root', serviceRoot], {log: 'host-status.log'}).stdout);
  persist('host-status-after.json', hostStatus);
  const logs = run('bash', ['-c', `ls -1t "${serviceRoot}"/logs/* 2>/dev/null | head -1`], {timeout: 30_000});
  const newestLog = logs.stdout.trim();
  if (newestLog) {
    const tail = run('tail', ['-n', '40', newestLog], {timeout: 30_000});
    writeFileSync(join(directory, 'endpoint-tail.txt'), tail.output);
  }
  assertStep(hostStatus.session_id, 'HOST_SESSION', 'the running Host reported no session id');
  record('host_evidence', 'pass', `host session_id ${hostStatus.session_id}`);

  // 7. The app must observe that same Host session, not a private one. Its
  //    `sessionId` projection is absent until a session exists, so a match here
  //    can only come from the Host's `ready` frame.
  const firstObserved = observedSessionId('');
  persist('session-identity.json', {app_observed: firstObserved, host_session_id: hostStatus.session_id});
  assertStep(firstObserved.snapshot, 'SESSION_IDENTITY',
    'the app never projected a session id, so it never attached to the Host');
  assertStep(firstObserved.snapshot === hostStatus.session_id, 'SESSION_IDENTITY',
    `app observed session ${firstObserved.snapshot} but the Host reports ${hostStatus.session_id}`);
  record('session_identity', 'pass', `app and Host agree on session ${hostStatus.session_id}`);

  // 8. Reconnect: restart the client and attach to the same Host session again.
  await stopApp(firstApp);
  const secondApp = launchApp('-restarted');
  persist('app-restarted.pid', {pid: secondApp.pid, previous_pid: firstApp.pid, at: now()});
  await sleep(3_000);
  assertStep(secondApp.exitCode === null, 'APP_RESTART', `restarted app exited with code ${secondApp.exitCode}`);
  const reconnectSeries = await connectThroughVisibleUi(secondApp, '-restarted', observationSeconds);
  persist('reconnect-series.json', reconnectSeries);
  const reconnected = reconnectSeries.some(entry => entry.connectionState === 'connected');
  const secondObserved = observedSessionId('-restarted');
  const hostStatusAfter = JSON.parse(required('host_status_after_restart', launcher, ['status', '--root', serviceRoot], {log: 'host-status-restart.log'}).stdout);
  persist('reconnect.json', {
    reconnected,
    first_pid: firstApp.pid,
    restarted_pid: secondApp.pid,
    app_observed: secondObserved,
    host_session_id: hostStatusAfter.session_id,
    final_surface: reconnectSeries.filter(entry => entry.available).at(-1)?.raw ?? null,
  });
  if (!reconnected) {
    throw fail('RECONNECT', `restarted app never reported connected; last surface: ${reconnectSeries.filter(entry => entry.available).at(-1)?.raw ?? 'unavailable'}`);
  }
  assertStep(secondObserved.snapshot === hostStatus.session_id, 'RECONNECT_SESSION',
    `reconnected app observed session ${secondObserved.snapshot} but the Host session is ${hostStatus.session_id}`);
  record('reconnect', 'pass', `restarted app reattached to session ${hostStatus.session_id}`);

  // 9. Negative path: a wrong token must surface the Host's real
  //    `REMOTE_TOKEN_INVALID` code, never a bare protocol message. The endpoint
  //    is correct, so the token is the only variable.
  const negativePairing = join(directory, 'negative-pairing');
  materializeWrongTokenPairing(negativePairing, endpointText);
  const negativeApp = launchApp('-negative', negativePairing);
  const negativeSeries = await connectThroughVisibleUi(negativeApp, '-negative', 12);
  const negativeLast = negativeSeries.filter(entry => entry.available).at(-1) ?? null;
  const error = negativeLast?.error ?? '';
  persist('negative-token.json', {final_surface: negativeLast?.raw ?? null, error});
  await stopApp(negativeApp);
  assertStep(error, 'NEGATIVE_TOKEN', `no error surfaced for the wrong token; last surface: ${negativeLast?.raw ?? 'unavailable'}`);
  assertStep(/REMOTE_TOKEN_INVALID/.test(error), 'NEGATIVE_TOKEN',
    `the Host token rejection did not surface REMOTE_TOKEN_INVALID: ${error}`);
  assertStep(!/Expected Host protocol version/.test(error), 'NEGATIVE_TOKEN',
    `the Host token rejection was collapsed into a protocol-version message: ${error}`);
  record('negative_token', 'pass', `surfaced ${error}`);

  persist('pass.json', {
    run_id: runId,
    result: 'pass',
    commit: certifiedCommit,
    app_executable_hash: hash(readFileSync(appExecutable)),
    bridge_executable_hash: hash(readFileSync(bridgeExecutable)),
    host_binary: hostStatus.host_binary,
    host_binary_sha256: hostStatus.binary_sha256,
    host_session_id: hostStatus.session_id,
    endpoint_pid: refreshReceipt.endpoint_pid,
    bound_address: boundAddress,
    first_final_surface: last.raw,
    reconnect_final_surface: reconnectSeries.filter(entry => entry.available).at(-1)?.raw ?? null,
    app_observed_session: firstObserved,
    created_at: now(),
  });
  cleanup('PASS');
  process.stdout.write(`${JSON.stringify({run_id: runId, directory: `evidence/r2-mac-client/${runId}`, result: 'pass'}, null, 2)}\n`);
  process.exit(0);
} catch (error) {
  cleanup('FAIL');
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(1);
}

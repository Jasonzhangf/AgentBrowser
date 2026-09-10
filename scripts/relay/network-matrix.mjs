#!/usr/bin/env node

/*
 * Task-local Relay network evidence runner.
 *
 * The browser/session payload is deliberately opaque here.  This runner
 * proves the Relay-owned authentication, directory and tunnel boundaries by
 * executing the compiled artifact produced from one explicit candidate root.
 * It does not import or call the candidate's auth/signing helpers.
 */

import assert from 'node:assert/strict';
import {createHash, generateKeyPairSync, sign} from 'node:crypto';
import {spawn, spawnSync} from 'node:child_process';
import {mkdtempSync, existsSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {tmpdir} from 'node:os';
import {dirname, join, relative, resolve} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {request as httpsRequest} from 'node:https';
import {
  NETWORK_EVIDENCE_SCHEMA,
  loadNetworkPathEvidence,
  projectNetworkMatrix,
} from './network-matrix-contract.mjs';

const RELAY_ABI_ID = 'agentbrowser-relay-v0';
const scriptPath = fileURLToPath(import.meta.url);
const selfRoot = resolve(dirname(scriptPath), '../..');
const args = process.argv.slice(2);

function option(name) {
  const index = args.indexOf(name);
  if (index < 0) return undefined;
  const value = args[index + 1];
  if (!value || value.startsWith('--')) throw new Error(`${name} requires a value`);
  return value;
}

const candidateRoot = resolve(option('--candidate-root') ?? selfRoot);
const runId = option('--run-id') ?? `network-matrix-${Date.now()}`;
const pathEvidenceRoot = option('--path-evidence-root') ?? option('--external-evidence-root');
const evidenceRoot = resolve(option('--evidence-root') ?? join(selfRoot, 'evidence', 'm1-network', runId));
mkdirSync(evidenceRoot, {recursive: true});
const runContext = {source: undefined, cleanup: undefined, pathEvidence: undefined};

function git(root, ...gitArgs) {
  const result = spawnSync('git', ['-C', root, ...gitArgs], {encoding: 'utf8'});
  if (result.error || result.status !== 0) {
    throw new Error(`git -C ${root} ${gitArgs.join(' ')} failed: ${result.error ?? result.stderr ?? result.status}`);
  }
  return result.stdout.trim();
}

function gitStatus(root) {
  return git(root, 'status', '--porcelain=v1');
}

function sha256Bytes(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

function sha256File(path) {
  return sha256Bytes(readFileSync(path));
}

function requiredFile(path, label) {
  if (!existsSync(path)) throw new Error(`${label} is unavailable at ${path}`);
  return path;
}

function optionalRealpath(path) {
  try {
    return realpathSync(path);
  } catch {
    return undefined;
  }
}

function optionalSha256File(path) {
  try {
    return sha256File(path);
  } catch {
    return undefined;
  }
}

function writeEvidence(name, value) {
  writeFileSync(join(evidenceRoot, name), typeof value === 'string' ? value : `${JSON.stringify(value, null, 2)}\n`);
}

function runSync(program, programArgs, options = {}) {
  const result = spawnSync(program, programArgs, {
    cwd: options.cwd,
    env: options.env,
    encoding: 'utf8',
    input: options.input,
    maxBuffer: 32 * 1024 * 1024,
    timeout: options.timeout ?? 120_000,
  });
  return {
    command: [program, ...programArgs].join(' '),
    cwd: options.cwd,
    status: result.status,
    signal: result.signal,
    error: result.error ? String(result.error) : undefined,
    stdout: result.stdout ?? '',
    stderr: result.stderr ?? '',
  };
}

function parseLsofNames(output, field) {
  const names = [];
  let activeField = false;
  for (const line of output.split('\n')) {
    if (line === `f${field}`) {
      activeField = true;
      continue;
    }
    if (activeField && line.startsWith('n')) {
      names.push(line.slice(1));
      activeField = false;
    }
  }
  return names;
}

function observeProcess(pid) {
  const ps = runSync('ps', ['-ww', '-p', String(pid), '-o', 'pid=,command='], {timeout: 10_000});
  if (ps.error || ps.status !== 0) {
    return {
      pid,
      alive: false,
      ps_status: ps.status,
      ps_error: ps.error,
      ps_stderr: ps.stderr,
    };
  }
  const line = ps.stdout.trim();
  const match = line.match(/^(\d+)\s+([\s\S]+)$/);
  if (!match) {
    return {
      pid,
      alive: false,
      ps_status: ps.status,
      ps_error: `Unable to parse process command identity: ${JSON.stringify(line)}`,
    };
  }
  const observedPid = Number(match[1]);
  const command = match[2];
  const cwd = runSync('lsof', ['-a', '-p', String(pid), '-d', 'cwd', '-Fn'], {timeout: 10_000});
  const executable = runSync('lsof', ['-a', '-p', String(pid), '-d', 'txt', '-Fn'], {timeout: 10_000});
  const cwdNames = cwd.status === 0 ? parseLsofNames(cwd.stdout, 'cwd') : [];
  const executableNames = executable.status === 0 ? parseLsofNames(executable.stdout, 'txt') : [];
  return {
    pid: observedPid,
    alive: true,
    command,
    cwd: cwdNames[0],
    executable: executableNames[0],
    diagnostics: {
      ps_status: ps.status,
      cwd_status: cwd.status,
      cwd_error: cwd.error,
      cwd_stderr: cwd.stderr,
      executable_status: executable.status,
      executable_error: executable.error,
      executable_stderr: executable.stderr,
    },
  };
}

function runtimeProcessCheck(observed, expected) {
  const mismatches = [];
  const expectedCwd = optionalRealpath(expected.cwd);
  const expectedExecutable = optionalRealpath(expected.executable.path);
  const expectedEntrypointHash = optionalSha256File(expected.entrypoint.path);
  const expectedArtifactHash = optionalSha256File(expected.artifact.path);
  const addMismatch = (label, actual, wanted) => {
    if (actual !== wanted) mismatches.push(`${label}: expected ${JSON.stringify(wanted)}, got ${JSON.stringify(actual)}`);
  };
  if (expectedCwd === undefined) mismatches.push(`Expected runtime cwd is unavailable: ${expected.cwd}`);
  if (expectedExecutable === undefined) mismatches.push(`Expected runtime executable is unavailable: ${expected.executable.path}`);
  if (expectedEntrypointHash === undefined) mismatches.push(`Expected extracted entrypoint is unavailable: ${expected.entrypoint.path}`);
  if (expectedArtifactHash === undefined) mismatches.push(`Expected artifact is unavailable: ${expected.artifact.path}`);

  addMismatch('pid alive', observed.alive, true);
  addMismatch('pid', observed.pid, expected.pid);
  addMismatch('argv command', observed.command, expected.argv.join(' '));
  addMismatch('cwd', observed.cwd, expectedCwd);
  addMismatch('executable', observed.executable, expectedExecutable);
  const observedExecutableHash = optionalSha256File(observed.executable);
  addMismatch('observed executable sha256', observedExecutableHash, expected.executable.sha256);
  addMismatch('entrypoint sha256', expectedEntrypointHash, expected.entrypoint.sha256);
  addMismatch('artifact sha256', expectedArtifactHash, expected.artifact.sha256);
  addMismatch('spawnfile', expected.spawnfile, expected.executable.path);
  if (JSON.stringify(expected.spawnargs) !== JSON.stringify(expected.argv)) {
    mismatches.push(`spawnargs: expected ${JSON.stringify(expected.argv)}, got ${JSON.stringify(expected.spawnargs)}`);
  }
  return {verified: mismatches.length === 0, mismatches};
}

function runtimeProcessEvidence(observed, expected, check) {
  const observedWithHashes = {
    ...observed,
    executable_sha256: optionalSha256File(observed.executable),
  };
  return {
    pid: expected.pid,
    argv: expected.argv,
    cwd: expected.cwd,
    cwd_realpath: optionalRealpath(expected.cwd),
    executable: {
      path: expected.executable.path,
      realpath: optionalRealpath(expected.executable.path),
      sha256: expected.executable.sha256,
    },
    entrypoint: {
      archive: expected.entrypoint.archive,
      path: expected.entrypoint.path,
      sha256: expected.entrypoint.sha256,
    },
    artifact: {
      path: expected.artifact.path,
      sha256: expected.artifact.sha256,
      source_commit: expected.artifact.source_commit,
      source_tree: expected.artifact.source_tree,
    },
    observed: observedWithHashes,
    verification: check,
  };
}

async function assertNoBinaryDuringWindow(inboxes, timeout = 500) {
  const deadline = Date.now() + timeout;
  let checks = 0;
  while (Date.now() < deadline) {
    for (const inbox of inboxes) {
      const unexpected = inbox.inbox.items.filter(item => item.binary);
      if (unexpected.length > 0) {
        const summary = unexpected.map(item => ({
          length: item.value.length,
          sha256: sha256Bytes(item.value),
        }));
        throw new Error(`Unexpected binary frame on ${inbox.label}: ${JSON.stringify(summary)}`);
      }
    }
    checks += 1;
    await new Promise(resolveDelay => setTimeout(resolveDelay, Math.min(25, Math.max(1, deadline - Date.now()))));
  }
  return {
    result: 'PASS',
    bounded_window_ms: timeout,
    checks,
    inboxes: inboxes.map(inbox => inbox.label),
  };
}

function assertRun(result, label) {
  assert.equal(result.error, undefined, `${label} failed to start: ${result.error ?? ''}`);
  assert.equal(result.status, 0, `${label} exited ${result.status}: ${result.stderr}`);
}

function independentDigest(value) {
  return sha256Bytes(Buffer.from(value, 'utf8'));
}

function independentAuthTranscript(nonce, path, deviceId, tokenDigest) {
  return Buffer.from(JSON.stringify([RELAY_ABI_ID, nonce, path, deviceId, tokenDigest]));
}

/*
 * This vector guards the runner's own wire encoding.  The live token remains
 * generated by the compiled Relay, while every live signature below uses the
 * two independent functions above instead of protocol/relay helpers.
 */
const FIXED_AUTH_VECTOR = Object.freeze({
  nonce: 'fixed-auth-nonce-v1',
  path: '/v2/control/client',
  deviceId: 'fixed-device-v1',
  token: 'fixed-token-v1',
  tokenDigest: '5f6c1871d2291ccbd6fbfe50bfc349b5e9a5b8720c139559ff0ae6f4e16b91ac',
  transcriptHex: '5b226167656e7462726f777365722d72656c61792d7630222c2266697865642d617574682d6e6f6e63652d7631222c222f76322f636f6e74726f6c2f636c69656e74222c2266697865642d6465766963652d7631222c2235663663313837316432323931636362643666626665353062666333343962356539613562383732306331333935353966663061653666346531366239316163225d',
});

function checkFixedAuthVector() {
  assert.equal(independentDigest(FIXED_AUTH_VECTOR.token), FIXED_AUTH_VECTOR.tokenDigest);
  assert.equal(independentAuthTranscript(
    FIXED_AUTH_VECTOR.nonce,
    FIXED_AUTH_VECTOR.path,
    FIXED_AUTH_VECTOR.deviceId,
    FIXED_AUTH_VECTOR.tokenDigest,
  ).toString('hex'), FIXED_AUTH_VECTOR.transcriptHex);
}

function sanitize(value) {
  if (Array.isArray(value)) return value.map(item => sanitize(item));
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [
    key.toLowerCase().includes('token') || key.toLowerCase().includes('password') || key === 'signature'
      ? [key, '[redacted]']
      : [key, sanitize(item)],
  ]));
}

const apiEvents = [];
function api(base, path, method = 'GET', token, payload, ca) {
  return new Promise((resolveResponse, reject) => {
    const headers = {'content-type': 'application/json'};
    if (token) headers.authorization = `Bearer ${token}`;
    const request = httpsRequest(`${base}${path}`, {method, ca, headers}, response => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', chunk => { body += chunk; });
      response.on('end', () => {
        try {
          const parsed = JSON.parse(body);
          const result = {status: response.statusCode ?? 0, body: parsed};
          apiEvents.push({method, path, status: result.status, body: sanitize(parsed)});
          resolveResponse(result);
        } catch (error) {
          reject(new Error(`Relay API returned invalid JSON for ${method} ${path}: ${error instanceof Error ? error.message : String(error)}`));
        }
      });
    });
    request.once('error', reject);
    if (payload === undefined) request.end();
    else request.end(JSON.stringify(payload));
  });
}

let WebSocket;
class Inbox {
  constructor(ws) {
    this.ws = ws;
    this.items = [];
    this.error = undefined;
    ws.on('error', error => { this.error = error; });
    ws.on('message', (data, binary) => {
      if (binary) this.items.push({binary: true, value: Buffer.from(data)});
      else {
        try { this.items.push({binary: false, value: JSON.parse(data.toString())}); }
        catch (error) { this.items.push({binary: false, value: {type: '__invalid__', error: String(error)}}); }
      }
    });
  }

  async take(type, timeout = 7000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const index = this.items.findIndex(item => type === 'binary'
        ? item.binary
        : !item.binary && item.value?.type === type);
      if (index >= 0) {
        const item = this.items.splice(index, 1)[0];
        if (!item.binary) assert.equal(item.value.abi, RELAY_ABI_ID, `Missing ABI on ${type}`);
        return item.value;
      }
      if (this.error && this.ws.readyState === WebSocket.CLOSED) throw this.error;
      await new Promise(resolveDelay => setTimeout(resolveDelay, 5));
    }
    throw new Error(`Timed out waiting for ${type}: ${JSON.stringify(this.items)}`);
  }
}

function control(type, fields = {}) {
  return JSON.stringify({type, abi: RELAY_ABI_ID, ...fields});
}

function connectWs(url, options = {}) {
  return new Promise((resolveSocket, reject) => {
    const ws = new WebSocket(url, options);
    const fail = error => reject(error);
    ws.once('error', fail);
    ws.once('open', () => {
      ws.off('error', fail);
      resolveSocket(new Inbox(ws));
    });
  });
}

async function closeSocket(ws) {
  if (!ws || ws.readyState === WebSocket.CLOSED) return;
  await new Promise(resolveClose => {
    const timer = setTimeout(() => {
      ws.terminate();
      resolveClose();
    }, 1500);
    ws.once('close', () => { clearTimeout(timer); resolveClose(); });
    ws.close(1000, 'MATRIX_CLEANUP');
  });
}

async function waitForClose(ws, timeout = 7000) {
  if (ws.readyState === WebSocket.CLOSED) return;
  await new Promise((resolveClose, reject) => {
    const timer = setTimeout(() => reject(new Error('Timed out waiting for WebSocket close')), timeout);
    ws.once('close', () => { clearTimeout(timer); resolveClose(); });
  });
}

async function enroll(base, token, name, ca) {
  const keys = generateKeyPairSync('ed25519');
  const publicKey = keys.publicKey.export({type: 'spki', format: 'pem'}).toString();
  const response = await api(base, '/v2/devices', 'POST', token, {name, publicKey}, ca);
  assert.equal(response.status, 201);
  return {id: response.body.id, privateKey: keys.privateKey, token};
}

async function connectControl(base, path, identity, ca, tamper) {
  const inbox = await connectWs(`${base.replace('https:', 'wss:')}${path}`, {ca});
  const challenge = await inbox.take('auth.challenge');
  assert.equal(challenge.path, path);
  const signaturePath = tamper === 'path' ? `${path}-tampered` : path;
  const signatureDevice = tamper === 'device' ? `${identity.id}-tampered` : identity.id;
  const signatureToken = tamper === 'token' ? `${identity.token}-tampered` : identity.token;
  const signature = sign(
    null,
    independentAuthTranscript(challenge.nonce, signaturePath, signatureDevice, independentDigest(signatureToken)),
    identity.privateKey,
  ).toString('base64url');
  inbox.ws.send(control('auth.prove', {token: identity.token, deviceId: identity.id, signature}));
  return inbox;
}

async function rejectedUpgrade(url, options = {}, timeout = 7000) {
  return new Promise(resolveRejected => {
    const ws = new WebSocket(url, options);
    let finished = false;
    const finish = value => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) ws.terminate();
      resolveRejected(value);
    };
    const timer = setTimeout(() => finish({status: 0, error: 'timeout'}), timeout);
    ws.once('unexpected-response', (_request, response) => {
      response.resume();
      finish({status: response.statusCode ?? 0});
    });
    ws.once('error', error => {
      const match = String(error).match(/\b([45]\d\d)\b/);
      finish({status: match ? Number(match[1]) : 0, error: String(error)});
    });
    ws.once('open', () => finish({status: 101, error: 'unexpected upgrade success'}));
  });
}

function channel(base, offer, name, ca) {
  const entry = offer.channels[name];
  return connectWs(`${base.replace('https:', 'wss:')}${entry.path}`, {
    ca,
    headers: {authorization: `Bearer ${entry.ticket}`},
  });
}

async function waitForListening(child, stdoutState, timeout = 12_000) {
  return new Promise((resolveAddress, reject) => {
    const deadline = setTimeout(() => reject(new Error(`Timed out waiting for artifact listener: ${stdoutState.value}`)), timeout);
    const inspect = () => {
      for (const line of stdoutState.value.split('\n')) {
        if (!line.trim()) continue;
        try {
          const message = JSON.parse(line);
          if (message.event === 'listening' && typeof message.address === 'string') {
            clearTimeout(deadline);
            resolveAddress(message.address);
            return;
          }
        } catch {
          // The compiled CLI may write non-JSON diagnostics; retain them in the raw log.
        }
      }
    };
    child.stdout.on('data', inspect);
    child.once('error', error => { clearTimeout(deadline); reject(error); });
    child.once('close', code => {
      if (!stdoutState.value.includes('"event":"listening"')) {
        clearTimeout(deadline);
        reject(new Error(`Artifact runtime exited before listening (${code}): ${stdoutState.value}`));
      }
    });
    inspect();
  });
}

async function stopChild(child, expected) {
  if (!child) return {stopped: true, pid_exit_verified: true, identity_before_verified: true, reason: 'no child'};
  const pid = child.pid;
  if (!Number.isInteger(pid) || pid <= 0) {
    return {stopped: false, pid_exit_verified: false, identity_before_verified: false, error: 'Runtime child has no valid PID'};
  }

  const before = observeProcess(pid);
  const beforeCheck = expected
    ? runtimeProcessCheck(before, expected)
    : {verified: false, mismatches: ['No expected runtime identity was captured']};
  const identityBefore = expected
    ? runtimeProcessEvidence(before, expected, beforeCheck)
    : before;
  if (!beforeCheck.verified) {
    return {
      stopped: false,
      pid,
      signal_sent: false,
      pid_exit_verified: false,
      identity_before_verified: false,
      identity_before: identityBefore,
      identity_mismatches: beforeCheck.mismatches,
      error: 'Runtime identity was not verified; SIGTERM was not sent',
    };
  }
  if (child.exitCode === null && !child.signalCode) {
    const closed = new Promise(resolveClose => child.once('close', (code, signal) => resolveClose({exitCode: code, signal})));
    if (!child.kill('SIGTERM')) {
      return {
        stopped: false,
        pid,
        signal_sent: false,
        pid_exit_verified: false,
        identity_before_verified: beforeCheck.verified,
        identity_before: identityBefore,
        error: 'SIGTERM was not delivered',
      };
    }
    const stopResult = await Promise.race([
      closed,
      new Promise(resolveTimeout => setTimeout(() => resolveTimeout({timeout: true}), 7000)),
    ]);
    const after = observeProcess(pid);
    return {
      pid,
      stopped: stopResult.timeout !== true && !after.alive,
      ...stopResult,
      signal_sent: true,
      pid_exit_verified: !after.alive,
      identity_before_verified: beforeCheck.verified,
      identity_before: identityBefore,
      identity_after: after,
    };
  }

  const after = observeProcess(pid);
  return {
    pid,
    stopped: !after.alive,
    exitCode: child.exitCode,
    signal: child.signalCode,
    signal_sent: false,
    pid_exit_verified: !after.alive,
    identity_before_verified: beforeCheck.verified,
    identity_before: identityBefore,
    identity_after: after,
  };
}

function probeListener(base, ca) {
  return new Promise(resolveProbe => {
    let finished = false;
    const finish = value => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      resolveProbe(value);
    };
    const request = httpsRequest(`${base}/health`, {method: 'GET', ca}, response => {
      response.resume();
      response.once('end', () => finish({closed: false, status: response.statusCode ?? 0}));
      response.once('error', error => finish({closed: false, error: String(error), code: error.code}));
    });
    const timer = setTimeout(() => {
      request.destroy();
      finish({closed: false, error: 'listener probe timed out'});
    }, 1000);
    request.once('error', error => finish({
      closed: error.code === 'ECONNREFUSED',
      error: String(error),
      code: error.code,
    }));
    request.end();
  });
}

async function waitForListenerClosed(base, ca, timeout = 7000) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    last = await probeListener(base, ca);
    if (last.closed) return {checked: true, ...last};
    await new Promise(resolveDelay => setTimeout(resolveDelay, 50));
  }
  return {checked: true, closed: false, timeout: true, last};
}

function applyCleanupResult(matrixResult, cleanup) {
  if (!matrixResult) return;
  matrixResult.result = matrixResult.result === 'PASS' && cleanup.result === 'PASS' ? 'PASS' : 'FAIL';
  matrixResult.cleanup = cleanup.result;
}

function relayPathEvidence({hostId, hostDeviceId}) {
  return {
    schema: NETWORK_EVIDENCE_SCHEMA,
    path_id: 'relay',
    network_path: 'relay',
    transport: 'WSS',
    session_id: 'matrix-session',
    run_id: runId,
    identity: {account_id: 'alice', host_id: hostId, device_id: hostDeviceId},
    signaling: {
      status: 'proved',
      evidence: ['api-transcript.json'],
    },
    data_plane: {
      status: 'proved',
      evidence: ['relay-data-plane.json'],
    },
    media: {
      status: 'unknown',
      decoded_frames: 0,
      displayed: false,
      transport_acknowledgements: 0,
      evidence: ['relay-data-plane.json'],
      reason: 'Relay forwarded opaque bytes; no Browser decoder or displayed native frame ran in this runner',
    },
    operation_evidence: {
      status: 'unknown',
      evidence: [],
      reason: 'Relay owns signaling and tunnel forwarding, not Browser operation execution or receipts',
    },
    operation_receipts: [],
    source: {
      root: evidenceRoot,
      files: ['api-transcript.json', 'relay-data-plane.json'],
      run_id: runId,
      producer: 'scripts/relay/network-matrix.mjs',
    },
    result: 'UNPROVEN',
  };
}

function projectPathEvidence(relayEvidence) {
  const external = pathEvidenceRoot === undefined
    ? {root: undefined, records: [], sources: []}
    : loadNetworkPathEvidence(pathEvidenceRoot);
  const records = external.records.some(item => item.path_id === 'relay')
    ? external.records
    : [relayEvidence, ...external.records];
  const rows = projectNetworkMatrix(records);
  const networkResult = rows.some(row => row.result === 'FAIL')
    ? 'FAIL'
    : rows.every(row => row.result === 'PASS')
      ? 'PASS'
      : 'UNPROVEN';
  return {external, records, rows, networkResult};
}

function sourceFile(root, path, label) {
  const absolute = requiredFile(join(root, path), label);
  return {path, sha256: sha256File(absolute)};
}

function waitForChildClose(child, timeout = 5000) {
  if (child.exitCode !== null || child.signalCode) {
    return Promise.resolve({closed: true, exitCode: child.exitCode, signal: child.signalCode});
  }
  return Promise.race([
    new Promise(resolveClose => child.once('close', (exitCode, signal) => resolveClose({closed: true, exitCode, signal}))),
    new Promise(resolveTimeout => setTimeout(() => resolveTimeout({closed: false, timeout: true}), timeout)),
  ]);
}

function simulateCleanupFailure() {
  const cleanup = {
    result: 'FAIL',
    runtime: {
      stopped: false,
      identity_before_verified: false,
      pid_exit_verified: false,
    },
    listener: {checked: true, closed: false},
    fixture_removed: false,
  };
  const matrixResult = {
    schema: 'agentbrowser.relay.network-matrix.v2',
    result: 'PASS',
    cleanup: 'PASS',
    simulation: 'cleanup failure',
  };
  applyCleanupResult(matrixResult, cleanup);
  writeEvidence('cleanup.json', cleanup);
  return matrixResult;
}

async function runNegativeChecks() {
  const mismatchChild = spawn(process.execPath, ['-e', 'setTimeout(() => {}, 5000)'], {
    cwd: selfRoot,
    stdio: ['ignore', 'ignore', 'ignore'],
  });
  await new Promise(resolveSpawn => mismatchChild.once('spawn', resolveSpawn));
  const originalKill = mismatchChild.kill.bind(mismatchChild);
  let killCalls = 0;
  mismatchChild.kill = signal => {
    killCalls += 1;
    return originalKill(signal);
  };
  const negativeHash = sha256File(scriptPath);
  const expected = {
    pid: mismatchChild.pid,
    argv: [process.execPath, '-e', 'identity-mismatch'],
    cwd: selfRoot,
    spawnfile: mismatchChild.spawnfile,
    spawnargs: mismatchChild.spawnargs,
    executable: {path: process.execPath, sha256: sha256File(process.execPath)},
    entrypoint: {archive: 'negative-check-entrypoint', path: scriptPath, sha256: negativeHash},
    artifact: {path: scriptPath, sha256: negativeHash, source_commit: 'negative', source_tree: 'negative'},
  };
  const stop = await stopChild(mismatchChild, expected);
  const blocked = killCalls === 0 && stop.signal_sent === false && stop.identity_before_verified === false;
  assert.equal(blocked, true, `Identity mismatch must block SIGTERM: ${JSON.stringify({killCalls, stop})}`);
  const aliveAfterBlock = observeProcess(mismatchChild.pid).alive;
  assert.equal(aliveAfterBlock, true, 'Identity mismatch negative check must leave the child running');
  originalKill('SIGTERM');
  const closed = await waitForChildClose(mismatchChild);
  assert.equal(closed.closed, true, `Negative identity child did not close: ${JSON.stringify(closed)}`);

  const simulatedEvidenceRoot = join(evidenceRoot, 'simulated-cleanup-failure');
  mkdirSync(simulatedEvidenceRoot, {recursive: true});
  const simulated = runSync(process.execPath, [
    scriptPath,
    '--simulate-cleanup-failure',
    '--evidence-root',
    simulatedEvidenceRoot,
  ], {cwd: selfRoot, timeout: 30_000});
  const simulatedLines = simulated.stdout.trim().split('\n').filter(Boolean);
  const simulatedResult = simulatedLines.length > 0 ? JSON.parse(simulatedLines.at(-1)) : undefined;
  assert.equal(simulated.status, 1, `Simulated cleanup failure must exit non-zero: ${JSON.stringify(simulated)}`);
  assert.equal(simulatedResult?.result, 'FAIL', `Simulated cleanup failure must report top-level FAIL: ${JSON.stringify(simulatedResult)}`);
  assert.equal(simulatedResult?.cleanup, 'FAIL', `Simulated cleanup failure must report cleanup FAIL: ${JSON.stringify(simulatedResult)}`);
  return {
    schema: 'agentbrowser.relay.network-matrix.negative.v1',
    result: 'PASS',
    identity_mismatch: {
      result: 'PASS',
      kill_calls: killCalls,
      signal_sent: stop.signal_sent,
      identity_before_verified: stop.identity_before_verified,
      mismatches: stop.identity_mismatches,
      child_closed_after_test: closed.closed,
    },
    cleanup_failure: {
      result: 'PASS',
      child_status: simulated.status,
      child_result: simulatedResult?.result,
      child_cleanup: simulatedResult?.cleanup,
      evidence_root: simulatedEvidenceRoot,
    },
  };
}

async function main() {
  checkFixedAuthVector();
  const runnerStatusBefore = gitStatus(selfRoot);
  const runnerIdentity = {
    root: selfRoot,
    commit: git(selfRoot, 'rev-parse', 'HEAD'),
    tree: git(selfRoot, 'rev-parse', 'HEAD^{tree}'),
    script: relative(selfRoot, scriptPath),
    script_sha256: sha256File(scriptPath),
    clean_before: runnerStatusBefore === '',
    status_before: runnerStatusBefore,
  };
  assert.equal(runnerStatusBefore, '', `Runner worktree must be clean before replay: ${runnerStatusBefore}`);
  runContext.source = {runner: runnerIdentity};

  const candidateStatusBefore = gitStatus(candidateRoot);
  assert.equal(candidateStatusBefore, '', `Relay candidate worktree must be clean before build: ${candidateStatusBefore}`);
  const candidateCommit = git(candidateRoot, 'rev-parse', 'HEAD');
  const candidateTree = git(candidateRoot, 'rev-parse', 'HEAD^{tree}');
  const runtimeSources = [
    sourceFile(candidateRoot, 'services/relay/src/main.ts', 'Relay runtime main source'),
    sourceFile(candidateRoot, 'services/relay/src/server.ts', 'Relay runtime server source'),
    sourceFile(candidateRoot, 'services/relay/src/store.ts', 'Relay runtime store source'),
  ];
  const protocolSource = sourceFile(candidateRoot, 'protocol/relay/index.ts', 'Relay protocol source');
  const buildSource = sourceFile(candidateRoot, 'services/relay/build-artifact.mjs', 'Relay artifact build source');
  const packageSource = sourceFile(candidateRoot, 'services/relay/package.json', 'Relay runtime package');
  const artifactPath = join(candidateRoot, 'generated/modules/relay-service/lib/relay.tar');
  const fixtureRoot = mkdtempSync(join(tmpdir(), 'agentbrowser-network-matrix-'));
  const artifactRoot = mkdtempSync(join(fixtureRoot, 'artifact-'));
  const certFile = join(fixtureRoot, 'cert.pem');
  const keyFile = join(fixtureRoot, 'key.pem');
  const dbFile = join(fixtureRoot, 'relay.sqlite');
  let runtimeChild;
  let runtimeStdout = '';
  let runtimeStderr = '';
  let runtimeBase;
  let runtimeCa;
  let runtimeExpected;
  let runtimeIdentity;
  let artifactBuild;
  let cleanup = {result: 'PENDING', fixture_root: fixtureRoot};
  let candidateIdentity;
  let matrixResult;

  try {
    const openssl = runSync('openssl', [
      'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', keyFile, '-out', certFile,
      '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost',
    ], {cwd: candidateRoot, timeout: 30_000});
    writeEvidence('tls-cert.log', `${openssl.stdout}${openssl.stderr}`);
    assertRun(openssl, 'TLS certificate generation');
    runtimeCa = readFileSync(certFile);
    const cert = runtimeCa;

    artifactBuild = runSync('npm', ['--prefix', 'services/relay', 'run', 'build'], {cwd: candidateRoot});
    writeEvidence('artifact-build.log', `${artifactBuild.stdout}${artifactBuild.stderr}`);
    assertRun(artifactBuild, 'Relay artifact build');
    requiredFile(artifactPath, 'Relay artifact');
    const artifactHash = sha256File(artifactPath);
    const archiveList = runSync('tar', ['-tf', artifactPath], {cwd: candidateRoot});
    writeEvidence('artifact-list.log', archiveList.stdout + archiveList.stderr);
    assertRun(archiveList, 'Relay artifact listing');
    const archiveEntries = archiveList.stdout.split('\n').map(entry => entry.replace(/^\.\//, '')).filter(Boolean);
    const entrypointArchive = 'dist/services/relay/src/main.js';
    assert.ok(archiveEntries.includes(entrypointArchive), `Artifact missing ${entrypointArchive}`);
    assert.ok(archiveEntries.includes('dist/protocol/relay/index.js'), 'Artifact missing compiled relay protocol');

    const extraction = runSync('tar', ['-xf', artifactPath, '-C', artifactRoot], {cwd: candidateRoot});
    writeEvidence('artifact-extract.log', extraction.stdout + extraction.stderr);
    assertRun(extraction, 'Relay artifact extraction');
    const entrypoint = join(artifactRoot, entrypointArchive);
    requiredFile(entrypoint, 'Compiled Relay entrypoint');
    const entrypointHash = sha256File(entrypoint);
    const executablePath = realpathSync(process.execPath);
    const executableHash = sha256File(executablePath);
    const artifactPackagePath = requiredFile(join(artifactRoot, 'package.json'), 'Compiled Relay package metadata');
    const artifactPackage = JSON.parse(readFileSync(artifactPackagePath, 'utf8'));
    const sourcePackage = JSON.parse(readFileSync(join(candidateRoot, packageSource.path), 'utf8'));
    assert.equal(artifactPackage.name, sourcePackage.name, 'Artifact package name drift');
    assert.equal(artifactPackage.version, sourcePackage.version, 'Artifact package version drift');
    const artifactProtocol = readFileSync(join(artifactRoot, 'dist/protocol/relay/index.js'), 'utf8');
    assert.match(artifactProtocol, new RegExp(`['"]${RELAY_ABI_ID}['"]`), 'Artifact ABI identity drift');
    candidateIdentity = {
      root: candidateRoot,
      commit: candidateCommit,
      tree: candidateTree,
      clean_before: candidateStatusBefore === '',
      runtime_sources: runtimeSources,
      protocol_source: protocolSource,
      artifact_build: {
        command: artifactBuild.command,
        cwd: candidateRoot,
        source_commit: candidateCommit,
        source_tree: candidateTree,
        build_script: buildSource,
        package: packageSource,
        output_log: join(evidenceRoot, 'artifact-build.log'),
      },
      artifact: {
        path: artifactPath,
        sha256: artifactHash,
        source_commit: candidateCommit,
        source_tree: candidateTree,
        entrypoint: entrypointArchive,
        entrypoint_sha256: entrypointHash,
        extracted: true,
        executed: false,
      },
    };
    runContext.source.relay = candidateIdentity;

    const requireFromArtifact = createRequire(pathToFileURL(artifactPackagePath));
    ({WebSocket} = requireFromArtifact('ws'));
    const accountAdd = (username, password) => {
      const result = runSync(process.execPath, [entrypoint, 'account-add', dbFile, username], {
        cwd: artifactRoot,
        input: `${password}\n`,
      });
      writeEvidence(`account-add-${username}.log`, `${result.stdout}${result.stderr}`);
      assertRun(result, `Compiled artifact account-add ${username}`);
    };
    accountAdd('alice', 'alice matrix password');
    accountAdd('bob', 'bob matrix password');

    const stdoutState = {value: ''};
    const runtimeArgs = [entrypoint, 'serve', dbFile, certFile, keyFile];
    const runtimeArgv = [process.execPath, ...runtimeArgs];
    runtimeChild = spawn(process.execPath, runtimeArgs, {
      cwd: artifactRoot,
      env: {...process.env, RELAY_PORT: '0', RELAY_BIND: '127.0.0.1'},
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    runtimeExpected = {
      pid: runtimeChild.pid,
      argv: runtimeArgv,
      cwd: artifactRoot,
      spawnfile: runtimeChild.spawnfile,
      spawnargs: runtimeChild.spawnargs,
      executable: {path: process.execPath, sha256: executableHash},
      entrypoint: {archive: entrypointArchive, path: entrypoint, sha256: entrypointHash},
      artifact: {
        path: artifactPath,
        sha256: artifactHash,
        source_commit: candidateCommit,
        source_tree: candidateTree,
      },
    };
    runtimeChild.stdout.setEncoding('utf8');
    runtimeChild.stderr.setEncoding('utf8');
    runtimeChild.stdout.on('data', chunk => { runtimeStdout += chunk; stdoutState.value = runtimeStdout; });
    runtimeChild.stderr.on('data', chunk => { runtimeStderr += chunk; });
    const base = await waitForListening(runtimeChild, stdoutState);
    runtimeBase = base;
    const observedRuntime = observeProcess(runtimeChild.pid);
    const runtimeCheck = runtimeProcessCheck(observedRuntime, runtimeExpected);
    assert.equal(runtimeCheck.verified, true, `Runtime artifact identity mismatch: ${runtimeCheck.mismatches.join('; ')}`);
    runtimeIdentity = runtimeProcessEvidence(observedRuntime, runtimeExpected, runtimeCheck);
    candidateIdentity.artifact.executed = true;
    candidateIdentity.artifact.runtime_identity = runtimeIdentity;
    const endpoint = `${base.replace('https:', 'wss:')}/v2/control/client`;
    const health = await api(base, '/health', 'GET', undefined, undefined, cert);
    assert.equal(health.status, 200);

    const aliceLogin = await api(base, '/v2/login', 'POST', undefined, {
      username: 'alice', password: 'alice matrix password',
    }, cert);
    const bobLogin = await api(base, '/v2/login', 'POST', undefined, {
      username: 'bob', password: 'bob matrix password',
    }, cert);
    assert.equal(aliceLogin.status, 200);
    assert.equal(bobLogin.status, 200);
    const aliceToken = aliceLogin.body.token;
    const bobToken = bobLogin.body.token;
    const hostDevice = await enroll(base, aliceToken, 'matrix-host', cert);
    const clientDevice = await enroll(base, aliceToken, 'matrix-client', cert);
    const bobDevice = await enroll(base, bobToken, 'other-account', cert);
    const hostRegistration = await api(base, '/v2/hosts', 'POST', aliceToken, {deviceId: hostDevice.id}, cert);
    assert.equal(hostRegistration.status, 201);
    const hostId = hostRegistration.body.id;
    const foreignHost = await api(base, '/v2/hosts', 'POST', bobToken, {deviceId: hostDevice.id}, cert);
    assert.equal(foreignHost.status, 404);

    const sockets = [];
    const authTampered = {};
    try {
      for (const tamper of ['path', 'device', 'token']) {
        const bad = await connectControl(base, '/v2/control/client', clientDevice, cert, tamper);
        sockets.push(bad.ws);
        const error = await bad.take('error');
        assert.equal(error.code, 'UNAUTHORIZED', `Auth ${tamper} tamper must fail closed`);
        authTampered[tamper] = error.code;
      }

      const host = await connectControl(base, `/v2/control/host/${hostId}`, hostDevice, cert);
      const client = await connectControl(base, '/v2/control/client', clientDevice, cert);
      const bob = await connectControl(base, '/v2/control/client', bobDevice, cert);
      sockets.push(host.ws, client.ws, bob.ws);
      assert.equal((await host.take('auth.ok')).deviceId, hostDevice.id);
      assert.equal((await client.take('auth.ok')).deviceId, clientDevice.id);
      assert.equal((await bob.take('auth.ok')).deviceId, bobDevice.id);

      host.ws.send(control('host.publish', {
        hostId,
        snapshot: {incarnation: 'matrix-host', revision: 1, endpoints: [], sessions: [{id: 'matrix-session'}]},
      }));
      let directory;
      do { directory = await client.take('directory.snapshot'); }
      while (!directory.hosts.some(item => item.hostId === hostId));
      assert.equal(directory.hosts.find(item => item.hostId === hostId).snapshot.sessions[0].id, 'matrix-session');
      const bobDirectory = await api(base, '/v2/directory', 'GET', bobToken, undefined, cert);
      assert.equal(bobDirectory.status, 200);
      assert.deepEqual(bobDirectory.body.hosts, []);

      bob.ws.send(control('tunnel.open', {hostId, sessionId: 'matrix-session'}));
      const wrongPeer = await bob.take('error');
      assert.equal(wrongPeer.code, 'HOST_UNAVAILABLE');
      client.ws.send(control('tunnel.open', {hostId, sessionId: 'missing-session'}));
      const wrongSession = await client.take('error');
      assert.equal(wrongSession.code, 'SESSION_UNAVAILABLE');

      client.ws.send(control('tunnel.open', {hostId, sessionId: 'matrix-session'}));
      const clientOffer = await client.take('tunnel.offer');
      const hostOffer = await host.take('tunnel.offer');
      const offerFields = ['tunnelId', 'hostId', 'sessionId'];
      for (const field of offerFields) assert.equal(clientOffer[field], hostOffer[field], `Offer ${field} mismatch`);
      assert.equal(clientOffer.hostId, hostId);
      assert.equal(clientOffer.sessionId, 'matrix-session');
      assert.equal(clientOffer.peerDeviceId, hostDevice.id);
      assert.equal(hostOffer.peerDeviceId, clientDevice.id);
      assert.equal(clientOffer.side, 0);
      assert.equal(hostOffer.side, 1);
      for (const name of ['control', 'media']) {
        assert.match(clientOffer.channels[name].path, new RegExp(`/v2/tunnel/${clientOffer.tunnelId}/${name}/0$`));
        assert.match(hostOffer.channels[name].path, new RegExp(`/v2/tunnel/${hostOffer.tunnelId}/${name}/1$`));
      }

      const wrongChannelPath = clientOffer.channels.control.path.replace('/control/', '/media/');
      const wrongChannel = await rejectedUpgrade(
        `${base.replace('https:', 'wss:')}${wrongChannelPath}`,
        {ca: cert, headers: {authorization: `Bearer ${clientOffer.channels.control.ticket}`}},
      );
      assert.equal(wrongChannel.status, 401);
      const wrongPath = clientOffer.channels.control.path.replace(clientOffer.tunnelId, `${clientOffer.tunnelId}-wrong`);
      const wrongPathResult = await rejectedUpgrade(
        `${base.replace('https:', 'wss:')}${wrongPath}`,
        {ca: cert, headers: {authorization: `Bearer ${clientOffer.channels.control.ticket}`}},
      );
      assert.equal(wrongPathResult.status, 401);

      const channels = {
        control: [await channel(base, clientOffer, 'control', cert), await channel(base, hostOffer, 'control', cert)],
        media: [await channel(base, clientOffer, 'media', cert), await channel(base, hostOffer, 'media', cert)],
      };
      for (const pair of Object.values(channels)) {
        for (const inbox of pair) sockets.push(inbox.ws);
      }
      const ready = {};
      for (const [name, pair] of Object.entries(channels)) {
        ready[name] = await Promise.all(pair.map(inbox => inbox.take('channel.ready')));
        for (const message of ready[name]) {
          assert.equal(message.tunnelId, clientOffer.tunnelId);
          assert.equal(message.channel, name);
        }
      }
      const controlBytes = Buffer.from('opaque-control-frame');
      const mediaBytes = Buffer.from([0, 1, 255, 13, 10, 2]);
      channels.control[0].ws.send(controlBytes);
      channels.media[1].ws.send(mediaBytes);
      assert.deepEqual(await channels.control[1].take('binary'), controlBytes);
      assert.deepEqual(await channels.media[0].take('binary'), mediaBytes);
      const separationEvidence = await assertNoBinaryDuringWindow([
        {label: 'control[0] sender', inbox: channels.control[0]},
        {label: 'control[1] receiver', inbox: channels.control[1]},
        {label: 'media[0] receiver', inbox: channels.media[0]},
        {label: 'media[1] sender', inbox: channels.media[1]},
      ]);

      const consumed = clientOffer.channels.media;
      const replay = await rejectedUpgrade(
        `${base.replace('https:', 'wss:')}${consumed.path}`,
        {ca: cert, headers: {authorization: `Bearer ${consumed.ticket}`}},
      );
      assert.equal(replay.status, 401);

      const revoke = await api(base, '/v2/token', 'DELETE', aliceToken, undefined, cert);
      assert.equal(revoke.status, 200);
      const closeReasons = [await client.take('tunnel.closed'), await host.take('tunnel.closed')].map(item => item.reason);
      assert.ok(closeReasons.includes('UNAUTHORIZED'));
      await Promise.all(sockets.filter(socket => socket !== bob.ws).map(socket => waitForClose(socket)));
      await closeSocket(bob.ws);

      const candidateStatusAfter = gitStatus(candidateRoot);
      assert.equal(candidateStatusAfter, '', `Relay candidate changed during replay: ${candidateStatusAfter}`);
      assert.equal(git(candidateRoot, 'rev-parse', 'HEAD'), candidateCommit);
      assert.equal(git(candidateRoot, 'rev-parse', 'HEAD^{tree}'), candidateTree);
      candidateIdentity.clean_after = candidateStatusAfter === '';
      candidateIdentity.status_after = candidateStatusAfter;
      candidateIdentity.artifact.sha256 = sha256File(artifactPath);
      assert.equal(candidateIdentity.artifact.sha256, artifactHash, 'Relay artifact changed during replay');
      const runnerStatusAfter = gitStatus(selfRoot);
      runnerIdentity.clean_after = runnerStatusAfter === '';
      runnerIdentity.status_after = runnerStatusAfter;
      assert.equal(runnerStatusAfter, '', `Runner worktree changed during replay: ${runnerStatusAfter}`);

      const relayDataPlane = {
        result: 'PASS',
        network_path: 'relay',
        transport: 'WSS',
        tunnel_id: clientOffer.tunnelId,
        control: {forwarded_bytes: controlBytes.length},
        media: {forwarded_bytes: mediaBytes.length},
        opaque: true,
        decoded_frames: 0,
        displayed: false,
        operation_receipts: 0,
        note: 'Relay forwarding proves only authenticated opaque data-plane delivery; Browser decoding and operation execution are separate owners.',
      };
      writeEvidence('relay-data-plane.json', relayDataPlane);
      writeEvidence('api-transcript.json', apiEvents);
      const relayEvidence = relayPathEvidence({hostId, hostDeviceId: hostDevice.id});
      writeEvidence('relay-path-evidence.json', relayEvidence);
      const pathProjection = projectPathEvidence(relayEvidence);
      runContext.pathEvidence = {
        external_root: pathProjection.external.root,
        external_sources: pathProjection.external.sources,
        records: pathProjection.records,
        rows: pathProjection.rows,
        result: pathProjection.networkResult,
      };
      writeEvidence('path-evidence.json', runContext.pathEvidence);

      matrixResult = {
        schema: 'agentbrowser.relay.network-matrix.v2',
        result: pathProjection.networkResult === 'FAIL' ? 'FAIL' : 'PASS',
        relay_result: 'PASS',
        network_matrix_result: pathProjection.networkResult,
        network_evidence_schema: NETWORK_EVIDENCE_SCHEMA,
        source: {runner: runnerIdentity, relay: candidateIdentity},
        candidate: candidateIdentity,
        artifact_execution: {
          entrypoint: `relay.tar:${entrypointArchive} serve (HTTPS/WSS)`,
          health: health.status,
          endpoint,
          process: 'compiled artifact child process',
          process_identity: runtimeIdentity,
        },
        network_matrix: pathProjection.rows,
        path_evidence: {
          schema: NETWORK_EVIDENCE_SCHEMA,
          external_root: pathProjection.external.root,
          external_sources: pathProjection.external.sources,
          relay_record: relayEvidence,
        },
        assertions: {
          tls: 'verified with task CA',
          signed_device_authentication: 'PASS',
          fixed_auth_vector: {
            digest: FIXED_AUTH_VECTOR.tokenDigest,
            transcript_sha256: sha256Bytes(Buffer.from(FIXED_AUTH_VECTOR.transcriptHex, 'hex')),
          },
          auth_tamper: authTampered,
          account_isolation: {
            foreign_host_registration: foreignHost.status,
            foreign_directory_hosts: bobDirectory.body.hosts.length,
            wrong_peer: wrongPeer.code,
          },
          wrong_session: wrongSession.code,
          tunnel_offer_identity: {
            tunnelId: clientOffer.tunnelId,
            hostId: clientOffer.hostId,
            sessionId: clientOffer.sessionId,
            client_peerDeviceId: clientOffer.peerDeviceId,
            host_peerDeviceId: hostOffer.peerDeviceId,
            client_side: clientOffer.side,
            host_side: hostOffer.side,
          },
          channel_ready_identity: ready,
          channel_paths: {
            control: [clientOffer.channels.control.path, hostOffer.channels.control.path],
            media: [clientOffer.channels.media.path, hostOffer.channels.media.path],
          },
          negative_tunnel_replay: {
            wrong_channel: wrongChannel.status,
            wrong_path: wrongPathResult.status,
            ticket_replay: replay.status,
          },
          control_binary: controlBytes.length,
          media_binary: mediaBytes.length,
          control_media_separation: 'PASS',
          control_media_separation_evidence: separationEvidence,
          relay_data_plane: relayDataPlane,
          relay_media: {
            result: 'UNPROVEN',
            reason: 'No decoded or displayed Browser frame was observed by the Relay runner',
          },
          relay_operations: {
            result: 'UNPROVEN',
            reason: 'No Browser operation receipt with operation ID and generation was emitted by the Relay runner',
          },
          token_revoke: revoke.status,
          revoke_close: closeReasons,
        },
        cleanup: 'PASS',
      };
    } finally {
      for (const socket of sockets) {
        if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) socket.terminate();
      }
    }
  } finally {
    let runtimeStop;
    try {
      runtimeStop = await stopChild(runtimeChild, runtimeExpected);
    } catch (error) {
      runtimeStop = {
        stopped: false,
        signal_sent: false,
        pid_exit_verified: false,
        identity_before_verified: false,
        error: `stopChild failed: ${error instanceof Error ? error.message : String(error)}`,
      };
    }
    let listener;
    try {
      listener = runtimeBase && runtimeCa
        ? await waitForListenerClosed(runtimeBase, runtimeCa)
        : {checked: false, closed: false, reason: 'Runtime listener was never observed'};
    } catch (error) {
      listener = {
        checked: false,
        closed: false,
        error: `listenerClosed failed: ${error instanceof Error ? error.message : String(error)}`,
      };
    }
    runtimeChild = undefined;
    writeEvidence('runtime-stdout.log', runtimeStdout);
    writeEvidence('runtime-stderr.log', runtimeStderr);
    cleanup = {...cleanup, runtime: runtimeStop, listener};
    const candidateStatusAfter = gitStatus(candidateRoot);
    cleanup.candidate_clean_after = candidateStatusAfter === '';
    cleanup.candidate_status_after = candidateStatusAfter;
    const runnerStatusAfter = gitStatus(selfRoot);
    cleanup.runner_clean_after = runnerStatusAfter === '';
    cleanup.runner_status_after = runnerStatusAfter;
    if (runContext.source?.runner) {
      runContext.source.runner.clean_after = runnerStatusAfter === '';
      runContext.source.runner.status_after = runnerStatusAfter;
    }
    try {
      if (existsSync(fixtureRoot)) rmSync(fixtureRoot, {recursive: true, force: true});
      cleanup.fixture_removed = !existsSync(fixtureRoot);
    } catch (error) {
      cleanup.fixture_removed = false;
      cleanup.fixture_remove_error = error instanceof Error ? error.message : String(error);
    }
    cleanup.result = cleanup.fixture_removed
      && runtimeStop.stopped
      && !runtimeStop.timeout
      && runtimeStop.identity_before_verified
      && runtimeStop.pid_exit_verified
      && listener.closed
      && cleanup.candidate_clean_after
      && cleanup.runner_clean_after
      ? 'PASS'
      : 'FAIL';
    applyCleanupResult(matrixResult, cleanup);
    runContext.cleanup = cleanup;
    writeEvidence('cleanup.json', cleanup);
  }
  return matrixResult;
}

const runMode = args.includes('--negative-checks')
  ? 'negative'
  : args.includes('--simulate-cleanup-failure')
    ? 'simulate-cleanup-failure'
    : 'matrix';
const runPromise = runMode === 'negative'
  ? runNegativeChecks()
  : runMode === 'simulate-cleanup-failure'
    ? Promise.resolve(simulateCleanupFailure())
    : main();

runPromise.then(result => {
  if (runMode === 'matrix') writeEvidence('api-transcript.json', apiEvents);
  const evidenceName = runMode === 'negative' ? 'negative-checks.json' : 'network-matrix.json';
  writeEvidence(evidenceName, result);
  process.stdout.write(`${JSON.stringify(result)}\n`);
  if (result.result !== 'PASS') process.exitCode = 1;
}).catch(error => {
  const failure = {
    schema: runMode === 'negative'
      ? 'agentbrowser.relay.network-matrix.negative.v1'
      : 'agentbrowser.relay.network-matrix.v2',
    result: 'FAIL',
    error: error instanceof Error ? error.message : String(error),
    source: runContext.source,
    cleanup: runContext.cleanup,
  };
  if (runMode === 'matrix') writeEvidence('api-transcript.json', apiEvents);
  const evidenceName = runMode === 'negative' ? 'negative-checks.json' : 'network-matrix.json';
  writeEvidence(evidenceName, failure);
  process.stderr.write(`${failure.error}\n`);
  process.exitCode = 1;
});

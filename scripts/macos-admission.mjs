// Formal Mac AppSDK admission: exact artifact install, restart and UI replay.
import {spawn, spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createWriteStream, existsSync, mkdirSync, readFileSync, writeFileSync, copyFileSync} from 'node:fs';
import {hostname, userInfo} from 'node:os';
import {resolve, join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {createInterface} from 'node:readline';
import assert from 'node:assert/strict';

const moduleId = 'macos-shell';
const issueId = 'macos-shell';
const root = resolve(process.cwd());
const pinnedTools = resolve('evidence/pinned-tools');
const protocolRoot = resolve(process.env.OBSCURA_PROTOCOL_ROOT ?? '');
const binaryRoot = resolve(process.env.OBSCURA_BIN_DIR ?? '');
const bindIp = process.env.OBSCURA_ENDPOINT_BIND_IP;
const appProcessName = 'AgentBrowserMac';
const appWindowName = 'AgentBrowser';
const appBundleName = 'AgentBrowserMac.app';
const appExecutableName = 'AgentBrowserMac';
const hash = value => `sha256:${createHash('sha256').update(value).digest('hex')}`;
const now = () => new Date().toISOString();
const sleep = milliseconds => delay(milliseconds);

function persist(path, value) {
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, {flag: 'wx'});
}

function command(program, args, options = {}) {
  const result = spawnSync(program, args, {
    cwd: options.cwd ?? root,
    env: options.env ?? process.env,
    encoding: 'buffer',
    maxBuffer: 64 * 1024 * 1024,
    timeout: options.timeout ?? 300_000,
    input: options.input,
  });
  const output = Buffer.concat([result.stdout ?? Buffer.alloc(0), result.stderr ?? Buffer.alloc(0)]);
  if (options.log) writeFileSync(options.log, output);
  assert(!result.error && result.status === 0,
    `${program} ${args.join(' ')} failed (${result.status}): ${result.error ?? output.toString()}`);
  return result.stdout ?? Buffer.alloc(0);
}

function optionalCommand(program, args, options = {}) {
  return spawnSync(program, args, {
    cwd: options.cwd ?? root,
    env: options.env ?? process.env,
    encoding: options.encoding ?? 'utf8',
    maxBuffer: 8 * 1024 * 1024,
    timeout: options.timeout ?? 10_000,
  });
}

const git = (...args) => command('git', args).toString().trim();
const shortHash = value => value.slice(0, 12);

function assertFile(path) {
  assert(existsSync(path), `Required file missing: ${path}`);
  return path;
}

function psRows() {
  const output = command('ps', ['-axo', 'pid=,ppid=,command=']).toString();
  return output.split('\n').map(line => {
    const match = line.trim().match(/^(\d+)\s+(\d+)\s+(.*)$/);
    return match ? {pid: Number(match[1]), ppid: Number(match[2]), command: match[3]} : null;
  }).filter(Boolean);
}

function processRows(prefix) {
  return psRows().filter(row => row.command === prefix || row.command.startsWith(`${prefix} `));
}

function ownedCommand(pid, prefix) {
  const row = psRows().find(candidate => candidate.pid === pid);
  assert(row, `Owned process ${pid} is no longer observable`);
  assert(row.command === prefix || row.command.startsWith(`${prefix} `),
    `PID ${pid} is not owned by ${prefix}: ${row.command}`);
  return row;
}

function waitForChildExit(child, timeout = 10_000) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve({code: child.exitCode, signal: child.signalCode});
  }
  return new Promise((resolveExit, reject) => {
    const timer = setTimeout(() => reject(new Error(`Process ${child.pid} did not exit`)), timeout);
    child.once('exit', (code, signal) => {
      clearTimeout(timer);
      resolveExit({code, signal});
    });
  });
}

async function terminateOwned(child, executable, logPath) {
  if (!child || child.exitCode !== null || child.signalCode !== null) {
    return {pid: child?.pid ?? null, code: child?.exitCode ?? null, signal: child?.signalCode ?? null};
  }
  ownedCommand(child.pid, executable);
  child.kill('SIGTERM');
  let result;
  try {
    result = await waitForChildExit(child, 8_000);
  } catch {
    ownedCommand(child.pid, executable);
    child.kill('SIGKILL');
    result = await waitForChildExit(child, 8_000);
  }
  if (logPath) writeFileSync(logPath, JSON.stringify({pid: child.pid, ...result}, null, 2) + '\n');
  return {pid: child.pid, ...result};
}

function osa(script, options = {}) {
  return command('osascript', ['-e', script], options).toString().trim();
}

function osaPoll(script) {
  return optionalCommand('osascript', ['-e', script], {encoding: 'utf8'});
}

function uiScript(body) {
  return `tell application "System Events"\n  tell process "${appProcessName}"\n    set frontmost to true\n    ${body}\n  end tell\nend tell`;
}

function windowRect() {
  const output = osaPoll(uiScript(`get {position, size} of window "${appWindowName}"`));
  if (output.status !== 0) return null;
  const values = String(output.stdout).match(/-?\d+/g)?.map(Number) ?? [];
  if (values.length < 4) return null;
  return {x: values[0], y: values[1], width: values[2], height: values[3]};
}

async function waitForWindow(child) {
  let rect;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error(`App exited before window appeared: ${child.exitCode}/${child.signalCode}`);
    }
    rect = windowRect();
    if (rect && rect.width >= 900 && rect.height >= 600) return rect;
    await sleep(100);
  }
  throw new Error('Timed out waiting for the installed AppKit window');
}

function point(rect, x, y) {
  return {x: Math.round(rect.x + x), y: Math.round(rect.y + y)};
}

function clickRelative(x, y) {
  const rect = windowRect();
  assert(rect, 'AgentBrowser window disappeared before click');
  const target = point(rect, x, y);
  osa(uiScript(`click at {${target.x}, ${target.y}}`));
}

function typeAscii(text) {
  assert(!/[^\x20-\x7e]/.test(text), 'ASCII keyboard helper received non-ASCII text');
  const escaped = text.replaceAll('\\', '\\\\').replaceAll('"', '\\"');
  osa(uiScript(`keystroke "${escaped}"`));
}

function setAccessibilityValue(pid, description, value, logPath) {
  const swift = `import ApplicationServices; import Foundation; let pid: pid_t = ${pid}; let text = ${JSON.stringify(value)}; let target = ${JSON.stringify(description)}; let app = AXUIElementCreateApplication(pid); func value(_ element: AXUIElement, _ attribute: String) -> CFTypeRef? { var result: CFTypeRef?; let error = AXUIElementCopyAttributeValue(element, attribute as CFString, &result); return error == .success ? result : nil }; func find(_ element: AXUIElement) -> Bool { let role = value(element, kAXRoleAttribute) as? String; let description = value(element, kAXDescriptionAttribute) as? String; if role == kAXTextFieldRole && description == target { return AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, text as CFTypeRef) == .success }; let children = value(element, kAXChildrenAttribute) as? [AXUIElement] ?? []; return children.contains(where: find) }; guard find(app) else { exit(2) }`;
  command('swift', ['-e', swift], {log: logPath, timeout: 30_000});
}

function pressAccessibilityButton(pid, title, logPath) {
  const swift = `import ApplicationServices; import Foundation; let pid: pid_t = ${pid}; let target = ${JSON.stringify(title)}; let app = AXUIElementCreateApplication(pid); func value(_ element: AXUIElement, _ attribute: String) -> CFTypeRef? { var result: CFTypeRef?; let error = AXUIElementCopyAttributeValue(element, attribute as CFString, &result); return error == .success ? result : nil }; func find(_ element: AXUIElement) -> Bool { let role = value(element, kAXRoleAttribute) as? String; let labels = [value(element, kAXTitleAttribute), value(element, kAXDescriptionAttribute), value(element, kAXValueAttribute)].compactMap { $0 as? String }; if role == kAXButtonRole && labels.contains(target) { return AXUIElementPerformAction(element, kAXPressAction as CFString) == .success }; let children = value(element, kAXChildrenAttribute) as? [AXUIElement] ?? []; return children.contains(where: find) }; guard find(app) else { exit(2) }`;
  command('swift', ['-e', swift], {log: logPath, timeout: 30_000});
}

function postScroll(pid, x, y, delta, logPath) {
  const swift = `import CoreGraphics; let source = CGEventSource(stateID: .hidSystemState); let event = CGEvent(scrollWheelEvent2Source: source, units: .line, wheelCount: 1, wheel1: ${delta}, wheel2: 0, wheel3: 0)!; event.location = CGPoint(x: ${x}, y: ${y}); event.setIntegerValueField(.scrollWheelEventDeltaAxis1, value: ${delta}); event.setIntegerValueField(.scrollWheelEventPointDeltaAxis1, value: ${delta}); event.setIntegerValueField(.scrollWheelEventFixedPtDeltaAxis1, value: ${delta * 65536}); event.postToPid(${pid})`;
  command('swift', ['-e', swift], {log: logPath, timeout: 30_000});
}

function captureWindow(path, full = true) {
  const rect = windowRect();
  assert(rect, 'AgentBrowser window disappeared before screenshot');
  const region = full
    ? rect
    : {x: rect.x, y: rect.y + 30, width: Math.floor(rect.width / 2), height: rect.height - 30};
  command('screencapture', ['-x', '-R', `${region.x},${region.y},${region.width},${region.height}`, path]);
  return {path, hash: hash(readFileSync(path)), region};
}

function screenshotLog(screenshots) {
  return screenshots.map(({path, hash: digest, region}) => ({path, hash: digest, region}));
}

function fixtureResponseValue(line) {
  const response = JSON.parse(line);
  assert.equal(response.type, 'result', `Fixture returned non-result: ${line}`);
  const value = response.value?.result?.value;
  if (typeof value === 'string') return JSON.parse(value);
  return value;
}

function startFixture(fixtureExecutable, directory, environment) {
  const stdoutLog = createWriteStream(join(directory, 'fixture.stdout.log'), {flags: 'wx'});
  const stderrLog = createWriteStream(join(directory, 'fixture.stderr.log'), {flags: 'wx'});
  const child = spawn(fixtureExecutable, [], {
    cwd: root,
    env: {...process.env, ...environment},
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  child.stdout.pipe(stdoutLog);
  child.stderr.pipe(stderrLog);
  const lines = [];
  const waiters = [];
  const reader = createInterface({input: child.stdout});
  reader.on('line', line => {
    const waiter = waiters.shift();
    if (waiter) waiter.resolve(line);
    else lines.push(line);
  });
  function nextLine(timeout = 20_000) {
    if (lines.length) return Promise.resolve(lines.shift());
    return new Promise((resolveLine, reject) => {
      const timer = setTimeout(() => reject(new Error('Fixture response deadline')), timeout);
      waiters.push({resolve: line => {clearTimeout(timer); resolveLine(line);}, reject});
    });
  }
  return {child, nextLine, async request(commandText) { child.stdin.write(`${commandText}\n`); return nextLine(); }};
}

async function quitFixture(fixture) {
  if (!fixture || fixture.child.exitCode !== null || fixture.child.signalCode !== null) return;
  fixture.child.stdin.write('quit\n');
  await waitForChildExit(fixture.child, 20_000);
}

async function main() {
  assert.equal(process.platform, 'darwin', 'Formal macOS admission requires Darwin');
  assert(!['main', 'master'].includes(git('branch', '--show-current')), 'Owner branch required');
  assert.equal(git('status', '--porcelain'), '', 'Commit complete candidate before admission');
  assertFile(join(pinnedTools, 'appsdk'));
  assertFile(join(protocolRoot, 'src/lib.rs'));
  assert.equal(bindIp, '127.0.0.1', 'Loopback fixture must bind 127.0.0.1');
  for (const name of ['obscura-host', 'obscura-endpoint', 'obscura-media']) assertFile(join(binaryRoot, name));

  const head = git('rev-parse', 'HEAD');
  const tree = git('rev-parse', 'HEAD^{tree}');
  const base = git('merge-base', 'HEAD', 'origin/main');
  const paths = git('diff', '--name-only', base, head).split('\n').filter(Boolean);
  assert(paths.length, 'Candidate delta required');
  const prefix = shortHash(head);
  const scopeHash = hash(JSON.stringify(paths));
  const candidateTime = now();
  const directory = `evidence/macos-admission-${prefix}-${Date.now()}`;
  const records = `.appsdk/records/evidence/${moduleId}`;
  const candidatePath = `.appsdk/records/fix-candidate-record-${moduleId}.json`;
  const validationPath = `.appsdk/records/pre-review-validation-record-${moduleId}.json`;
  assert(!existsSync(candidatePath) && !existsSync(validationPath), 'Preserve a prior admission graph');
  mkdirSync(directory, {recursive: true, mode: 0o700});
  mkdirSync(records, {recursive: true, mode: 0o700});
  const identity = `${hostname()}/${userInfo().username}/${process.version}`;
  const whiteProducer = {adapter: 'scripts/macos-admission.mjs:compile-and-replay', identity};
  const blackProducer = {adapter: 'scripts/macos-admission.mjs:installed-appkit-ui', identity};
  const admissionEnv = {...process.env, PATH: `${pinnedTools}:${process.env.PATH ?? ''}`, OBSCURA_PROTOCOL_ROOT: protocolRoot};
  const protocolFiles = [
    join(protocolRoot, 'Cargo.toml'),
    join(protocolRoot, 'src/lib.rs'),
    join(protocolRoot, '../../Cargo.toml'),
  ];
  const externalFiles = [
    ...protocolFiles,
    ...['obscura-host', 'obscura-endpoint', 'obscura-media'].map(name => join(binaryRoot, name)),
  ].map(assertFile);
  const externalHashes = externalFiles.map(path => hash(readFileSync(path)));
  const environment = `darwin/${process.arch}/${identity}`;
  const entrypoint = `${appBundleName}/Contents/MacOS/${appExecutableName}; AppKit+WebKit+VideoToolbox; installed private bundle`;
  const screenshots = [];
  const appChildren = [];
  let fixture;
  let install;
  let restart;
  let reconnect;
  let blackbox;
  let cleanup;

  try {
    command('npm', ['run', 'verify:local'], {env: admissionEnv, log: join(directory, 'whitebox.log')});
    command('appsdk', ['compile-module', '--module', 'client-connection'], {env: admissionEnv, log: join(directory, 'client-compile.log')});
    command('appsdk', ['compile-module', '--module', moduleId], {env: admissionEnv, log: join(directory, 'macos-compile.log')});
    command('cargo', [
      'build', '--release', '--locked', '-p', 'agentbrowser-android', '--example', 'device_fixture',
      '--config', `patch.crates-io.obscura-host-protocol.path="${protocolRoot}"`,
    ], {env: {...admissionEnv, CARGO_BUILD_JOBS: '2'}, log: join(directory, 'fixture-compile.log')});

    const manifestPath = `generated/modules/${moduleId}/module.compiled.json`;
    const manifestBytes = readFileSync(manifestPath);
    const manifest = JSON.parse(manifestBytes);
    assert.deepEqual(manifest.deployment_operations, ['install', 'restart']);
    const artifactPath = `generated/modules/${moduleId}/lib/AgentBrowserMac.app.zip`;
    const artifactZipHash = hash(readFileSync(artifactPath));
    const artifactEntry = manifest.artifacts.find(artifact => artifact.path === 'AgentBrowserMac.app.zip');
    assert(artifactEntry, 'Compiled Mac manifest lacks the app zip');
    assert.equal(artifactEntry.hash, artifactZipHash);
    const artifactHash = manifest.artifact_hash;
    const dependencyManifest = JSON.parse(readFileSync('generated/modules/client-connection/module.compiled.json'));
    const dependencyHash = dependencyManifest.artifact_hash;
    assert.equal(manifest.dependency_hashes.find(item => item.module_id === 'client-connection')?.artifact_hash, dependencyHash);
    const appSource = `apps/macos/build/${appBundleName}`;
    const appExecutable = join(appSource, 'Contents/MacOS', appExecutableName);
    const bridgeExecutable = join(appSource, 'Contents/MacOS/AgentBrowserMacBridge');
    assertFile(appExecutable); assertFile(bridgeExecutable);

    const installRoot = resolve(mkdtempPath('abmac-admission-'));
    const installApplications = join(installRoot, 'Applications');
    mkdirSync(installApplications, {recursive: true, mode: 0o700});
    const copiedZip = join(installRoot, 'AgentBrowserMac.app.zip');
    copyFileSync(artifactPath, copiedZip);
    assert.equal(hash(readFileSync(copiedZip)), artifactZipHash);
    command('ditto', ['-x', '-k', '--norsrc', copiedZip, installApplications], {log: join(directory, 'install-extract.log')});
    const installedApp = join(installApplications, appBundleName);
    const installedExecutable = join(installedApp, 'Contents/MacOS', appExecutableName);
    const installedBridge = join(installedApp, 'Contents/MacOS/AgentBrowserMacBridge');
    assertFile(installedExecutable); assertFile(installedBridge);
    const installedExecutableHash = hash(readFileSync(installedExecutable));
    assert.equal(installedExecutableHash, hash(readFileSync(appExecutable)));
    const installedBridgeHash = hash(readFileSync(installedBridge));
    assert.equal(installedBridgeHash, hash(readFileSync(bridgeExecutable)));
    install = {source_zip: artifactPath, copied_zip: copiedZip, installed_app: installedApp,
      artifact_hash: artifactHash, artifact_zip_hash: artifactZipHash,
      copied_zip_hash: hash(readFileSync(copiedZip)), installed_executable_hash: installedExecutableHash,
      installed_bridge_hash: installedBridgeHash, installed_at: now()};
    persist(join(directory, 'install.json'), install);

    const fixtureExecutable = resolve('target/release/examples/device_fixture');
    assertFile(fixtureExecutable);
    const fixtureEnvironment = {OBSCURA_BIN_DIR: binaryRoot, OBSCURA_ENDPOINT_BIND_IP: bindIp};
    fixture = startFixture(fixtureExecutable, directory, fixtureEnvironment);
    const fixtureInfo = JSON.parse(await fixture.nextLine());
    assert.match(fixtureInfo.fixture, /^\/tmp\/an-[a-z0-9]+$/);
    assert.match(fixtureInfo.endpoint, /^wss:\/\/127\.0\.0\.1:\d+$/);
    assert.equal(typeof fixtureInfo.session, 'string');
    install.fixture = {root: fixtureInfo.fixture, endpoint: fixtureInfo.endpoint, session: fixtureInfo.session};
    writeFileSync(join(directory, 'fixture.json'), JSON.stringify(install.fixture, null, 2) + '\n', {flag: 'wx'});

    function launchApp(label) {
      const logPath = join(directory, `${label}.log`);
      const stdout = createWriteStream(logPath, {flags: 'wx'});
      const child = spawn(installedExecutable, ['--pairing-dir', fixtureInfo.fixture], {
        cwd: installedApp,
        env: {...admissionEnv},
        stdio: ['ignore', 'pipe', 'pipe'],
      });
      child.stdout.pipe(stdout);
      child.stderr.pipe(stdout);
      appChildren.push(child);
      return child;
    }

    assert.equal(processRows(installedExecutable).length, 0, 'A prior installed AppBrowser process is still running');
    const firstApp = launchApp('app-first');
    await waitForWindow(firstApp);
    await sleep(1_500);
    screenshots.push(captureWindow(join(directory, 'waiting.png'), false));
    pressAccessibilityButton(firstApp.pid, '连接 Host', join(directory, 'connect.log'));
    await sleep(4_000);
    const connected = captureWindow(join(directory, 'connected.png'), false);
    screenshots.push(connected);
    assert.notEqual(connected.hash, screenshots[0].hash, 'AppKit pane stayed unchanged after connect');
    pressAccessibilityButton(firstApp.pid, '接管页面', join(directory, 'takeover.log'));
    await sleep(1_000);
    const page = '<body style="margin:0;background:white"><h1 id="title" style="height:45px;margin:0">中文导航验收</h1><button id="target" style="display:block;width:180px;height:100px;background:red" onclick="window.clicked=(window.clicked||0)+1;this.style.background=\'lime\'">touch</button><input id="field" style="display:block;width:220px;height:50px"><div style="height:1400px;background:blue"></div><script>window.maxScroll=0;window.addEventListener(\'scroll\',()=>window.maxScroll=Math.max(window.maxScroll,window.scrollY));</script></body>';
    const url = `data:text/html,${encodeURIComponent(page)}`;
    setAccessibilityValue(firstApp.pid, '远程页面地址', url, join(directory, 'address-set.log'));
    pressAccessibilityButton(firstApp.pid, '打开', join(directory, 'navigate.log'));
    await sleep(4_000);
    clickRelative(120, 105);
    await sleep(1_000);
    clickRelative(120, 185);
    await sleep(400);
    const inputText = '你好，Mac 输入';
    setAccessibilityValue(firstApp.pid, '输入到远程页面的文字', inputText, join(directory, 'input-set.log'));
    pressAccessibilityButton(firstApp.pid, '发送', join(directory, 'send-text.log'));
    await sleep(2_000);
    const rect = windowRect();
    assert(rect, 'AppKit window disappeared before scroll');
    const scrollPoint = point(rect, 180, 500);
    postScroll(firstApp.pid, scrollPoint.x, scrollPoint.y, -20, join(directory, 'scroll.log'));
    await sleep(2_000);
    screenshots.push(captureWindow(join(directory, 'navigation-input-scroll.png'), true));
    pressAccessibilityButton(firstApp.pid, '返回观察', join(directory, 'release.log'));
    await sleep(2_000);
    const inspection = fixtureResponseValue(await fixture.request('inspect'));
    assert.deepEqual({title: inspection.title, clicked: inspection.clicked, text: inspection.text},
      {title: '中文导航验收', clicked: 1, text: inputText});
    assert(Number.isFinite(inspection.scrollY) && inspection.scrollY > 0);
    assert(Number.isFinite(inspection.maxScroll) && inspection.maxScroll > 0);
    pressAccessibilityButton(firstApp.pid, '断开', join(directory, 'disconnect.log'));
    await sleep(1_500);
    const reconnectStart = now();
    pressAccessibilityButton(firstApp.pid, '连接 Host', join(directory, 'reconnect.log'));
    await sleep(4_000);
    screenshots.push(captureWindow(join(directory, 'reconnect.png'), false));
    assert.notEqual(screenshots.at(-1).hash, screenshots[0].hash, 'Reconnect produced no displayed frame');
    pressAccessibilityButton(firstApp.pid, '断开', join(directory, 'disconnect-after-reconnect.log'));
    await sleep(1_000);
    reconnect = {at: reconnectStart, app_pid: firstApp.pid, fixture_session: fixtureInfo.session,
      displayed_screenshot: screenshots.at(-1), disconnected_before_reconnect: true};
    persist(join(directory, 'reconnect.json'), reconnect);

    const firstExit = await terminateOwned(firstApp, installedExecutable, join(directory, 'first-app-exit.json'));
    const secondApp = launchApp('app-restarted');
    await waitForWindow(secondApp);
    pressAccessibilityButton(secondApp.pid, '连接 Host', join(directory, 'restart-connect.log'));
    await sleep(4_000);
    screenshots.push(captureWindow(join(directory, 'restart-reconnect.png'), false));
    assert.notEqual(screenshots.at(-1).hash, screenshots[0].hash, 'Restarted installed app displayed no frame');
    pressAccessibilityButton(secondApp.pid, '断开', join(directory, 'restart-disconnect.log'));
    await sleep(1_000);
    const secondExit = await terminateOwned(secondApp, installedExecutable, join(directory, 'second-app-exit.json'));
    restart = {first: {pid: firstApp.pid, executable: installedExecutable, exit: firstExit},
      restarted: {pid: secondApp.pid, executable: installedExecutable, exit: secondExit},
      exact_executable_hash: installedExecutableHash, different_pid: firstApp.pid !== secondApp.pid,
      restarted_at: now()};
    assert(restart.different_pid);
    persist(join(directory, 'restart.json'), restart);
    blackbox = {entrypoint, fixture: install.fixture, inspection, screenshots: screenshotLog(screenshots),
      app_pids: [firstApp.pid, secondApp.pid], operations: ['connect', 'h264_display', 'takeover', 'navigate', 'click', 'input_text', 'scroll', 'release', 'inspect_after_release', 'disconnect', 'reconnect', 'restart', 'reconnect_after_restart']};
    persist(join(directory, 'blackbox.json'), blackbox);
    cleanup = {fixture_quit_requested: false, app_pids: [firstApp.pid, secondApp.pid]};
    await quitFixture(fixture);
    cleanup.fixture_quit_requested = true;
    cleanup.fixture_exit = {code: fixture.child.exitCode, signal: fixture.child.signalCode};
    persist(join(directory, 'cleanup.json'), cleanup);
    assert.equal(git('rev-parse', 'HEAD'), head, 'Source commit drifted during admission');
    assert.equal(git('rev-parse', 'HEAD^{tree}'), tree, 'Source tree drifted during admission');
    assert.equal(git('status', '--porcelain'), '', 'Source became dirty during admission');
    assert.deepEqual(readFileSync(manifestPath), manifestBytes, 'Compiled manifest drifted during admission');
    assert.equal(hash(readFileSync(artifactPath)), artifactZipHash, 'Mac artifact drifted during admission');
    assert.deepEqual(externalFiles.map(path => hash(readFileSync(path))), externalHashes, 'External protocol/binary drifted');
  } finally {
    for (const child of appChildren) {
      if (child.exitCode === null && child.signalCode === null) {
        if (install?.installed_app) {
          const exact = join(install.installed_app, 'Contents/MacOS/AgentBrowserMac');
          if (processRows(exact).some(row => row.pid === child.pid)) {
            ownedCommand(child.pid, exact);
            child.kill('SIGTERM');
            try { await waitForChildExit(child, 8_000); } catch { /* preserve primary failure */ }
          }
        }
      }
    }
    if (fixture && fixture.child.exitCode === null && fixture.child.signalCode === null) {
      try { fixture.child.stdin.write('quit\n'); } catch { /* fixture may already be closing */ }
    }
  }

  assert(restart && reconnect && blackbox, 'Admission flow did not produce complete runtime receipts');
  const whiteTime = now();
  const blackTime = now();
  const ids = {white: `whitebox-${prefix}`, install: `install-${prefix}`, restart: `restart-${prefix}`, reconnect: `reconnect-${prefix}`, black: `blackbox-${prefix}`};
  function evidence(id, phase, kind, time, log, surface) {
    persist(`${records}/${id}.json`, {evidence_id: id, issue_id: issueId, experiment_id: `${issueId}-${prefix}`, phase, kind,
      source_commit: head, artifact_hash: JSON.parse(readFileSync('generated/modules/macos-shell/module.compiled.json')).artifact_hash,
      execution_surface: surface, environment_id: environment, entrypoint, scope: {module_id: moduleId, feature_id: issueId, entrypoint},
      producer: surface === 'development_whitebox' ? whiteProducer : blackProducer, result: 'pass', created_at: time,
      expires_at: new Date(Date.now() + 86400000).toISOString(), input_hashes: [tree, ...externalHashes, hash(readFileSync(log))],
      scope_hash: scopeHash, raw_evidence: log});
  }
  evidence(ids.white, 'development_whitebox', 'gate', whiteTime, `${directory}/macos-compile.log`, 'development_whitebox');
  evidence(ids.install, 'deployment_install', 'install', install.installed_at, `${directory}/install.json`, 'deployed_blackbox');
  evidence(ids.restart, 'deployment_restart', 'restart', restart.restarted_at, `${directory}/restart.json`, 'deployed_blackbox');
  evidence(ids.reconnect, 'deployed_blackbox', 'runtime', reconnect.at, `${directory}/reconnect.json`, 'deployed_blackbox');
  evidence(ids.black, 'deployed_blackbox', 'sample_replay', blackTime, `${directory}/blackbox.json`, 'deployed_blackbox');
  persist(candidatePath, {fix_candidate_id: `candidate-${prefix}`, issue_id: issueId, module_id: moduleId,
    worktree_id: root, base_commit: base, head_commit: head, tree_hash: tree,
    diff_hash: hash(command('git', ['diff', '--binary', base, head])), design_id: 'docs/macos-client.md', owner: identity,
    scope_hash: scopeHash, changed_paths: paths, verification_evidence_ids: Object.values(ids), created_at: candidateTime});
  persist(validationPath, {validation_id: `validation-${prefix}`, issue_id: issueId, module_id: moduleId,
    fix_candidate_id: `candidate-${prefix}`, candidate_commit: head, candidate_tree_hash: tree,
    artifact_hash: JSON.parse(readFileSync('generated/modules/macos-shell/module.compiled.json')).artifact_hash,
    whitebox_producer: whiteProducer, whitebox_evidence_ids: [ids.white], blackbox_evidence_ids: [ids.reconnect, ids.black],
    deployment: {environment_id: environment, entrypoint, producer: blackProducer, install_receipt_id: ids.install,
      restart_receipt_id: ids.restart, reconnect_receipt_id: ids.reconnect, observed_at: blackTime}, source_unchanged: true,
    result: 'pass', created_at: now()});
  command('appsdk', ['verify', '--review-admission', '--module', moduleId], {env: admissionEnv, log: join(directory, 'admission.log')});
  console.log(JSON.stringify({candidate: head, tree, artifactHash: JSON.parse(readFileSync('generated/modules/macos-shell/module.compiled.json')).artifact_hash,
    artifactZipHash: install.artifact_zip_hash, installedExecutableHash: install.installed_executable_hash,
    directory, install: `${directory}/install.json`, restart: `${directory}/restart.json`, reconnect: `${directory}/reconnect.json`,
    blackbox: `${directory}/blackbox.json`, admission: 'pass'}, null, 2));
}

function mkdtempPath(prefix) {
  const output = command('mktemp', ['-d', `/tmp/${prefix}.XXXXXX`]).toString().trim();
  assert.match(output, new RegExp(`^/tmp/${prefix}\\.`));
  return output;
}

main().catch(error => {
  console.error(error.stack ?? error);
  process.exitCode = 1;
});

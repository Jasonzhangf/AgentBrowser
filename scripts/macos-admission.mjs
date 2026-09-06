// Formal Mac AppSDK admission: exact artifact install, restart and UI replay.
import {spawn, spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createWriteStream, existsSync, mkdirSync, readFileSync, writeFileSync, copyFileSync} from 'node:fs';
import {createConnection} from 'node:net';
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

function commandPrefixes(prefix) {
  return prefix.startsWith('/tmp/') ? [prefix, `/private${prefix}`] : [prefix];
}

function ownsCommand(row, prefix) {
  return commandPrefixes(prefix).some(candidate => row.command === candidate || row.command.startsWith(`${candidate} `));
}

function processRows(prefix) {
  return psRows().filter(row => ownsCommand(row, prefix));
}

function ownedCommand(pid, prefix) {
  const row = psRows().find(candidate => candidate.pid === pid);
  assert(row, `Owned process ${pid} is no longer observable`);
  assert(ownsCommand(row, prefix),
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

async function waitForProcessExit(pid, executable, timeout = 8_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (!processRows(executable).some(row => row.pid === pid)) return {code: null, signal: 'SIGTERM'};
    await sleep(100);
  }
  throw new Error(`Process ${pid} did not exit`);
}

async function waitForProcess(executable, timeout = 10_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const rows = processRows(executable);
    if (rows.length === 1) return rows[0];
    assert(rows.length === 0, `Multiple installed App processes observed: ${JSON.stringify(rows)}`);
    await sleep(100);
  }
  throw new Error(`Installed App process did not appear: ${executable}`);
}

async function terminateOwned(child, executable, logPath) {
  if (!child || !processRows(executable).some(row => row.pid === child.pid)) {
    return {pid: child?.pid ?? null, code: null, signal: null};
  }
  ownedCommand(child.pid, executable);
  process.kill(child.pid, 'SIGTERM');
  let result;
  try {
    result = await waitForProcessExit(child.pid, executable, 8_000);
  } catch {
    ownedCommand(child.pid, executable);
    process.kill(child.pid, 'SIGKILL');
    result = await waitForProcessExit(child.pid, executable, 8_000);
    result.signal = 'SIGKILL';
  }
  if (logPath) writeFileSync(logPath, JSON.stringify({pid: child.pid, ...result}, null, 2) + '\n');
  return {pid: child.pid, ...result};
}

function windowRect(pid) {
  const swift = `
import ApplicationServices
import Foundation
let app = AXUIElementCreateApplication(${pid})
func value(_ element: AXUIElement, _ attribute: String) -> CFTypeRef? {
    var result: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute as CFString, &result)
    return error == .success ? result : nil
}
guard let windows = value(app, kAXWindowsAttribute) as? [AXUIElement], let window = windows.first,
      let positionValue = value(window, kAXPositionAttribute), let sizeValue = value(window, kAXSizeAttribute) else { exit(2) }
let position = positionValue as! AXValue
let size = sizeValue as! AXValue
var origin = CGPoint.zero
var extent = CGSize.zero
guard AXValueGetValue(position, .cgPoint, &origin), AXValueGetValue(size, .cgSize, &extent), extent.width > 0, extent.height > 0 else { exit(3) }
print("{\\"x\\":\\(origin.x),\\"y\\":\\(origin.y),\\"width\\":\\(extent.width),\\"height\\":\\(extent.height)}")
`;
  const output = optionalCommand('swift', ['-e', swift], {encoding: 'utf8', timeout: 30_000});
  if (output.status !== 0) return null;
  return JSON.parse(String(output.stdout).trim());
}

function activatePid(pid) {
  const swift = `
import AppKit
let app = NSRunningApplication(processIdentifier: ${pid})!
_ = app.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.15))
`;
  command('swift', ['-e', swift], {timeout: 30_000});
}

async function waitForWindow(child) {
  let rect;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (!processRows(child.executable).some(row => row.pid === child.pid)) throw new Error(`App exited before window appeared: ${child.pid}`);
    rect = windowRect(child.pid);
    if (rect && rect.width >= 900 && rect.height >= 600) return rect;
    await sleep(100);
  }
  throw new Error('Timed out waiting for the installed AppKit window');
}

function videoSurfaceGeometry(pid, logPath) {
  const swift = `
import ApplicationServices
import Foundation
let pid: pid_t = ${pid}
let target = "H.264 原生视频显示区"
let app = AXUIElementCreateApplication(pid)
func value(_ element: AXUIElement, _ attribute: String) -> CFTypeRef? {
    var result: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute as CFString, &result)
    return error == .success ? result : nil
}
func rectangle(_ element: AXUIElement, _ attribute: String) -> CGRect? {
    guard let raw = value(element, attribute) else { return nil }
    let axValue = raw as! AXValue
    var point = CGPoint.zero
    var size = CGSize.zero
    if attribute == kAXPositionAttribute {
        guard AXValueGetValue(axValue, .cgPoint, &point) else { return nil }
        return CGRect(origin: point, size: .zero)
    }
    guard AXValueGetValue(axValue, .cgSize, &size) else { return nil }
    return CGRect(origin: .zero, size: size)
}
func rect(_ element: AXUIElement) -> CGRect? {
    guard let position = rectangle(element, kAXPositionAttribute),
          let size = rectangle(element, kAXSizeAttribute) else { return nil }
    return CGRect(origin: position.origin, size: size.size)
}
func findLabel(_ element: AXUIElement) -> AXUIElement? {
    let labels = [value(element, kAXTitleAttribute), value(element, kAXDescriptionAttribute), value(element, kAXValueAttribute)].compactMap { $0 as? String }
    if labels.contains(target) { return element }
    let children = value(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
    for child in children {
        if let match = findLabel(child) { return match }
    }
    return nil
}
func findRole(_ element: AXUIElement, _ targetRole: String) -> AXUIElement? {
    if (value(element, kAXRoleAttribute) as? String) == targetRole { return element }
    let children = value(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
    for child in children {
        if let match = findRole(child, targetRole) { return match }
    }
    return nil
}
if let surface = findLabel(app), let surfaceRect = rect(surface) {
    print("{\\"source\\":\\"labelled_surface\\",\\"x\\":\\(surfaceRect.minX),\\"y\\":\\(surfaceRect.minY),\\"width\\":\\(surfaceRect.width),\\"height\\":\\(surfaceRect.height)}")
    exit(0)
}
guard let splitGroup = findRole(app, "AXSplitGroup"),
      let splitter = findRole(splitGroup, "AXSplitter"),
      let groupRect = rect(splitGroup),
      let splitterRect = rect(splitter) else { exit(2) }
let leftWidth = splitterRect.minX - groupRect.minX
guard leftWidth > 0, groupRect.height > 0 else { exit(3) }
print("{\\"source\\":\\"split_group_left_of_splitter\\",\\"x\\":\\(groupRect.minX),\\"y\\":\\(groupRect.minY),\\"width\\":\\(leftWidth),\\"height\\":\\(groupRect.height)}")
`;
  const output = command('swift', ['-e', swift], {timeout: 30_000}).toString().trim();
  const geometry = JSON.parse(output);
  assert(Number.isFinite(geometry.x) && Number.isFinite(geometry.y)
    && Number.isFinite(geometry.width) && Number.isFinite(geometry.height)
    && geometry.width > 0 && geometry.height > 0,
  `Invalid H.264 surface geometry: ${output}`);
  writeFileSync(logPath, JSON.stringify({pid, label: 'H.264 原生视频显示区', geometry}, null, 2) + '\n', {flag: 'wx'});
  return geometry;
}

function pageScreenPoint(surface, viewport, x, y) {
  const sourceWidth = viewport.width;
  const sourceHeight = viewport.height;
  const scale = Math.min(surface.width / sourceWidth, surface.height / sourceHeight);
  const renderedWidth = sourceWidth * scale;
  const renderedHeight = sourceHeight * scale;
  const offsetX = (surface.width - renderedWidth) / 2;
  const offsetY = (surface.height - renderedHeight) / 2;
  assert(x >= 0 && x <= sourceWidth && y >= 0 && y <= sourceHeight, 'Page coordinate outside H.264 viewport');
  return {
    x: Math.round(surface.x + offsetX + x * scale),
    y: Math.round(surface.y + offsetY + y * scale),
    mapping: {source_width: sourceWidth, source_height: sourceHeight, scale, offset_x: offsetX, offset_y: offsetY},
  };
}

function postMouse(pid, target, logPath) {
  const swift = `
import AppKit
import CoreGraphics
let pid: pid_t = ${pid}
let app = NSRunningApplication(processIdentifier: pid)!
_ = app.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.15))
// AX and screencapture use a top-left origin; Quartz mouse events use a
// bottom-left origin. Keep the page/surface mapping in AX coordinates and
// convert only at the native event boundary.
let display = CGDisplayBounds(CGMainDisplayID())
let point = CGPoint(x: ${target.x}, y: display.maxY - ${target.y})
let source = CGEventSource(stateID: .hidSystemState)!
let move = CGEvent(mouseEventSource: source, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left)!
move.post(tap: .cghidEventTap)
let down = CGEvent(mouseEventSource: source, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left)!
down.post(tap: .cghidEventTap)
let up = CGEvent(mouseEventSource: source, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)!
up.post(tap: .cghidEventTap)
`;
  command('swift', ['-e', swift], {timeout: 30_000});
  writeFileSync(logPath, JSON.stringify({pid, event: 'native_mouse_click', delivery: 'hid_event_tap', activation: 'pid', screen_point: target}, null, 2) + '\n', {flag: 'wx'});
}

function clickPage(pid, viewport, x, y, geometryLogPath, eventLogPath) {
  const surface = videoSurfaceGeometry(pid, geometryLogPath);
  const target = pageScreenPoint(surface, viewport, x, y);
  postMouse(pid, target, eventLogPath);
}

function focusAccessibilityTextField(pid, description, logPath) {
  activatePid(pid);
  const swift = `
import ApplicationServices
import Foundation
let pid: pid_t = ${pid}
let target = ${JSON.stringify(description)}
let app = AXUIElementCreateApplication(pid)
func value(_ element: AXUIElement, _ attribute: String) -> CFTypeRef? {
    var result: CFTypeRef?
    let error = AXUIElementCopyAttributeValue(element, attribute as CFString, &result)
    return error == .success ? result : nil
}
func find(_ element: AXUIElement) -> AXUIElement? {
    let role = value(element, kAXRoleAttribute) as? String
    let labels = [value(element, kAXTitleAttribute), value(element, kAXDescriptionAttribute)].compactMap { $0 as? String }
    if role == kAXTextFieldRole && labels.contains(target) { return element }
    let children = value(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
    for child in children {
        if let match = find(child) { return match }
    }
    return nil
}
guard let field = find(app) else { exit(2) }
guard AXUIElementPerformAction(field, kAXPressAction as CFString) == .success else { exit(3) }
guard AXUIElementSetAttributeValue(field, kAXFocusedAttribute as CFString, kCFBooleanTrue) == .success else { exit(3) }
`;
  command('swift', ['-e', swift], {log: logPath, timeout: 30_000});
}

function postUnicodeText(pid, text, logPath) {
  activatePid(pid);
  const swift = `
import CoreGraphics
import Foundation
let pid: pid_t = ${pid}
let text = ${JSON.stringify(text)}
let source = CGEventSource(stateID: .hidSystemState)!
let units = Array(text.utf16)
for start in stride(from: 0, to: units.count, by: 16) {
    let chunk = Array(units[start..<min(start + 16, units.count)])
    let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true)!
    chunk.withUnsafeBufferPointer { buffer in
        down.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress!)
    }
    down.postToPid(pid)
    let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false)!
    chunk.withUnsafeBufferPointer { buffer in
        up.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress!)
    }
    up.postToPid(pid)
    Thread.sleep(forTimeInterval: 0.002)
}
`;
  command('swift', ['-e', swift], {timeout: 30_000});
  writeFileSync(logPath, JSON.stringify({pid, text_length: text.length, input: 'native_unicode_key_events'}, null, 2) + '\n', {flag: 'wx'});
}

async function pressAccessibilityButton(pid, title, logPath) {
  const swift = `
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
  let failures = [];
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const result = spawnSync('swift', ['-e', swift], {cwd: root, encoding: 'buffer', maxBuffer: 8 * 1024 * 1024, timeout: 30_000});
    const output = Buffer.concat([result.stdout ?? Buffer.alloc(0), result.stderr ?? Buffer.alloc(0)]);
    failures.push({attempt, status: result.status, error: result.error?.message ?? null, output: output.toString()});
    if (!result.error && result.status === 0) {
      writeFileSync(logPath, JSON.stringify({target: title, attempts: failures.length, output: output.toString()}, null, 2) + '\n');
      return;
    }
    if (result.error || ![2, 4].includes(result.status)) {
      writeFileSync(logPath, JSON.stringify({target: title, attempts: failures.length, failures}, null, 2) + '\n');
      assert.fail(`AX button action failed: ${title} (status ${result.status})`);
    }
    await sleep(250);
  }
  writeFileSync(logPath, JSON.stringify({target: title, attempts: failures.length, failures}, null, 2) + '\n');
  assert.fail(`AX button did not become available: ${title}`);
}

function postSurfaceScroll(pid, viewport, x, y, delta, geometryLogPath, eventLogPath) {
  const surface = videoSurfaceGeometry(pid, geometryLogPath);
  const target = pageScreenPoint(surface, viewport, x, y);
  const swift = `
import AppKit
import CoreGraphics
let pid: pid_t = ${pid}
let app = NSRunningApplication(processIdentifier: pid)!
_ = app.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.15))
let source = CGEventSource(stateID: .hidSystemState)!
let point = CGPoint(x: ${target.x}, y: ${target.y})
let event = CGEvent(scrollWheelEvent2Source: source, units: .line, wheelCount: 1, wheel1: ${delta}, wheel2: 0, wheel3: 0)!
event.location = point
event.setIntegerValueField(.scrollWheelEventDeltaAxis1, value: ${delta})
event.setIntegerValueField(.scrollWheelEventPointDeltaAxis1, value: ${delta})
event.setIntegerValueField(.scrollWheelEventFixedPtDeltaAxis1, value: ${delta * 65536})
event.post(tap: .cghidEventTap)
`;
  command('swift', ['-e', swift], {timeout: 30_000});
  writeFileSync(eventLogPath, JSON.stringify({pid, event: 'native_scroll', delivery: 'hid_event_tap', delta, screen_point: target}, null, 2) + '\n', {flag: 'wx'});
}

function captureWindow(path, pid, full = true) {
  const rect = windowRect(pid);
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
  const value = response.result?.value;
  assert.equal(typeof value, 'string', `Fixture returned non-evaluation: ${line}`);
  return JSON.parse(value);
}

function hostStatus(socketPath, requestId) {
  return new Promise((resolveStatus, rejectStatus) => {
    const socket = createConnection({path: socketPath});
    const reader = createInterface({input: socket});
    let stage = 'ready';
    let attachedStatus;
    let settled = false;
    const timer = setTimeout(() => finish(new Error(`Host status deadline: ${socketPath}`)), 20_000);
    function finish(error, value) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reader.close();
      socket.destroy();
      if (error) rejectStatus(error);
      else resolveStatus(value);
    }
    socket.on('error', error => finish(error));
    socket.on('close', () => {
      if (!settled) finish(new Error(`Host status socket closed before response: ${socketPath}`));
    });
    reader.on('line', line => {
      let response;
      try {
        response = JSON.parse(line);
      } catch (error) {
        finish(error);
        return;
      }
      if (stage === 'ready') {
        if (response.type !== 'ready') {
          finish(new Error(`Unexpected Host ready response: ${line}`));
          return;
        }
        stage = 'attach';
        socket.write(`${JSON.stringify({id: requestId, command: {type: 'attach', mode: 'observe', viewport: null}, operation: null})}\n`);
        return;
      }
      if (response.type === 'error') {
        finish(new Error(`Host status request failed: ${line}`));
        return;
      }
      if (response.type !== 'result') {
        finish(new Error(`Unexpected Host status response: ${line}`));
        return;
      }
      if (stage === 'attach') {
        if (response.id !== requestId) {
          finish(new Error(`Unexpected Host attach response: ${line}`));
          return;
        }
        attachedStatus = response.value;
        stage = 'status';
        socket.write(`${JSON.stringify({id: requestId + 1, command: {type: 'status'}, operation: null})}\n`);
        return;
      }
      if (stage === 'status') {
        if (response.id !== requestId + 1) {
          finish(new Error(`Unexpected Host status response: ${line}`));
          return;
        }
        stage = 'detach';
        socket.write(`${JSON.stringify({id: requestId + 2, command: {type: 'detach'}, operation: null})}\n`);
        return;
      }
      if (response.id !== requestId + 2) {
        finish(new Error(`Unexpected Host detach response: ${line}`));
        return;
      }
      stage = 'done';
      finish(null, {attached: attachedStatus, status: response.value});
    });
  });
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
    const fixtureInfo = JSON.parse(await fixture.nextLine(60_000));
    assert.match(fixtureInfo.fixture, /^\/tmp\/an-[a-z0-9]+$/);
    assert.match(fixtureInfo.endpoint, /^wss:\/\/127\.0\.0\.1:\d+$/);
    assert.equal(typeof fixtureInfo.session, 'string');
    const hostSocket = join(fixtureInfo.fixture, 'host/host.sock');
    assertFile(hostSocket);
    const hostStatuses = [];
    let hostRequestId = 1;
    async function observeHostStatus(label, expectedAttachments, expectedPhase) {
      let status;
      for (let attempt = 0; attempt < 40; attempt += 1) {
        const observed = await hostStatus(hostSocket, hostRequestId);
        hostRequestId += 3;
        status = observed.status;
        if (observed.attached?.attachments === expectedAttachments + 1
          && observed.attached?.control?.phase?.type === expectedPhase
          && status?.attachments === expectedAttachments
          && status.control?.phase?.type === expectedPhase) {
          const receipt = {label, expected: {attachments: expectedAttachments, control_phase: expectedPhase}, observed_at: now(),
            diagnostic_attachment: {mode: 'observe', attached_status: observed.attached, detached: true}, status};
          hostStatuses.push(receipt);
          return status;
        }
        await sleep(250);
      }
      assert.fail(`Unexpected Host status at ${label}: ${JSON.stringify(status)}`);
    }
    install.fixture = {root: fixtureInfo.fixture, endpoint: fixtureInfo.endpoint, session: fixtureInfo.session};
    writeFileSync(join(directory, 'fixture.json'), JSON.stringify(install.fixture, null, 2) + '\n', {flag: 'wx'});

    async function launchApp(label) {
      const logPath = join(directory, `${label}.log`);
      const stdout = createWriteStream(logPath, {flags: 'wx'});
      const launcher = spawn('open', ['-n', '-a', installedApp, '--args', '--pairing-dir', fixtureInfo.fixture], {
        cwd: root,
        env: {...admissionEnv},
        stdio: ['ignore', 'pipe', 'pipe'],
      });
      launcher.stdout.pipe(stdout);
      launcher.stderr.pipe(stdout);
      const launchResult = await waitForChildExit(launcher, 20_000);
      assert.equal(launchResult.code, 0, `open failed for ${installedApp}: ${JSON.stringify(launchResult)}`);
      const row = await waitForProcess(installedExecutable);
      const child = {pid: row.pid, executable: installedExecutable, label};
      appChildren.push(child);
      return child;
    }

    assert.equal(processRows(installedExecutable).length, 0, 'A prior installed AppBrowser process is still running');
    const firstApp = await launchApp('app-first');
    await waitForWindow(firstApp);
    await sleep(1_500);
    screenshots.push(captureWindow(join(directory, 'waiting.png'), firstApp.pid, false));
    await pressAccessibilityButton(firstApp.pid, '连接 Host', join(directory, 'connect.log'));
    await sleep(4_000);
    await observeHostStatus('connect', 2, 'agent');
    const connected = captureWindow(join(directory, 'connected.png'), firstApp.pid, false);
    screenshots.push(connected);
    assert.notEqual(connected.hash, screenshots[0].hash, 'AppKit pane stayed unchanged after connect');
    await pressAccessibilityButton(firstApp.pid, '接管页面', join(directory, 'takeover.log'));
    await sleep(1_000);
    const takeoverStatus = await observeHostStatus('takeover', 2, 'human');
    assert(Array.isArray(takeoverStatus.viewport) && takeoverStatus.viewport.length === 2,
      `Host did not publish a committed viewport: ${JSON.stringify(takeoverStatus)}`);
    const viewport = {width: takeoverStatus.viewport[0], height: takeoverStatus.viewport[1]};
    assert(Number.isFinite(viewport.width) && Number.isFinite(viewport.height)
      && viewport.width > 0 && viewport.height > 0, `Invalid Host viewport: ${JSON.stringify(takeoverStatus.viewport)}`);
    const page = '<body style="margin:0;background:white"><h1 id="title" style="height:45px;margin:0">中文导航验收</h1><button id="target" style="display:block;width:180px;height:100px;background:red" onclick="window.clicked=(window.clicked||0)+1;this.style.background=\'lime\'">touch</button><input id="field" style="display:block;width:220px;height:50px"><div style="height:1400px;background:blue"></div><script>window.maxScroll=0;window.addEventListener(\'scroll\',()=>window.maxScroll=Math.max(window.maxScroll,window.scrollY));</script></body>';
    const url = `data:text/html,${encodeURIComponent(page)}`;
    focusAccessibilityTextField(firstApp.pid, '远程页面地址', join(directory, 'address-focus.log'));
    postUnicodeText(firstApp.pid, url, join(directory, 'address-input.log'));
    await sleep(500);
    await pressAccessibilityButton(firstApp.pid, '打开', join(directory, 'navigate.log'));
    await sleep(4_000);
    clickPage(firstApp.pid, viewport, 90, 95, join(directory, 'button-surface-geometry.log'), join(directory, 'button-click.log'));
    await sleep(1_000);
    clickPage(firstApp.pid, viewport, 110, 170, join(directory, 'input-surface-geometry.log'), join(directory, 'input-click.log'));
    await sleep(400);
    const inputText = '你好，Mac 输入';
    focusAccessibilityTextField(firstApp.pid, '输入到远程页面的文字', join(directory, 'input-focus.log'));
    postUnicodeText(firstApp.pid, inputText, join(directory, 'input-set.log'));
    await sleep(400);
    await pressAccessibilityButton(firstApp.pid, '发送', join(directory, 'send-text.log'));
    await sleep(2_000);
    postSurfaceScroll(firstApp.pid, viewport, 180, 500, -20, join(directory, 'scroll-surface-geometry.log'), join(directory, 'scroll.log'));
    await sleep(2_000);
    screenshots.push(captureWindow(join(directory, 'navigation-input-scroll.png'), firstApp.pid, true));
    await pressAccessibilityButton(firstApp.pid, '返回观察', join(directory, 'release.log'));
    await sleep(2_000);
    await observeHostStatus('release', 2, 'agent');
    const inspection = fixtureResponseValue(await fixture.request('inspect'));
    assert.deepEqual({clicked: inspection.clicked, text: inspection.text},
      {clicked: 1, text: inputText});
    assert(Number.isFinite(inspection.scrollY) && inspection.scrollY > 0);
    assert(Number.isFinite(inspection.maxScroll) && inspection.maxScroll > 0);
    await pressAccessibilityButton(firstApp.pid, '断开', join(directory, 'disconnect.log'));
    await sleep(1_500);
    const disconnectedStatus = await observeHostStatus('disconnect', 1, 'agent');
    const reconnectStart = now();
    await pressAccessibilityButton(firstApp.pid, '连接 Host', join(directory, 'reconnect.log'));
    await sleep(4_000);
    const reconnectStatus = await observeHostStatus('reconnect', 2, 'agent');
    screenshots.push(captureWindow(join(directory, 'reconnect.png'), firstApp.pid, false));
    assert.notEqual(screenshots.at(-1).hash, screenshots[0].hash, 'Reconnect produced no displayed frame');
    await pressAccessibilityButton(firstApp.pid, '断开', join(directory, 'disconnect-after-reconnect.log'));
    await sleep(1_000);
    const disconnectedAfterReconnectStatus = await observeHostStatus('disconnect_after_reconnect', 1, 'agent');
    reconnect = {at: reconnectStart, app_pid: firstApp.pid, fixture_session: fixtureInfo.session,
      displayed_screenshot: screenshots.at(-1), disconnected_before_reconnect: true,
      host_status: {after_disconnect: disconnectedStatus, after_reconnect: reconnectStatus,
        after_disconnect_again: disconnectedAfterReconnectStatus}};
    persist(join(directory, 'reconnect.json'), reconnect);

    const firstExit = await terminateOwned(firstApp, installedExecutable, join(directory, 'first-app-exit.json'));
    const secondApp = await launchApp('app-restarted');
    await waitForWindow(secondApp);
    await pressAccessibilityButton(secondApp.pid, '连接 Host', join(directory, 'restart-connect.log'));
    await sleep(4_000);
    const restartReconnectStatus = await observeHostStatus('reconnect_after_restart', 2, 'agent');
    screenshots.push(captureWindow(join(directory, 'restart-reconnect.png'), secondApp.pid, false));
    assert.notEqual(screenshots.at(-1).hash, screenshots[0].hash, 'Restarted installed app displayed no frame');
    await pressAccessibilityButton(secondApp.pid, '断开', join(directory, 'restart-disconnect.log'));
    await sleep(1_000);
    const restartDisconnectedStatus = await observeHostStatus('disconnect_after_restart', 1, 'agent');
    const secondExit = await terminateOwned(secondApp, installedExecutable, join(directory, 'second-app-exit.json'));
    restart = {first: {pid: firstApp.pid, executable: installedExecutable, exit: firstExit},
      restarted: {pid: secondApp.pid, executable: installedExecutable, exit: secondExit},
      exact_executable_hash: installedExecutableHash, different_pid: firstApp.pid !== secondApp.pid,
      host_status: {after_reconnect: restartReconnectStatus, after_disconnect: restartDisconnectedStatus},
      restarted_at: now()};
    assert(restart.different_pid);
    persist(join(directory, 'restart.json'), restart);
    blackbox = {entrypoint, fixture: install.fixture, inspection, host_status: hostStatuses,
      screenshots: screenshotLog(screenshots),
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
      if (install?.installed_app) {
        const exact = join(install.installed_app, 'Contents/MacOS/AgentBrowserMac');
        if (processRows(exact).some(row => row.pid === child.pid)) {
          try { await terminateOwned(child, exact); } catch { /* preserve primary failure */ }
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

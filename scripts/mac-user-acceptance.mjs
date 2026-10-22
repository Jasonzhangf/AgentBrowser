// Mac user-path acceptance against an isolated loopback Host.
//
// The automated scope is exactly:
//   build fixture + app -> start fixture -> launch app -> open menu -> connect
//   -> observe a real frame -> take over -> return to observation -> inspect
//   -> close app + fixture -> write a fail-loud receipt.
//
// Native click, keyboard, scroll and IME are not automatable in this
// environment. They are written to the receipt as PENDING_HUMAN, never PASS.
import {spawn, spawnSync} from 'node:child_process';
import {accessSync, chmodSync, constants, createWriteStream, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
import {isDeepStrictEqual} from 'node:util';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const startedAt = new Date().toISOString();
const runId = `${startedAt.replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')}-${Math.random().toString(16).slice(2, 8)}`;

const usage = `Usage: node scripts/mac-user-acceptance.mjs [options]

Build the isolated fixture and Mac app, then exercise the real visible user path:
open menu -> connect -> wait for a rendered frame -> takeover -> return to
observation -> inspect the Host page -> close both processes.

Required environment:
  OBSCURA_PROTOCOL_ROOT  absolute path to the Obscura protocol/browser owner
  OBSCURA_BIN_DIR        directory containing obscura-host, obscura-endpoint
                         and obscura-media

Options:
  --receipt-dir <dir>    receipt root, relative to this worktree
                         (default: evidence/mac-user-acceptance)
  --timeout <seconds>    per runtime gate timeout (default: 60)
  --observe-seconds <n>  observation hold before takeover and before release
                         (default: 2)
  --persisted-pairing    prove the one-time import path: import the fixture
                         bundle into a throwaway HOME's default store, then
                         launch the app with no pairing argument at all
  --persisted-unreachable
                         prove the failure path: a throwaway HOME store points
                         at a closed port, so the launch must show one visible
                         error and must not retry
  --self-test            run the fixture-cleanup decision cases and exit
  -h, --help             show this help and exit

The script captures both app stdout and stderr. This revision writes the
MAC_NATIVE_SEND/MAC_NATIVE_RECV diagnostics to stderr; the combined app log is
the raw evidence. Native click, keyboard, scroll and IME remain PENDING_HUMAN.
`;

function parseArguments(argv) {
  const options = {
    receiptDir: 'evidence/mac-user-acceptance',
    timeoutSeconds: 60,
    observeSeconds: 2,
    help: false,
    selfTest: false,
    persistedPairing: false,
    persistedUnreachable: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--help' || argument === '-h') {
      options.help = true;
      continue;
    }
    if (argument === '--self-test') {
      options.selfTest = true;
      continue;
    }
    if (argument === '--persisted-pairing') {
      options.persistedPairing = true;
      continue;
    }
    if (argument === '--persisted-unreachable') {
      options.persistedUnreachable = true;
      continue;
    }
    const next = () => {
      index += 1;
      if (index >= argv.length) throw new Error(`${argument} requires a value`);
      return argv[index];
    };
    if (argument === '--receipt-dir') {
      options.receiptDir = next();
      continue;
    }
    if (argument === '--timeout') {
      options.timeoutSeconds = Number(next());
      continue;
    }
    if (argument === '--observe-seconds') {
      options.observeSeconds = Number(next());
      continue;
    }
    throw new Error(`unknown argument: ${argument}`);
  }
  if (!Number.isFinite(options.timeoutSeconds) || options.timeoutSeconds <= 0) {
    throw new Error('--timeout must be a positive number of seconds');
  }
  if (!Number.isFinite(options.observeSeconds) || options.observeSeconds < 0) {
    throw new Error('--observe-seconds must be zero or a positive number');
  }
  if (options.persistedPairing && options.persistedUnreachable) {
    throw new Error('--persisted-pairing and --persisted-unreachable are separate runs');
  }
  return options;
}

let options;
try {
  options = parseArguments(process.argv.slice(2));
} catch (error) {
  process.stderr.write(`${error.message}\n\n${usage}`);
  process.exit(2);
}
if (options.help) {
  process.stdout.write(usage);
  process.exit(0);
}
if (options.selfTest) {
  process.exit(selfTestFixtureCleanupDecision() ? 0 : 1);
}

const receiptRoot = resolve(root, options.receiptDir);
const runDirectory = join(receiptRoot, runId);
mkdirSync(runDirectory, {recursive: true, mode: 0o700});

const protocolRoot = process.env.OBSCURA_PROTOCOL_ROOT
  ? resolve(process.env.OBSCURA_PROTOCOL_ROOT)
  : null;
const binDirectory = process.env.OBSCURA_BIN_DIR
  ? resolve(process.env.OBSCURA_BIN_DIR)
  : null;
// A throwaway HOME keeps the one-time import proof away from the real store.
const persistedHome = join(root, `target/persisted-home-${runId}`);
const persistedStore = join(persistedHome, 'Library/Application Support/AgentBrowser/mac-pairing');
const moduleCache = join(root, 'target/abn-swift-modcache');
mkdirSync(moduleCache, {recursive: true});
mkdirSync(join(runDirectory, 'ax'), {recursive: true});
const persistedHomeRoot = dirname(persistedHome);
mkdirSync(persistedHomeRoot, {recursive: true});
rmSync(persistedHome, {recursive: true, force: true});
mkdirSync(persistedHome, {recursive: true, mode: 0o700});

const fixtureExecutable = join(root, 'target/release/examples/device_fixture');
const appExecutable = join(root, 'apps/macos/build/AgentBrowserMac.app/Contents/MacOS/AgentBrowserMac');
const timeoutMilliseconds = options.timeoutSeconds * 1000;

const gates = [];
const manual = [
  {
    id: 'native-click',
    status: 'PENDING_HUMAN',
    action: '在接管状态下点击原生画面里的红色 touch 按钮。',
    expected: '按钮变为绿色；fixture inspect 的 clicked 从 0 变为 1。',
  },
  {
    id: 'native-text',
    status: 'PENDING_HUMAN',
    action: '在接管状态下点击输入框并输入 ASCII 文本。',
    expected: '输入框显示完整文本；fixture inspect 的 text 等于输入值。',
  },
  {
    id: 'native-scroll',
    status: 'PENDING_HUMAN',
    action: '在接管状态下向下拖动滚动，再回到顶部。',
    expected: '页面滚动；fixture inspect 记录 scrollTrace 与 maxScroll，最终 scrollY 为 0。',
  },
  {
    id: 'native-ime',
    status: 'PENDING_HUMAN',
    action: '使用中文输入法组合并提交文本，再验证一次取消组合。',
    expected: '提交一次完整文本；取消组合不发送部分文本；fixture inspect 的 editorEvents 可核对。',
  },
];

const observations = {
  app: {pid: null, executable: appExecutable, stdout: 'app.stdout.log', stderr: 'app.stderr.log', trace: 'app.log'},
  fixture: {pid: null, executable: fixtureExecutable, stdout: 'fixture.stdout.log', stderr: 'fixture.stderr.log'},
  fixture_info: null,
  baseline_inspect: null,
  after_inspect: null,
  observe_before_takeover: [],
  observe_after_release: [],
  connect_snapshot: null,
  takeover_snapshot: null,
  release_snapshot: null,
  visible_content: null,
  persisted_pairing: null,
};

let appChild = null;
let fixtureChild = null;
// Children are only visible while their parent lives. Record them at start so
// cleanup can still close them after the parent died.
let recordedAppChildren = [];
let recordedFixtureChildren = [];
let appExitMethod = null;
let fixtureExitMethod = null;
let failure = null;
let cleanupFailed = false;
let interrupted = null;
let appStdoutStream = null;
let appStderrStream = null;
let fixtureStdoutStream = null;
let fixtureStderrStream = null;

const appTailLines = [];
const serializedSnapshots = [];
const rejections = [];
let appCombinedStream = null;
let fixtureStdoutBuffer = '';
let fixtureStderrBuffer = '';
const fixtureLines = [];

process.on('SIGINT', () => { interrupted = 'SIGINT'; });
process.on('SIGTERM', () => { interrupted = 'SIGTERM'; });

const now = () => new Date().toISOString();
const sleep = milliseconds => delay(milliseconds);

function failGate(name, detail, evidence = null) {
  const error = new Error(detail);
  error.gate = name;
  error.evidence = evidence;
  throw error;
}

function passGate(name, detail, evidence = null) {
  gates.push({name, status: 'PASS', detail, evidence, at: now()});
}

function checkInterrupted() {
  if (interrupted) failGate('INTERRUPTED', `received ${interrupted}; child resources are being released`);
}

function outputTail(text, limit = 4000) {
  if (!text) return '';
  return text.slice(-limit);
}

function trimSnapshot(snapshot) {
  if (!snapshot) return null;
  const copy = {...snapshot};
  delete copy.raw;
  return copy;
}

function executable(path) {
  try {
    accessSync(path, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function runSync(gate, program, args, optionsForRun = {}) {
  const result = spawnSync(program, args, {
    cwd: optionsForRun.cwd ?? root,
    env: optionsForRun.env ?? process.env,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    timeout: optionsForRun.timeout ?? 30 * 60 * 1000,
  });
  const output = `${result.stdout ?? ''}${result.stderr ?? ''}`;
  const logPath = join(runDirectory, `${gate.toLowerCase()}.log`);
  writeFileSync(logPath, output);
  if (result.error) {
    failGate(gate, `${program} ${args.join(' ')} failed: ${result.error.message}`, {
      command: [program, ...args],
      log: logPath,
      output_tail: outputTail(output),
    });
  }
  if (result.status !== 0) {
    failGate(gate, `${program} ${args.join(' ')} exited ${result.status}`, {
      command: [program, ...args],
      exit: result.status,
      log: logPath,
      output_tail: outputTail(output),
    });
  }
  return {result, output, logPath};
}

function runHelper(program, args, timeout = 30_000) {
  return spawnSync(program, args, {
    cwd: root,
    env: {...process.env, CLANG_MODULE_CACHE_PATH: moduleCache},
    encoding: 'utf8',
    maxBuffer: 32 * 1024 * 1024,
    timeout,
  });
}

// The chrome strip is the one user-visible control that the accessibility tree
// cannot vouch for: it reported the toolbar and the address field with the
// correct geometry while the strip itself painted as a flat dark bar. The gates
// below therefore capture the screen region that AX names and check that it
// holds real content.
function chromeStripRect(pid) {
  const swift = `
import AppKit
import ApplicationServices
let pid: pid_t = ${pid}
let app = AXUIElementCreateApplication(pid)
func value(_ element: AXUIElement, _ attribute: String) -> CFTypeRef? {
    var result: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, attribute as CFString, &result) == .success ? result : nil
}
func find(_ element: AXUIElement, _ depth: Int) -> AXUIElement? {
    if depth > 12 { return nil }
    let labels = [value(element, kAXTitleAttribute), value(element, kAXDescriptionAttribute)].compactMap { $0 as? String }
    if labels.contains("浏览器工具栏") { return element }
    for child in value(element, kAXChildrenAttribute) as? [AXUIElement] ?? [] {
        if let match = find(child, depth + 1) { return match }
    }
    return nil
}
_ = NSRunningApplication(processIdentifier: pid)?.activate(options: [.activateAllWindows])
if let windows = value(app, kAXWindowsAttribute) as? [AXUIElement], let window = windows.first {
    _ = AXUIElementPerformAction(window, kAXRaiseAction as CFString)
}
guard let strip = find(app, 0),
      let positionValue = value(strip, kAXPositionAttribute),
      let sizeValue = value(strip, kAXSizeAttribute) else { exit(2) }
var origin = CGPoint.zero
var extent = CGSize.zero
guard AXValueGetValue(positionValue as! AXValue, .cgPoint, &origin),
      AXValueGetValue(sizeValue as! AXValue, .cgSize, &extent),
      extent.width > 200, extent.height >= 40 else { exit(3) }
print("{\\"x\\":\\(Int(origin.x)),\\"y\\":\\(Int(origin.y)),\\"width\\":\\(Int(extent.width)),\\"height\\":\\(Int(extent.height))}")
`;
  const result = runHelper('swift', ['-e', swift]);
  if (result.status !== 0) return null;
  try {
    return JSON.parse(String(result.stdout).trim());
  } catch {
    return null;
  }
}

function chromeStripStats(path) {
  const swift = `
import AppKit
guard let image = NSImage(contentsOfFile: ${JSON.stringify(path)}),
      let tiff = image.tiffRepresentation,
      let rep = NSBitmapImageRep(data: tiff) else { exit(2) }
var distinct = Set<Int>()
var bright = 0
var total = 0
var y = 0
while y < rep.pixelsHigh {
    var x = 0
    while x < rep.pixelsWide {
        if let color = rep.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB) {
            let r = Int((color.redComponent * 255).rounded())
            let g = Int((color.greenComponent * 255).rounded())
            let b = Int((color.blueComponent * 255).rounded())
            distinct.insert((r << 16) | (g << 8) | b)
            if (r * 299 + g * 587 + b * 114) / 1000 > 200 { bright += 1 }
            total += 1
        }
        x += 2
    }
    y += 2
}
print("{\\"distinct\\":\\(distinct.count),\\"bright\\":\\(bright),\\"total\\":\\(total)}")
`;
  const result = runHelper('swift', ['-e', swift]);
  if (result.status !== 0) return null;
  try {
    return JSON.parse(String(result.stdout).trim());
  } catch {
    return null;
  }
}

async function assertChromeStripPainted(pid) {
  const strip = chromeStripRect(pid);
  if (!strip) {
    failGate('CHROME_PAINTED', 'the accessibility tree did not expose the chrome strip rectangle', {pid});
  }
  await sleep(600);
  const path = join(runDirectory, 'takeover-chrome.png');
  runSync('CHROME_CAPTURE', 'screencapture', ['-x', '-R', `${strip.x},${strip.y},${strip.width},${strip.height}`, path]);
  const stats = chromeStripStats(path);
  const brightShare = stats && stats.total ? stats.bright / stats.total : 0;
  if (!stats || stats.distinct < 6 || brightShare < 0.2) {
    failGate('CHROME_PAINTED', `the chrome strip is not painted: ${JSON.stringify({strip, stats, brightShare})}`, {
      strip,
      stats,
      bright_share: brightShare,
      screenshot: path,
    });
  }
  passGate('CHROME_PAINTED', 'the native-video chrome strip shows the toolbar', {
    strip,
    stats,
    bright_share: brightShare,
    screenshot: path,
  });
}

function preflight() {
  checkInterrupted();
  if (process.platform !== 'darwin') {
    failGate('PREFLIGHT_PLATFORM', `requires macOS/Darwin, got ${process.platform}`);
  }
  passGate('PREFLIGHT_PLATFORM', 'Darwin');

  if (!protocolRoot) {
    failGate('PREFLIGHT_PROTOCOL_ROOT', 'OBSCURA_PROTOCOL_ROOT is not set. Set it to the absolute Obscura protocol/browser path.');
  }
  if (!existsSync(join(protocolRoot, 'Cargo.toml')) || !existsSync(join(protocolRoot, 'src/lib.rs'))) {
    failGate('PREFLIGHT_PROTOCOL_ROOT', `OBSCURA_PROTOCOL_ROOT does not contain Cargo.toml and src/lib.rs: ${protocolRoot}`);
  }
  passGate('PREFLIGHT_PROTOCOL_ROOT', protocolRoot);

  if (!binDirectory) {
    failGate('PREFLIGHT_BIN_DIR', 'OBSCURA_BIN_DIR is not set. Set it to the directory containing obscura-host, obscura-endpoint and obscura-media.');
  }
  const missingBinaries = ['obscura-host', 'obscura-endpoint', 'obscura-media']
    .filter(name => !executable(join(binDirectory, name)));
  if (missingBinaries.length) {
    failGate('PREFLIGHT_BIN_DIR', `OBSCURA_BIN_DIR is missing executable ${missingBinaries.join(', ')}: ${binDirectory}`);
  }
  passGate('PREFLIGHT_BIN_DIR', binDirectory);

  const sourceFiles = [
    'packages/android-bridge/examples/device_fixture.rs',
    'apps/macos/Sources/main.swift',
    'apps/macos/connection-bridge/Cargo.toml',
    'scripts/build-macos.sh',
  ];
  const missingSources = sourceFiles.filter(path => !existsSync(join(root, path)));
  if (missingSources.length) {
    failGate('PREFLIGHT_SOURCE', `required source files are missing: ${missingSources.join(', ')}`);
  }
  if (!existsSync(join(root, 'node_modules/.bin/esbuild'))) {
    failGate('PREFLIGHT_NODE_DEPS', `node_modules/.bin/esbuild is missing; run npm ci in ${root}`);
  }
  passGate('PREFLIGHT_SOURCE', 'fixture, app, bridge and UI build inputs are present');

  for (const [program, args] of [
    ['cargo', ['--version']],
    ['swift', ['--version']],
    ['xcrun', ['--find', 'swiftc']],
  ]) {
    const result = runHelper(program, args, 60_000);
    if (result.error || result.status !== 0) {
      failGate('PREFLIGHT_TOOLS', `${program} ${args.join(' ')} failed: ${result.error?.message ?? outputTail(result.stderr || result.stdout)}`, {
        command: [program, ...args],
        exit: result.status,
        output_tail: outputTail(`${result.stdout ?? ''}${result.stderr ?? ''}`),
      });
    }
  }
  passGate('PREFLIGHT_TOOLS', 'cargo, swift and xcrun are available');

  const ax = runHelper('swift', ['-e', 'import ApplicationServices\nimport Foundation\nexit(AXIsProcessTrusted() ? 0 : 1)'], 60_000);
  if (ax.error || ax.status !== 0) {
    failGate('PREFLIGHT_AX_TRUST', 'Accessibility is not granted to the process that launches this script. Enable it in System Settings > Privacy & Security > Accessibility, then rerun.', {
      exit: ax.status,
      output_tail: outputTail(`${ax.stdout ?? ''}${ax.stderr ?? ''}`),
      hint: 'The terminal or Codex host that runs the script is the process that needs Accessibility permission.',
    });
  }
  passGate('PREFLIGHT_AX_TRUST', 'AXIsProcessTrusted() is true');
}

function buildFixture() {
  checkInterrupted();
  const result = runSync('BUILD_FIXTURE', 'cargo', [
    'build',
    '--release',
    '--locked',
    '-p',
    'agentbrowser-android',
    '--example',
    'device_fixture',
    '--config',
    `patch.crates-io.obscura-host-protocol.path="${protocolRoot}"`,
  ], {
    env: {...process.env, CLANG_MODULE_CACHE_PATH: moduleCache, OBSCURA_PROTOCOL_ROOT: protocolRoot},
    timeout: 30 * 60 * 1000,
  });
  if (!executable(fixtureExecutable)) {
    failGate('BUILD_FIXTURE', `fixture executable was not produced: ${fixtureExecutable}`, {log: result.logPath});
  }
  passGate('BUILD_FIXTURE', fixtureExecutable, {log: result.logPath});
}

function buildApp() {
  checkInterrupted();
  const result = runSync('BUILD_APP', 'bash', ['scripts/build-macos.sh', 'build'], {
    env: {
      ...process.env,
      CLANG_MODULE_CACHE_PATH: moduleCache,
      OBSCURA_PROTOCOL_ROOT: protocolRoot,
      OBSCURA_BIN_DIR: binDirectory,
    },
    timeout: 30 * 60 * 1000,
  });
  if (!executable(appExecutable)) {
    failGate('BUILD_APP', `app executable was not produced: ${appExecutable}`, {log: result.logPath});
  }
  passGate('BUILD_APP', appExecutable, {log: result.logPath});
}

function pumpLines(stream, onChunk, onLine) {
  let buffer = '';
  stream.setEncoding('utf8');
  stream.on('data', chunk => {
    onChunk?.(chunk);
    buffer += chunk;
    let newline;
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newline).replace(/\r$/, '');
      buffer = buffer.slice(newline + 1);
      onLine(line);
    }
  });
  stream.on('end', () => {
    if (buffer) {
      onLine(buffer);
      buffer = '';
    }
  });
}

function rememberAppLine(line) {
  appTailLines.push(line);
  if (appTailLines.length > 500) appTailLines.shift();
  if (!line.includes('MAC_NATIVE_')) return;

  const marker = 'MAC_NATIVE_RECV ';
  const at = line.indexOf(marker);
  if (at < 0) return;
  // The app writes each diagnostic with a literal trailing `\n` escape pair, so
  // strip it before matching the response object.
  const record = line.slice(at + marker.length).replace(/\\n+$/, '');
  const match = /^op=(\S+) connectionState=(\S+) response=(\{.*\})\s*$/.exec(record);
  if (!match) return;
  let response;
  try {
    response = JSON.parse(match[3]);
  } catch {
    return;
  }
  const entry = {
    at: now(),
    op: match[1],
    declared_connection_state: match[2],
    ...response,
    raw: line,
  };
  if (response && typeof response.rejection === 'string') {
    rejections.push({at: now(), op: match[1], rejection: response.rejection, raw: line});
    return;
  }
  if (!response || typeof response.connectionState !== 'string') return;
  serializedSnapshots.push(entry);
}

function rememberFixtureStdout(line) {
  fixtureStdoutBuffer += `${line}\n`;
  fixtureLines.push(line);
  if (fixtureLines.length > 200) fixtureLines.shift();
}

function rememberFixtureStderr(line) {
  fixtureStderrBuffer += `${line}\n`;
}

const unreachableEndpoint = '127.0.0.1:1';

// The negative case needs a configured store that cannot work: a closed port.
// It is written directly here, because a bundle import must stay honest.
function writeUnreachableStore() {
  mkdirSync(persistedStore, {recursive: true, mode: 0o700});
  writeFileSync(join(persistedStore, 'endpoint.txt'), unreachableEndpoint, {mode: 0o600});
  writeFileSync(join(persistedStore, 'token'), 'unreachable-token', {mode: 0o600});
  chmodSync(persistedStore, 0o700);
  chmodSync(join(persistedStore, 'endpoint.txt'), 0o600);
  chmodSync(join(persistedStore, 'token'), 0o600);
}

// Every native command is logged once as `MAC_NATIVE_SEND op=<op> raw=...`.
function countNativeCommands(op) {
  const path = join(runDirectory, 'app.log');
  if (!existsSync(path)) return 0;
  const text = readFileSync(path, {encoding: 'utf8', flag: 'r'});
  const pattern = new RegExp(`MAC_NATIVE_SEND op=${op} raw=`, 'g');
  return (text.match(pattern) ?? []).length;
}

function bridgeExecutable() {
  return join(root, 'apps/macos/build/AgentBrowserMac.app/Contents/MacOS/AgentBrowserMacBridge');
}

// One line-framed text response: 1 kind byte, u32be header length, header
// JSON, u32be body length, body. Returns null until a whole frame is buffered.
function takeTextResponse(buffer) {
  if (buffer.length < 5) return null;
  if (buffer[0] !== 0) throw new Error(`unexpected bridge frame kind ${buffer[0]}`);
  const headerLength = buffer.readUInt32BE(1);
  if (buffer.length < 5 + headerLength + 4) return null;
  const header = JSON.parse(buffer.subarray(5, 5 + headerLength).toString('utf8'));
  const bodyLengthOffset = 5 + headerLength;
  const bodyLength = buffer.readUInt32BE(bodyLengthOffset);
  const bodyStart = bodyLengthOffset + 4;
  if (buffer.length < bodyStart + bodyLength) return null;
  return {
    header,
    value: header.value,
    rest: buffer.subarray(bodyStart + bodyLength),
  };
}

async function bridgeRequestOnce(environment, command) {
  const child = spawn(bridgeExecutable(), [], {cwd: root, env: environment, stdio: ['pipe', 'pipe', 'pipe']});
  try {
    return await new Promise((resolvePromise, rejectPromise) => {
      let buffer = Buffer.alloc(0);
      const timer = setTimeout(() => rejectPromise(new Error('bridge response timeout')), 20_000);
      const finish = value => { clearTimeout(timer); resolvePromise(value); };
      child.stdout.on('data', chunk => {
        buffer = Buffer.concat([buffer, chunk]);
        let frame;
        try {
          frame = takeTextResponse(buffer);
        } catch (error) {
          clearTimeout(timer);
          rejectPromise(error);
          return;
        }
        if (frame) finish(frame.value);
      });
      child.on('error', error => { clearTimeout(timer); rejectPromise(error); });
      child.stdin.write(`${JSON.stringify({id: 1, command})}\n`);
    });
  } finally {
    try { child.stdin.end(); } catch {}
    try { child.kill('SIGTERM'); } catch {}
  }
}

// The one-time configuration step: import the fixture bundle into a throwaway
// HOME's default store through the same bridge op the native panel uses.
async function importPairingThroughBridge(bundlePath) {
  const environment = {...process.env, HOME: persistedHome};
  delete environment.AGENTBROWSER_MAC_PAIRING;
  const raw = await bridgeRequestOnce(environment, JSON.stringify({op: 'import_connection', path: bundlePath}));
  let snapshot = null;
  try {
    snapshot = JSON.parse(raw);
  } catch {
    failGate('PERSISTED_IMPORT', `import response is not JSON: ${raw}`, {raw});
  }
  if (snapshot.rejection) {
    failGate('PERSISTED_IMPORT', `import rejected: ${snapshot.rejection}`, {raw});
  }
  if (snapshot.connectionSetup?.state !== 'configured') {
    failGate('PERSISTED_IMPORT', `import did not report a configured store: ${raw}`, {raw});
  }
  const endpointPath = join(persistedStore, 'endpoint.txt');
  const tokenPath = join(persistedStore, 'token');
  if (!existsSync(endpointPath) || !existsSync(tokenPath)) {
    failGate('PERSISTED_IMPORT', 'import did not write endpoint.txt and token into the default store', {
      store: persistedStore,
      exists: {endpoint: existsSync(endpointPath), token: existsSync(tokenPath)},
    });
  }
  const modes = {
    store: statSync(persistedStore).mode & 0o777,
    endpoint: statSync(endpointPath).mode & 0o777,
    token: statSync(tokenPath).mode & 0o777,
  };
  if (modes.store !== 0o700 || modes.endpoint !== 0o600 || modes.token !== 0o600) {
    failGate('PERSISTED_IMPORT', `store modes are not private: ${JSON.stringify(modes)}`, {modes});
  }
  observations.persisted_pairing = {
    home: persistedHome,
    store: persistedStore,
    modes,
    launch_arguments: [],
    auto_connect: 'pending',
  };
  passGate('PERSISTED_IMPORT', 'bundle imported into the default store with private modes', {
    store: persistedStore,
    modes,
  });
}

function launchApp() {
  checkInterrupted();
  appStdoutStream = createWriteStream(join(runDirectory, 'app.stdout.log'));
  appStderrStream = createWriteStream(join(runDirectory, 'app.stderr.log'));
  appCombinedStream = createWriteStream(join(runDirectory, 'app.log'));
  // The persisted mode is the ordinary user case: no `--pairing-dir`, no
  // `AGENTBROWSER_MAC_PAIRING`. Only the scratch HOME differs from a real
  // double-click, so this run never touches the developer's own store.
  const persistedLaunch = options.persistedPairing || options.persistedUnreachable;
  const arguments_ = persistedLaunch ? [] : ['--pairing-dir', observations.fixture_info.fixture];
  const child = spawn(appExecutable, arguments_, {
    cwd: root,
    env: {...process.env, ...(persistedLaunch ? {HOME: persistedHome, AGENTBROWSER_MAC_PAIRING: ''} : {})},
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  appChild = child;
  observations.app.pid = child.pid;
  pumpLines(child.stdout, chunk => { appStdoutStream.write(chunk); appCombinedStream.write(chunk); }, rememberAppLine);
  pumpLines(child.stderr, chunk => { appStderrStream.write(chunk); appCombinedStream.write(chunk); }, rememberAppLine);
  child.on('error', error => {
    if (!failure) failure = {gate: 'APP_LAUNCH', detail: `app spawn error: ${error.message}`, evidence: null};
  });
  if (!child.pid) {
    failGate('APP_LAUNCH', 'spawn returned no app pid');
  }
  // Record the arguments this launch really used, so a persisted run cannot be
  // read as a `--pairing-dir` run.
  passGate('APP_LAUNCH', `pid ${child.pid}`, {
    arguments: arguments_,
    ...(persistedLaunch ? {home: persistedHome} : {pairing_directory: observations.fixture_info.fixture}),
  });
}

async function startFixture() {
  checkInterrupted();
  fixtureStdoutStream = createWriteStream(join(runDirectory, 'fixture.stdout.log'));
  fixtureStderrStream = createWriteStream(join(runDirectory, 'fixture.stderr.log'));
  const child = spawn(fixtureExecutable, [], {
    cwd: root,
    env: {
      ...process.env,
      OBSCURA_BIN_DIR: binDirectory,
      OBSCURA_ENDPOINT_BIND_IP: '127.0.0.1',
    },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  fixtureChild = child;
  observations.fixture.pid = child.pid;
  pumpLines(child.stdout, chunk => fixtureStdoutStream.write(chunk), rememberFixtureStdout);
  pumpLines(child.stderr, chunk => fixtureStderrStream.write(chunk), rememberFixtureStderr);
  child.on('error', error => {
    if (!failure) failure = {gate: 'FIXTURE_START', detail: `fixture spawn error: ${error.message}`, evidence: null};
  });
  if (!child.pid) {
    failGate('FIXTURE_START', 'spawn returned no fixture pid');
  }
  const line = await nextFixtureLine(timeoutMilliseconds);
  let info;
  try {
    info = JSON.parse(line);
  } catch {
    failGate('FIXTURE_START', `fixture first stdout line is not JSON: ${line}`, {fixture_stderr_tail: outputTail(fixtureStderrBuffer)});
  }
  for (const field of ['fixture', 'endpoint', 'bundle', 'session', 'initialUrl']) {
    if (typeof info[field] !== 'string' || !info[field]) {
      failGate('FIXTURE_START', `fixture startup line is missing ${field}: ${line}`, {fixture_line: line});
    }
  }
  observations.fixture_info = info;
  passGate('FIXTURE_START', `pid ${child.pid}, endpoint ${info.endpoint}`, {
    endpoint: info.endpoint,
    session: info.session,
    fixture_directory: info.fixture,
    bundle: info.bundle,
  });
}

async function nextFixtureLine(timeout) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    checkInterrupted();
    if (fixtureLines.length) return fixtureLines.shift();
    if (childExited(fixtureChild)) {
      failGate('FIXTURE_COMMAND', `fixture exited with ${childExitDescription(fixtureChild)}`, {
        fixture_stderr_tail: outputTail(fixtureStderrBuffer),
        fixture_stdout_tail: outputTail(fixtureStdoutBuffer),
      });
    }
    if (failure?.gate === 'FIXTURE_START') {
      failGate(failure.gate, failure.detail, failure.evidence);
    }
    await sleep(50);
  }
  failGate('FIXTURE_COMMAND', `fixture produced no stdout line within ${timeout} ms`, {
    fixture_stderr_tail: outputTail(fixtureStderrBuffer),
    fixture_stdout_tail: outputTail(fixtureStdoutBuffer),
  });
}

async function fixtureCommand(command, gate = 'FIXTURE_COMMAND') {
  checkInterrupted();
  if (!fixtureChild?.stdin?.writable) {
    failGate(gate, `fixture stdin is not writable for ${command}`);
  }
  fixtureChild.stdin.write(`${command}\n`);
  const line = await nextFixtureLine(timeoutMilliseconds);
  let value;
  try {
    value = JSON.parse(line);
  } catch {
    failGate(gate, `fixture returned non-JSON for ${command}: ${line}`, {fixture_line: line});
  }
  return value;
}

function findInspectSnapshot(value, depth = 0) {
  if (depth > 8 || value === null || value === undefined) return null;
  if (typeof value === 'string') {
    try {
      return findInspectSnapshot(JSON.parse(value), depth + 1);
    } catch {
      return null;
    }
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findInspectSnapshot(item, depth + 1);
      if (found) return found;
    }
    return null;
  }
  if (typeof value === 'object') {
    const required = ['clicked', 'text', 'scrollY', 'maxScroll'];
    if (required.every(key => Object.prototype.hasOwnProperty.call(value, key))) return value;
    for (const child of Object.values(value)) {
      const found = findInspectSnapshot(child, depth + 1);
      if (found) return found;
    }
  }
  return null;
}

function axPressSource(pid, title) {
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
var visited = 0
func find(_ element: AXUIElement, _ depth: Int) -> AXUIElement? {
    if depth > 24 || visited > 50000 { return nil }
    visited += 1
    let role = value(element, kAXRoleAttribute) as? String
    let labels = [value(element, kAXTitleAttribute), value(element, kAXDescriptionAttribute), value(element, kAXValueAttribute)].compactMap { $0 as? String }
    if role == kAXButtonRole && labels.contains(target) { return element }
    let children = value(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
    for child in children {
        if let match = find(child, depth + 1) { return match }
    }
    return nil
}
guard let button = find(app, 0) else { exit(2) }
if (value(button, kAXEnabledAttribute) as? Bool) == false { exit(4) }
guard AXUIElementPerformAction(button, kAXPressAction as CFString) == .success else { exit(3) }
`;
}

function axTreeSource(pid) {
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
var visited = 0
func walk(_ element: AXUIElement, _ depth: Int) {
    if depth > 20 || visited > 50000 { return }
    visited += 1
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
}

function dumpAxTree(pid, name) {
  const result = runHelper('swift', ['-e', axTreeSource(pid)], 60_000);
  const path = join(runDirectory, `ax/${name}.txt`);
  writeFileSync(path, `${result.stdout ?? ''}${result.stderr ?? ''}`);
  return {path, exit: result.status, output_tail: outputTail(`${result.stdout ?? ''}${result.stderr ?? ''}`)};
}

async function pressAxButton(pid, title, gate) {
  const deadline = Date.now() + timeoutMilliseconds;
  let attempts = 0;
  let last = null;
  while (Date.now() < deadline) {
    checkInterrupted();
    if (childExited(appChild)) {
      failGate(gate, `app exited before AX button ${title} could be pressed`, {app_tail: appTailLines.slice(-40)});
    }
    attempts += 1;
    last = runHelper('swift', ['-e', axPressSource(pid, title)], 30_000);
    if (!last.error && last.status === 0) {
      passGate(gate, `pressed ${title}`, {pid, attempts});
      return;
    }
    if (last.error || ![2, 4].includes(last.status)) {
      const tree = dumpAxTree(pid, `${gate.toLowerCase()}-failed`);
      failGate(gate, `AX button ${title} failed (exit ${last.status}): ${outputTail(last.stderr || last.stdout)}`, {
        pid,
        attempts,
        ax_tree: tree.path,
        output_tail: tree.output_tail,
      });
    }
    await sleep(1_000);
  }
  const tree = dumpAxTree(pid, `${gate.toLowerCase()}-timeout`);
  failGate(gate, `AX button ${title} did not become available after ${attempts} attempts`, {
    pid,
    attempts,
    last_exit: last?.status ?? null,
    last_output_tail: outputTail(`${last?.stderr ?? ''}${last?.stdout ?? ''}`),
    ax_tree: tree.path,
  });
}

function latestSnapshot(from = 0) {
  return serializedSnapshots.slice(from).at(-1) ?? null;
}

async function waitForSnapshot(gate, fromSnapshot, fromRejection, predicate, description) {
  const deadline = Date.now() + timeoutMilliseconds;
  while (Date.now() < deadline) {
    checkInterrupted();
    if (childExited(appChild)) {
      failGate(gate, `app exited before ${description}`, {
        exit: appChild?.exitCode ?? null,
        signal: appChild?.signalCode ?? null,
        app_tail: appTailLines.slice(-40),
        recent_snapshots: serializedSnapshots.slice(fromSnapshot).slice(-5).map(trimSnapshot),
      });
    }
    const newRejections = rejections.slice(fromRejection);
    if (newRejections.length) {
      failGate(gate, `${description} was rejected: ${newRejections[0].rejection}`, {
        rejection: newRejections[0],
        recent_snapshots: serializedSnapshots.slice(fromSnapshot).slice(-5).map(trimSnapshot),
      });
    }
    const candidates = serializedSnapshots.slice(fromSnapshot);
    const error = candidates.find(snapshot => snapshot.connectionState === 'error' || (typeof snapshot.error === 'string' && snapshot.error));
    if (error) {
      failGate(gate, `${description} surfaced an error: ${error.error ?? 'connectionState=error'}`, {
        snapshot: trimSnapshot(error),
        app_tail: appTailLines.slice(-40),
      });
    }
    const found = candidates.find(predicate);
    if (found) return found;
    await sleep(100);
  }
  failGate(gate, `${description} timed out after ${timeoutMilliseconds} ms`, {
    last_snapshot: trimSnapshot(latestSnapshot(fromSnapshot)),
    recent_snapshots: serializedSnapshots.slice(fromSnapshot).slice(-5).map(trimSnapshot),
    app_tail: appTailLines.slice(-40),
  });
}

// The failure path expects the error that `waitForSnapshot` refuses. It waits
// for a typed visible error and never accepts a connection success.
async function waitForFailureSnapshot(gate, fromSnapshot, fromRejection, description) {
  const deadline = Date.now() + timeoutMilliseconds;
  while (Date.now() < deadline) {
    checkInterrupted();
    if (childExited(appChild)) {
      failGate(gate, `app exited before ${description}`, {
        exit: appChild?.exitCode ?? null,
        signal: appChild?.signalCode ?? null,
        app_tail: appTailLines.slice(-40),
        recent_snapshots: serializedSnapshots.slice(fromSnapshot).slice(-5).map(trimSnapshot),
      });
    }
    const newRejections = rejections.slice(fromRejection);
    if (newRejections.length) {
      failGate(gate, `${description} was rejected instead of failing at connect: ${newRejections[0].rejection}`, {
        rejection: newRejections[0],
      });
    }
    const candidates = serializedSnapshots.slice(fromSnapshot);
    const connected = candidates.find(snapshot => snapshot.connectionState === 'connected');
    if (connected) {
      failGate(gate, `${description} connected, but the store points at a closed port`, {
        snapshot: trimSnapshot(connected),
      });
    }
    const failed = candidates.find(
      snapshot => snapshot.connectionState === 'error' || (typeof snapshot.error === 'string' && snapshot.error),
    );
    if (failed) return failed;
    await sleep(100);
  }
  failGate(gate, `${description} timed out after ${timeoutMilliseconds} ms`, {
    last_snapshot: trimSnapshot(latestSnapshot(fromSnapshot)),
    recent_snapshots: serializedSnapshots.slice(fromSnapshot).slice(-5).map(trimSnapshot),
    app_tail: appTailLines.slice(-40),
  });
}

async function observeFor(seconds, fromSnapshot, gate) {
  const deadline = Date.now() + seconds * 1000;
  const samples = [];
  while (Date.now() < deadline) {
    checkInterrupted();
    if (childExited(appChild)) {
      failGate(gate, `app exited during observation: ${childExitDescription(appChild)}`, {
        app_tail: appTailLines.slice(-40),
      });
    }
    const snapshot = latestSnapshot(fromSnapshot);
    if (snapshot && (snapshot.connectionState === 'error' || (typeof snapshot.error === 'string' && snapshot.error))) {
      failGate(gate, `app reported an error during observation: ${snapshot.error ?? 'connectionState=error'}`, {
        snapshot: trimSnapshot(snapshot),
      });
    }
    if (snapshot) samples.push(trimSnapshot(snapshot));
    await sleep(250);
  }
  return samples.slice(-20);
}

function childPids(pid) {
  const helper = runHelper('swift', ['-e', `
import Darwin
let ppid: pid_t = ${pid}
var pids = [pid_t](repeating: 0, count: 128)
let count = pids.withUnsafeMutableBytes { proc_listchildpids(ppid, $0.baseAddress, Int32($0.count)) }
for child in pids.prefix(max(0, Int(count))) { print(child) }
`], 30_000);
  if (!helper.error && helper.status === 0) {
    return {
      pids: helper.stdout.split(/\s+/).filter(Boolean).map(Number).filter(Number.isInteger),
      method: 'proc_listchildpids',
    };
  }
  // `ps`/`pgrep` are denied in some sandboxes. Keep them only as a fallback.
  const fallback = spawnSync('pgrep', ['-P', String(pid)], {encoding: 'utf8'});
  if (!fallback.error && fallback.status === 0) {
    return {
      pids: fallback.stdout.split(/\s+/).filter(Boolean).map(Number).filter(Number.isInteger),
      method: 'pgrep',
    };
  }
  return {
    pids: [],
    method: 'unavailable',
    detail: `proc_listchildpids exit ${helper.status}; pgrep exit ${fallback.status}`,
  };
}

function childExited(child) {
  return !child || child.exitCode !== null || child.signalCode !== null;
}

function childExitDescription(child) {
  if (!child) return 'no child process';
  if (child.signalCode) return `signal ${child.signalCode}`;
  return `code ${child.exitCode}`;
}

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function waitForExit(child, timeout) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (childExited(child)) return true;
    await sleep(100);
  }
  return childExited(child);
}

async function recordOwnedChildren(child, expected) {
  const collected = new Set();
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (!child || childExited(child)) break;
    for (const pid of childPids(child.pid).pids) collected.add(pid);
    if (collected.size >= expected) break;
    await sleep(250);
  }
  return [...collected];
}

async function terminateApp() {
  if (!appChild) return {status: 'absent'};
  const pid = appChild.pid;
  const discovery = childPids(pid);
  const children = [...new Set([...discovery.pids, ...recordedAppChildren])];
  if (childExited(appChild)) {
    for (const child of children) {
      if (!pidAlive(child)) continue;
      try { process.kill(child, 'SIGTERM'); } catch {}
      await sleep(500);
      if (pidAlive(child)) {
        try { process.kill(child, 'SIGKILL'); } catch {}
      }
    }
    return {
      status: 'already_exited',
      pid,
      exit: appChild.exitCode,
      signal: appChild.signalCode,
      child_pids: children,
      child_discovery: discovery.method,
      child_discovery_detail: discovery.detail ?? null,
    };
  }
  const graceful = runHelper('swift', ['-e', `
import AppKit
import Foundation
let pid: pid_t = ${pid}
guard let app = NSRunningApplication(processIdentifier: pid) else { exit(2) }
if app.isTerminated { exit(0) }
exit(app.terminate() ? 0 : 3)
`], 30_000);
  if (!graceful.error && graceful.status === 0 && await waitForExit(appChild, 10_000)) {
    appExitMethod = 'NSRunningApplication.terminate';
  } else {
    try { appChild.kill('SIGTERM'); } catch {}
    if (await waitForExit(appChild, 5_000)) {
      appExitMethod = 'SIGTERM';
    } else {
      try { appChild.kill('SIGKILL'); } catch {}
      await waitForExit(appChild, 5_000);
      appExitMethod = 'SIGKILL';
    }
  }
  const orphanedChildren = [];
  for (const child of children) {
    if (!pidAlive(child)) continue;
    orphanedChildren.push(child);
    try { process.kill(child, 'SIGTERM'); } catch {}
    await sleep(500);
    if (pidAlive(child)) {
      try { process.kill(child, 'SIGKILL'); } catch {}
    }
  }
  return {
    status: childExited(appChild) ? 'exited' : 'alive',
    pid,
    exit: appChild.exitCode,
    signal: appChild.signalCode,
    method: appExitMethod,
    child_pids: children,
    child_discovery: discovery.method,
    child_discovery_detail: discovery.detail ?? null,
    orphaned_child_pids: orphanedChildren,
  };
}

// The fixture owns a Host and an endpoint child process. Its own `quit`
// teardown closes them, so a clean cleanup needs a clean graceful exit or, for
// a forced exit, proof that every recorded child is gone.
function decideFixtureCleanup({method, exitCode, signalCode, discoveryMethod, survivors}) {
  // A clean cleanup needs a graceful exit that closed its own children, or a
  // forced exit whose recorded children are all verifiably gone.
  const gracefulExit = (method === 'quit' || method === 'already_exited')
    && exitCode === 0 && signalCode === null;
  const forcedExit = method === 'SIGTERM' || method === 'SIGKILL';
  const childrenVerified = !forcedExit || discoveryMethod !== 'unavailable';
  const ok = survivors.length === 0 && childrenVerified && (gracefulExit || forcedExit);
  return {ok, exitFailed: !gracefulExit};
}

function selfTestFixtureCleanupDecision() {
  const cases = [
    ['graceful quit exits cleanly', {method: 'quit', exitCode: 0, signalCode: null, discoveryMethod: 'proc_listchildpids', survivors: []}, true],
    ['quit then killed by a signal is not a clean exit', {method: 'quit', exitCode: null, signalCode: 'SIGKILL', discoveryMethod: 'proc_listchildpids', survivors: []}, false],
    ['pre-exit with a nonzero code is not clean', {method: 'already_exited', exitCode: 1, signalCode: null, discoveryMethod: 'proc_listchildpids', survivors: []}, false],
    ['pre-exit killed by a signal is not clean', {method: 'already_exited', exitCode: null, signalCode: 'SIGTERM', discoveryMethod: 'proc_listchildpids', survivors: []}, false],
    ['forced exit without child discovery is unverified', {method: 'SIGKILL', exitCode: null, signalCode: 'SIGKILL', discoveryMethod: 'unavailable', survivors: []}, false],
    ['forced exit with every recorded child gone is clean', {method: 'SIGKILL', exitCode: null, signalCode: 'SIGKILL', discoveryMethod: 'proc_listchildpids', survivors: []}, true],
    ['a surviving owned child is never clean', {method: 'SIGTERM', exitCode: null, signalCode: 'SIGTERM', discoveryMethod: 'proc_listchildpids', survivors: [42]}, false],
    ['a survivor after graceful quit is not clean', {method: 'quit', exitCode: 0, signalCode: null, discoveryMethod: 'proc_listchildpids', survivors: [42]}, false],
  ];
  let failures = 0;
  for (const [name, input, expected] of cases) {
    const {ok} = decideFixtureCleanup(input);
    if (ok !== expected) {
      failures += 1;
      process.stdout.write(`SELF_TEST_FAIL ${name}: expected ok=${expected}, got ok=${ok}\n`);
    } else {
      process.stdout.write(`SELF_TEST_PASS ${name}\n`);
    }
  }
  process.stdout.write(failures === 0 ? 'SELF_TEST_OK\n' : `SELF_TEST_FAILURES ${failures}\n`);
  return failures === 0;
}

async function terminateFixture() {
  if (!fixtureChild) return {status: 'absent'};
  const pid = fixtureChild.pid;
  // The fixture owns its Host and endpoint children. A forced exit skips its
  // Rust teardown, so keep the owned PIDs from before termination.
  const discovery = childPids(pid);
  const ownedChildren = [...new Set([...discovery.pids, ...recordedFixtureChildren])];
  if (!childExited(fixtureChild)) {
    try { fixtureChild.stdin.write('quit\n'); } catch {}
    if (await waitForExit(fixtureChild, 30_000)) {
      fixtureExitMethod = 'quit';
    } else {
      try { fixtureChild.kill('SIGTERM'); } catch {}
      if (await waitForExit(fixtureChild, 5_000)) {
        fixtureExitMethod = 'SIGTERM';
      } else {
        try { fixtureChild.kill('SIGKILL'); } catch {}
        await waitForExit(fixtureChild, 5_000);
        fixtureExitMethod = 'SIGKILL';
      }
    }
  }
  const closedChildren = [];
  for (const child of ownedChildren) {
    if (!pidAlive(child)) continue;
    closedChildren.push(child);
    try { process.kill(child, 'SIGTERM'); } catch {}
    await sleep(500);
    if (pidAlive(child)) {
      try { process.kill(child, 'SIGKILL'); } catch {}
      await sleep(500);
    }
  }
  const survivors = [pid, ...ownedChildren].filter(candidate => pidAlive(candidate));
  const exit = fixtureChild.exitCode;
  const method = fixtureExitMethod ?? (exit !== null ? 'already_exited' : (fixtureChild.signalCode ?? 'unknown'));
  const decision = decideFixtureCleanup({
    method,
    exitCode: exit,
    signalCode: fixtureChild.signalCode,
    discoveryMethod: discovery.method,
    survivors,
  });
  let rootRemoved = false;
  const fixtureRoot = observations.fixture_info?.fixture;
  if (decision.ok && typeof fixtureRoot === 'string'
      && /^\/tmp\/an-[0-9a-f]{32}$/.test(fixtureRoot) && existsSync(fixtureRoot)) {
    rmSync(fixtureRoot, {recursive: true, force: true});
    rootRemoved = true;
  }
  return {
    status: decision.ok ? 'exited' : 'failed',
    pid,
    exit,
    signal: fixtureChild.signalCode,
    method,
    child_pids: ownedChildren,
    child_discovery: discovery.method,
    child_discovery_detail: discovery.detail ?? null,
    closed_child_pids: closedChildren,
    surviving_pids: survivors,
    exit_failed: decision.exitFailed,
    fixture_root_removed: rootRemoved,
  };
}

async function cleanup() {
  const cleanupReceipt = {app: null, fixture: null};
  try {
    cleanupReceipt.app = await terminateApp();
    if (cleanupReceipt.app.status === 'alive') {
      throw new Error(`app ${cleanupReceipt.app.pid} is still alive after SIGKILL`);
    }
    passGate('CLEANUP_APP', `app ${cleanupReceipt.app.status}`, cleanupReceipt.app);
  } catch (error) {
    cleanupFailed = true;
    cleanupReceipt.app = {status: 'failed', detail: error.message};
    gates.push({name: 'CLEANUP_APP', status: 'FAIL', detail: error.message, at: now()});
  }
  try {
    cleanupReceipt.fixture = await terminateFixture();
    if (cleanupReceipt.fixture.status !== 'exited') {
      const survivors = (cleanupReceipt.fixture.surviving_pids ?? []).join(', ') || 'none';
      throw new Error(`fixture teardown failed: exit=${cleanupReceipt.fixture.exit} surviving=${survivors} method=${cleanupReceipt.fixture.method}`);
    }
    passGate('CLEANUP_FIXTURE', `fixture ${cleanupReceipt.fixture.status}`, cleanupReceipt.fixture);
  } catch (error) {
    cleanupFailed = true;
    cleanupReceipt.fixture = {status: 'failed', detail: error.message};
    gates.push({name: 'CLEANUP_FIXTURE', status: 'FAIL', detail: error.message, at: now()});
  }
  if (existsSync(persistedHome)) {
    rmSync(persistedHome, {recursive: true, force: true});
  }
  const homeRemoved = !existsSync(persistedHome);
  cleanupReceipt.scratch_home = {path: persistedHome, removed: homeRemoved};
  if (homeRemoved) {
    passGate('CLEANUP_SCRATCH_HOME', 'throwaway HOME removed', cleanupReceipt.scratch_home);
  } else {
    cleanupFailed = true;
    gates.push({name: 'CLEANUP_SCRATCH_HOME', status: 'FAIL', detail: `scratch HOME still exists at ${persistedHome}`, at: now()});
  }
  appCombinedStream?.end();
  appStdoutStream?.end();
  appStderrStream?.end();
  fixtureStdoutStream?.end();
  fixtureStderrStream?.end();
  return cleanupReceipt;
}

function writeReceipt(cleanupReceipt, result) {
  const failedGate = gates.find(gate => gate.status === 'FAIL') ?? null;
  const receipt = {
    schema: 'agentbrowser.mac-user-acceptance.v1',
    run_id: runId,
    result,
    scope: {
      automated: options.persistedUnreachable
        ? 'build fixture and app, throwaway HOME store with a closed port, one visible connect error, no retry'
        : options.persistedPairing
          ? 'build fixture and app, throwaway HOME store, one-time import, no-argument launch, auto connect, rendered frame, takeover, release, human disconnect without reconnect, independent DOM unchanged check'
          : 'build fixture and app, isolated Host, AX connect, rendered frame, takeover, release, independent DOM unchanged check',
      manual: 'native click, ASCII text, scroll and Chinese IME',
      not_claimed: 'P5, P6 or M1 completion',
    },
    started_at: startedAt,
    finished_at: now(),
    environment: {
      platform: process.platform,
      node: process.version,
      worktree: root,
      receipt_directory: runDirectory,
      protocol_root: protocolRoot,
      bin_directory: binDirectory,
      module_cache: moduleCache,
      app_executable: appExecutable,
      fixture_executable: fixtureExecutable,
    },
    first_failing_gate: failedGate?.name ?? null,
    failure_detail: failedGate?.detail ?? null,
    failure_evidence: failedGate?.evidence ?? null,
    gates,
    observations,
    manual,
    cleanup: cleanupReceipt,
    artifacts: {
      app_log: join(runDirectory, 'app.log'),
      app_stdout: join(runDirectory, 'app.stdout.log'),
      app_stderr: join(runDirectory, 'app.stderr.log'),
      fixture_stdout: join(runDirectory, 'fixture.stdout.log'),
      fixture_stderr: join(runDirectory, 'fixture.stderr.log'),
      ax_directory: join(runDirectory, 'ax'),
    },
    diagnostics: {
      app_tail: appTailLines.slice(-40),
      fixture_stdout_tail: outputTail(fixtureStdoutBuffer),
      fixture_stderr_tail: outputTail(fixtureStderrBuffer),
      recent_snapshots: serializedSnapshots.slice(-5).map(trimSnapshot),
      recent_rejections: rejections.slice(-5),
    },
  };
  const path = join(runDirectory, 'receipt.json');
  writeFileSync(path, `${JSON.stringify(receipt, null, 2)}\n`);
  return path;
}

// One place closes both app processes, writes the receipt and sets the exit code,
// so every mode reports cleanup the same way.
async function finishRun(message) {
  const cleanupReceipt = await cleanup();
  receiptPath = writeReceipt(cleanupReceipt, cleanupFailed ? 'FAIL' : 'PASS_AUTOMATED');
  if (cleanupFailed) {
    process.stderr.write('FAIL CLEANUP: one or more child resources did not close cleanly\n');
    process.stderr.write(`receipt: ${receiptPath}\n`);
    process.exitCode = 1;
  } else {
    process.stdout.write(`${message}\n`);
    process.stdout.write(`receipt: ${receiptPath}\n`);
    process.stdout.write('manual: native click/text/scroll/IME PENDING_HUMAN\n');
    process.exitCode = 0;
  }
}

let receiptPath = null;
try {
  preflight();
  buildFixture();
  buildApp();
  await startFixture();
  recordedFixtureChildren = await recordOwnedChildren(fixtureChild, 2);
  observations.fixture_children_at_start = recordedFixtureChildren;

  const baselineRaw = await fixtureCommand('inspect', 'FIXTURE_INSPECT_BASELINE');
  const baseline = findInspectSnapshot(baselineRaw);
  if (!baseline) {
    failGate('FIXTURE_INSPECT_BASELINE', `fixture inspect did not contain clicked/text/scrollY/maxScroll: ${JSON.stringify(baselineRaw)}`, {
      fixture_inspect: baselineRaw,
    });
  }
  if (baseline.clicked !== 0 || baseline.text !== '' || baseline.scrollY !== 0) {
    failGate('FIXTURE_BASELINE', 'the isolated Host page was not pristine before the app connected', {fixture_inspect: baseline});
  }
  observations.baseline_inspect = baseline;
  passGate('FIXTURE_INSPECT_BASELINE', 'fixture page is pristine before connect', {fixture_inspect: baseline});

  if (options.persistedPairing) {
    await importPairingThroughBridge(observations.fixture_info.bundle);
  }
  if (options.persistedUnreachable) {
    writeUnreachableStore();
  }
  launchApp();
  recordedAppChildren = await recordOwnedChildren(appChild, 1);
  observations.app_children_at_start = recordedAppChildren;
  const connectSnapshotStart = serializedSnapshots.length;
  const connectRejectionStart = rejections.length;
  if (options.persistedPairing) {
    // The ordinary user case: the import happened once, before this launch, and
    // the app is started with no pairing argument at all. The connect must come
    // from the app itself, so this path presses nothing to connect.
    await waitForSnapshot(
      'PERSISTED_AUTO_CONNECT',
      connectSnapshotStart,
      connectRejectionStart,
      snapshot => snapshot.connectionState === 'connected' && Number(snapshot.renderedFrames) > 0,
      'connectionState=connected with renderedFrames>0 after a no-argument launch',
    );
    if (observations.persisted_pairing) observations.persisted_pairing.auto_connect = 'connected';
    passGate('PERSISTED_AUTO_CONNECT', 'a no-argument launch connected through the persisted store', {
      store: persistedStore,
      launch_arguments: [],
    });
    // The drawer holds the takeover control; the connect itself came from the app.
    await pressAxButton(appChild.pid, '打开菜单', 'AX_OPEN_MENU');
    await sleep(750);
  } else if (!options.persistedUnreachable) {
    await pressAxButton(appChild.pid, '打开菜单', 'AX_OPEN_MENU');
    await sleep(750);
    await pressAxButton(appChild.pid, '连接', 'AX_CONNECT');
  }
  if (options.persistedUnreachable) {
    // A configured store that points at a closed port must fail loudly once.
    const failedSnapshot = await waitForFailureSnapshot(
      'PERSISTED_UNREACHABLE',
      connectSnapshotStart,
      connectRejectionStart,
      'a visible connection error after a no-argument launch',
    );
    observations.persisted_failure = trimSnapshot(failedSnapshot);
    if (String(failedSnapshot.error).includes('unreachable-token')) {
      failGate('PERSISTED_UNREACHABLE_ERROR', 'the connection error exposed the store token', {
        error: failedSnapshot.error,
      });
    }
    passGate('PERSISTED_UNREACHABLE_ERROR', `visible error: ${failedSnapshot.error}`, {
      connection_state: failedSnapshot.connectionState ?? null,
      error: failedSnapshot.error,
      store: persistedStore,
      endpoint: unreachableEndpoint,
      launch_arguments: [],
    });
    await sleep(4000);
    const attempts = countNativeCommands('connect');
    observations.connect_attempts = attempts;
    if (attempts !== 1) {
      failGate('PERSISTED_NO_RETRY', `expected exactly one connect attempt, saw ${attempts}`, {
        attempts,
        held_seconds: 4,
      });
    }
    passGate('PERSISTED_NO_RETRY', 'the failed launch connected once and did not retry', {
      attempts,
      held_seconds: 4,
    });
    await finishRun('PASS_AUTOMATED persisted pairing failure path');
  } else {
    const connected = await waitForSnapshot(
      'CONNECT',
      connectSnapshotStart,
      connectRejectionStart,
      snapshot => snapshot.connectionState === 'connected' && Number(snapshot.renderedFrames) > 0,
      'connectionState=connected with renderedFrames>0',
    );
    observations.connect_snapshot = trimSnapshot(connected);
    if (connected.sessionId !== observations.fixture_info.session) {
      failGate('SESSION_IDENTITY', `app session ${connected.sessionId ?? 'missing'} does not match fixture session ${observations.fixture_info.session}`, {
        app_snapshot: trimSnapshot(connected),
        fixture_session: observations.fixture_info.session,
      });
    }
    passGate('CONNECT', `connected with renderedFrames=${connected.renderedFrames}`, {
      session: connected.sessionId,
      renderedFrames: connected.renderedFrames,
    });

    const observeStart = serializedSnapshots.length;
    observations.observe_before_takeover = await observeFor(options.observeSeconds, observeStart, 'OBSERVE_BEFORE_TAKEOVER');
    passGate('OBSERVE_BEFORE_TAKEOVER', `held observation for ${options.observeSeconds}s`, {
      samples: observations.observe_before_takeover.length,
    });

    const takeoverSnapshotStart = serializedSnapshots.length;
    const takeoverRejectionStart = rejections.length;
    await pressAxButton(appChild.pid, '接管', 'AX_TAKEOVER');
    const takeover = await waitForSnapshot(
      'TAKEOVER',
      takeoverSnapshotStart,
      takeoverRejectionStart,
      snapshot => snapshot.connectionState === 'connected' && snapshot.controlMode === 'control' && snapshot.inputReady === true,
      'controlMode=control with inputReady=true',
    );
    observations.takeover_snapshot = trimSnapshot(takeover);
    observations.visible_content = takeover.layout?.visibleContent ?? takeover.visibleContent ?? null;
    if (observations.visible_content !== 'native-video') {
      failGate('NATIVE_VIDEO', `takeover snapshot did not report native-video content: ${observations.visible_content ?? 'missing'}`, {
        snapshot: trimSnapshot(takeover),
      });
    }
    passGate('TAKEOVER', 'controlMode=control, inputReady=true', {
      controlMode: takeover.controlMode,
      inputReady: takeover.inputReady,
      visibleContent: observations.visible_content,
    });
    passGate('NATIVE_VIDEO', 'visibleContent=native-video', {layout: takeover.layout ?? null});
    await assertChromeStripPainted(appChild.pid);

    const releaseSnapshotStart = serializedSnapshots.length;
    const releaseRejectionStart = rejections.length;
    await pressAxButton(appChild.pid, '返回观察', 'AX_RELEASE');
    const released = await waitForSnapshot(
      'RELEASE',
      releaseSnapshotStart,
      releaseRejectionStart,
      snapshot => snapshot.connectionState === 'connected' && snapshot.controlMode === 'observe',
      'controlMode=observe after returning observation',
    );
    observations.release_snapshot = trimSnapshot(released);
    passGate('RELEASE', 'controlMode=observe', {controlMode: released.controlMode});

    const observeAfterStart = serializedSnapshots.length;
    observations.observe_after_release = await observeFor(options.observeSeconds, observeAfterStart, 'OBSERVE_AFTER_RELEASE');
    passGate('OBSERVE_AFTER_RELEASE', `held observation for ${options.observeSeconds}s`, {
      samples: observations.observe_after_release.length,
    });

    await sleep(500);
    const afterRaw = await fixtureCommand('inspect', 'FIXTURE_INSPECT_AFTER');
    const after = findInspectSnapshot(afterRaw);
    if (!after) {
      failGate('FIXTURE_INSPECT_AFTER', `fixture inspect did not contain clicked/text/scrollY/maxScroll: ${JSON.stringify(afterRaw)}`, {
        fixture_inspect: afterRaw,
      });
    }
    observations.after_inspect = after;
    const changedFields = ['clicked', 'text', 'scrollY', 'maxScroll', 'scrollTrace', 'editorEvents']
      .filter(field => !isDeepStrictEqual(after[field], baseline[field]));
    if (changedFields.length) {
      failGate('OBSERVATION_UNCHANGED', `Host page changed during observe/takeover/release: ${changedFields.join(', ')}`, {
        baseline,
        after,
        changed_fields: changedFields,
      });
    }
    passGate('OBSERVATION_UNCHANGED', 'fixture inspect matches the pre-connect baseline after release', {
      baseline,
      after,
    });

    if (options.persistedPairing) {
      // The once-only auto-connect must not turn into a reconnect loop. A human
      // disconnect has to stay disconnected with no further connect command.
      const disconnectStart = serializedSnapshots.length;
      const disconnectRejectionStart = rejections.length;
      await pressAxButton(appChild.pid, '断开', 'AX_DISCONNECT');
      await waitForSnapshot(
        'PERSISTED_DISCONNECT',
        disconnectStart,
        disconnectRejectionStart,
        snapshot => snapshot.connectionState === 'stopped' || snapshot.connectionState === 'idle',
        'connectionState=stopped after a human disconnect',
      );
      await sleep(3000);
      const attempts = countNativeCommands('connect');
      observations.connect_attempts = attempts;
      const latestState = serializedSnapshots.at(-1)?.connectionState ?? null;
      if (attempts !== 1) {
        failGate('PERSISTED_NO_RECONNECT', `the app issued ${attempts} connect commands; a human disconnect must not reconnect`, {
          attempts,
        });
      }
      if (latestState === 'connected' || latestState === 'connecting') {
        failGate('PERSISTED_NO_RECONNECT', `the app reconnected after a human disconnect: ${latestState}`, {
          connection_state: latestState,
          latest_snapshot: trimSnapshot(serializedSnapshots.at(-1) ?? {}),
        });
      }
      passGate('PERSISTED_NO_RECONNECT', 'a human disconnect stayed disconnected', {
        attempts,
        held_seconds: 3,
        connection_state: latestState,
      });
    }

    await finishRun('PASS_AUTOMATED connect/frame/takeover/release/inspect');
  }
} catch (error) {
  failure = {
    gate: error?.gate ?? 'UNEXPECTED',
    detail: error?.message ?? String(error),
    evidence: error?.evidence ?? null,
  };
  gates.push({name: failure.gate, status: 'FAIL', detail: failure.detail, evidence: failure.evidence, at: now()});
  const cleanupReceipt = await cleanup();
  receiptPath = writeReceipt(cleanupReceipt, 'FAIL');
  process.stderr.write(`FAIL ${failure.gate}: ${failure.detail}\n`);
  process.stderr.write(`receipt: ${receiptPath}\n`);
  process.exitCode = 1;
}

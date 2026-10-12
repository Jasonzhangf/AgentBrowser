const { writeFile, mkdir, readFile } = await import('node:fs/promises');
const { createHash } = await import('node:crypto');
const { join } = await import('node:path');

const root = '/Volumes/Intel/playground/agentbrowser/m1-ui-connection-recovery-r1-20261004/docs/runs/m1-ui-connection-recovery-r1-20261004';
const params = JSON.parse(await readFile(`${root}/blackbox/params.json`, 'utf8'));
const baseUrl = params.baseUrl;
const bundleDir = params.bundleDir ?? null;
const sourceCommit = params.sourceCommit ?? null;
const sourceTree = params.sourceTree ?? null;
const finishTask = params.finishTask !== false;
if (!baseUrl) throw new Error('E2E_URL_REQUIRED');

const task = await taskSpace(params.spaceId);
console.log(JSON.stringify({spaceId: task.spaceId, name: task.name}));
const page = task.page('p1');

const expectedSafeInset = 24;
const screenshots = {};
const cases = {};
const assertions = [];
let finishReceipt = null;
let failure = null;

function check(condition, message, evidence) {
  assertions.push({ok: Boolean(condition), message, evidence});
}

function countOp(items, op) {
  return items.filter(item => item.op === op).length;
}

function setupOps(items) {
  return items.filter(item => item.op !== 'set_presentation');
}

function near(value, expected, tolerance = 0.51) {
  return typeof value === 'number' && Math.abs(value - expected) <= tolerance;
}

async function setViewport(width, height, mobile) {
  await page.cdp('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: mobile ? 2 : 1,
    mobile,
  });
}

async function navigate() {
  await page.goto(baseUrl);
  await page.waitForSelector('#scenario-setup', {state: 'visible'});
  await page.waitForFunction(() => {
    const frame = document.querySelector('#consumer-frame');
    return Boolean(frame?.contentDocument?.querySelector('main'));
  }, undefined, {timeout: 5000});
}

async function waitForDrawer(open) {
  await page.waitForFunction((expected) => {
    const frame = document.querySelector('#consumer-frame');
    const main = frame?.contentDocument?.querySelector('main');
    if (main?.getAttribute('data-drawer-open') !== String(expected)) return false;
    const drawer = frame?.contentDocument?.querySelector('#browser-drawer');
    const win = frame?.contentWindow;
    if (!drawer || !win) return false;
    if (!expected) return getComputedStyle(drawer).visibility === 'hidden';
    const rect = drawer.getBoundingClientRect();
    return rect.right <= win.innerWidth + 0.51;
  }, open, {timeout: 5000});
}

async function safeClick(selector, label) {
  try {
    await page.evaluate((sel) => {
      const frame = document.querySelector('#consumer-frame');
      const element = document.querySelector(sel) ?? frame?.contentDocument?.querySelector(sel);
      element?.scrollIntoView({block: 'center', inline: 'nearest'});
    }, selector);
    await page.click(selector, {label});
  } catch (error) {
    const diag = await page.evaluate((sel) => {
      const frame = document.querySelector('#consumer-frame');
      const frameDoc = frame?.contentDocument ?? null;
      const element = document.querySelector(sel) ?? frameDoc?.querySelector(sel);
      const rect = element?.getBoundingClientRect();
      const win = element?.ownerDocument?.defaultView ?? window;
      const top = rect ? win.document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2) : null;
      return {
        selector: sel,
        inFrame: Boolean(frameDoc?.querySelector(sel)),
        exists: Boolean(element),
        disabled: element?.disabled ?? null,
        rect: rect ? {x: rect.x, y: rect.y, width: rect.width, height: rect.height} : null,
        topElement: top ? (top.id || top.className || top.tagName) : null,
        drawerOpen: frameDoc?.querySelector('main')?.getAttribute('data-drawer-open') ?? null,
        scrollY: win.scrollY,
      };
    }, selector);
    throw new Error(`safeClick(${label}) failed: ${String(error?.message ?? error)} diag=${JSON.stringify(diag)}`);
  }
}

async function chooseScenario(id, scenarioName = id.replace(/^scenario-/, ''), openDrawer = true) {
  const drawerOpen = await page.evaluate(() => {
    const main = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('main');
    return main?.getAttribute('data-drawer-open') === 'true';
  });
  if (drawerOpen) {
    await safeClick('#drawer-close', `close ${scenarioName} before switch`);
    try {
      await waitForDrawer(false);
    } catch (error) {
      const diag = await scenarioDiagnostics(scenarioName, 'close-before-switch');
      throw new Error(`chooseScenario(${scenarioName}) close failed: ${String(error?.message ?? error)} ${JSON.stringify(diag)}`);
    }
  }
  await safeClick(`#${id}`, `select ${scenarioName}`);
  try {
    await page.waitForFunction((scenario) => {
      const header = document.querySelector('.consumer-header');
      const applied = document.querySelector('#consumer-frame')?.contentWindow?.__w4Scenario;
      return (header?.textContent?.includes(`scenario=${scenario}`) ?? false) && applied === scenario;
    }, scenarioName, {timeout: 5000});
  } catch (error) {
    const diag = await scenarioDiagnostics(scenarioName, 'apply-scenario');
    throw new Error(`chooseScenario(${scenarioName}) apply failed: ${String(error?.message ?? error)} ${JSON.stringify(diag)}`);
  }
  await page.waitForFunction(() => {
    const frame = document.querySelector('#consumer-frame');
    return Boolean(frame?.contentDocument?.querySelector('main'));
  }, undefined, {timeout: 5000});
  await page.waitForTimeout(300);
  if (openDrawer) {
    await safeClick('#drawer-toggle', `open ${scenarioName} drawer`);
    try {
      await waitForDrawer(true);
    } catch (error) {
      const diag = await scenarioDiagnostics(scenarioName, 'open-drawer');
      throw new Error(`chooseScenario(${scenarioName}) open failed: ${String(error?.message ?? error)} ${JSON.stringify(diag)}`);
    }
    await page.waitForTimeout(250);
  }
}

async function scenarioDiagnostics(scenarioName, phase) {
  return page.evaluate((info) => {
    const frame = document.querySelector('#consumer-frame');
    const doc = frame?.contentDocument;
    const main = doc?.querySelector('main');
    return {
      phase: info.phase,
      expected: info.scenarioName,
      header: document.querySelector('.consumer-header')?.textContent?.trim() ?? null,
      appliedScenario: frame?.contentWindow?.__w4Scenario ?? null,
      frameHasMain: Boolean(main),
      drawerOpen: main?.getAttribute('data-drawer-open') ?? null,
      drawerVisibility: doc?.querySelector('#browser-drawer') ? getComputedStyle(doc.querySelector('#browser-drawer')).visibility : null,
      statusTitle: doc?.querySelector('.status-title')?.textContent?.trim() ?? null,
      commandCount: JSON.parse(document.querySelector('#commands')?.textContent ?? '[]').length,
    };
  }, {scenarioName, phase});
}

async function readUi() {
  return page.evaluate(() => {
    const frame = document.querySelector('#consumer-frame');
    const doc = frame?.contentDocument;
    const win = frame?.contentWindow;
    const main = doc?.querySelector('main');
    const drawer = doc?.querySelector('#browser-drawer');
    const drawerRect = drawer?.getBoundingClientRect();
    const button = (id) => {
      const element = doc?.querySelector(`#${id}`);
      return element ? {disabled: element.disabled, text: element.textContent?.trim() ?? null} : null;
    };
    return {
      viewport: win ? {width: win.innerWidth, height: win.innerHeight} : null,
      drawerOpen: main?.getAttribute('data-drawer-open') ?? null,
      presentation: main?.getAttribute('data-presentation') ?? null,
      statusTitle: doc?.querySelector('.status-title')?.textContent?.trim() ?? null,
      statusMeta: doc?.querySelector('.status-meta')?.textContent?.trim() ?? null,
      label: doc?.querySelector('[data-connection-label]')?.textContent?.trim() ?? null,
      activeRevision: doc?.querySelector('[data-active-revision]')?.getAttribute('data-active-revision') ?? null,
      error: doc?.querySelector('.chrome-error')?.textContent?.replace(/\s+/g, ' ').trim() ?? null,
      capabilityMessage: doc?.querySelector('.capability-unavailable')?.textContent?.trim() ?? null,
      setup: button('connection-setup'),
      import: button('connection-import'),
      retry: button('connection-retry'),
      repair: button('connection-repair'),
      connect: button('connect'),
      disconnect: button('disconnect'),
      viewerReady: doc?.querySelector('.viewer')?.getAttribute('data-ready') ?? null,
      visibleContent: doc?.querySelector('.viewer')?.getAttribute('data-visible-content') ?? null,
      drawerVisibility: drawer ? getComputedStyle(drawer).visibility : null,
      drawerRect: drawerRect ? {
        top: drawerRect.top,
        right: drawerRect.right,
        bottom: drawerRect.bottom,
        left: drawerRect.left,
        width: drawerRect.width,
        height: drawerRect.height,
      } : null,
      safeTop: main ? getComputedStyle(main).getPropertyValue('--safe-top').trim() : null,
      safeBottom: main ? getComputedStyle(main).getPropertyValue('--safe-bottom').trim() : null,
      horizontalOverflow: doc ? Math.max(doc.documentElement.scrollWidth, doc.body.scrollWidth) - doc.documentElement.clientWidth : null,
      sensitiveInputs: doc ? doc.querySelectorAll('input[type="password"], textarea, [contenteditable="true"]').length : null,
      inputs: doc ? [...doc.querySelectorAll('input')].map(input => ({id: input.id, type: input.type})) : [],
    };
  });
}

async function commands() {
  return page.evaluate(() => JSON.parse(document.querySelector('#commands')?.textContent ?? '[]'));
}

async function capture(viewport, name) {
  const path = `${root}/screenshots/${viewport}-${name}.png`;
  screenshots[`${viewport}-${name}`] = path;
  await page.screenshot({path});
  return path;
}

async function bundleHashes() {
  if (!bundleDir) return {};
  const names = ['app.js', 'app.css', 'consumer.js', 'consumer-frame.js', 'consumer.html', 'ui-frame.html'];
  const hashes = {};
  for (const name of names) {
    const body = await readFile(join(bundleDir, name));
    hashes[name] = createHash('sha256').update(body).digest('hex');
  }
  return hashes;
}

async function assertNoForbiddenCommands(items, label) {
  const forbidden = items.filter(item => /viewport|declare|resize|reload|navigate|input|send|remote_ime|click|scroll/i.test(item.op ?? ''));
  check(forbidden.length === 0, `${label}: no viewport, reload, remote-input, or navigation command`, forbidden);
}

async function requiredImportCase(viewport) {
  await chooseScenario('scenario-setup');
  const before = await readUi();
  const beforeCommands = await commands();
  check(before.statusTitle === '需要配置连接', `${viewport}: required state is explicit`);
  check(before.setup?.disabled === false, `${viewport}: required state enables native setup`);
  check(before.import?.disabled === false, `${viewport}: required state enables ordinary import`);
  check(before.connect?.disabled === true, `${viewport}: required state refuses connect`);
  check(before.retry === null && before.repair === null, `${viewport}: required state has no recovery action`);
  check(setupOps(beforeCommands).length === 0, `${viewport}: opening required drawer emits no setup intent`);
  await assertNoForbiddenCommands(beforeCommands, `${viewport}: required`);
  await safeClick('#connection-import', `${viewport} ordinary import from required`);
  await page.waitForFunction(() => {
    const title = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('.status-title');
    return title?.textContent?.includes('已断开') ?? false;
  }, undefined, {timeout: 5000});
  const after = await readUi();
  const afterCommands = await commands();
  check(after.connect?.disabled === false, `${viewport}: native configured receipt enables connect`);
  check(countOp(afterCommands, 'import_connection') === 1, `${viewport}: required import emits exactly one ordinary intent`);
  check(countOp(afterCommands, 'open_connection_setup') === 0, `${viewport}: required import does not call setup`);
  check(countOp(afterCommands, 'retry_connection_setup') === 0, `${viewport}: required import does not retry reconcile`);
  check(countOp(afterCommands, 'repair_connection_import') === 0, `${viewport}: required import does not repair`);
  await assertNoForbiddenCommands(afterCommands, `${viewport}: required import`);
  cases[`${viewport}-required-import`] = {before, after, commands: afterCommands};
  await capture(viewport, 'required-import');
}

async function configuredReplaceCase(viewport) {
  await chooseScenario('scenario-configured-repair');
  const before = await readUi();
  const beforeCommands = await commands();
  check(before.statusTitle === '已断开，Host 页面保留', `${viewport}: configured state keeps connection status`);
  check(before.label?.includes('Configured phone'), `${viewport}: configured label is visible`);
  check(before.activeRevision === '3', `${viewport}: configured active revision is native-owned`);
  check(before.setup?.disabled === false && before.import?.disabled === false, `${viewport}: configured state allows ordinary import`);
  check(before.retry === null && before.repair === null, `${viewport}: configured state has no repair action`);
  await assertNoForbiddenCommands(beforeCommands, `${viewport}: configured`);
  await safeClick('#connection-import', `${viewport} replace ordinary import`);
  await page.waitForFunction(() => {
    const label = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('[data-connection-label]');
    return label?.textContent?.includes('Repaired phone') ?? false;
  }, undefined, {timeout: 5000});
  const after = await readUi();
  const afterCommands = await commands();
  check(countOp(afterCommands, 'import_connection') === 1, `${viewport}: configured replacement emits one ordinary intent`);
  check(countOp(afterCommands, 'repair_connection_import') === 0, `${viewport}: configured replacement does not repair`);
  check(after.activeRevision === '6', `${viewport}: replacement consumes native revision`);
  await assertNoForbiddenCommands(afterCommands, `${viewport}: configured replacement`);
  cases[`${viewport}-configured-replace`] = {before, after, commands: afterCommands};
  await capture(viewport, 'configured-replace');
}

async function unresolvedCase(viewport) {
  await chooseScenario('scenario-unresolved-legacy-true');
  const blocked = await readUi();
  const blockedCommands = await commands();
  check(blocked.statusTitle === '连接配置提交状态未确认', `${viewport}: unresolved status is visible`);
  check(blocked.statusMeta?.includes('不会自动重试或重放导入'), `${viewport}: unresolved copy refuses auto recovery`);
  check(blocked.setup?.disabled === true && blocked.import?.disabled === true, `${viewport}: unresolved refuses ordinary setup/import`);
  check(blocked.connect?.disabled === true, `${viewport}: unresolved refuses NEW connect despite legacy networkConfigured=true`);
  check(blocked.repair === null, `${viewport}: unresolved has no repair action`);
  check(blocked.retry?.disabled === false, `${viewport}: unresolved exposes one explicit retry action`);
  check(setupOps(blockedCommands).length === 0, `${viewport}: unresolved opening emits no replay`);
  await assertNoForbiddenCommands(blockedCommands, `${viewport}: unresolved`);
  await safeClick('#connection-retry', `${viewport} explicit retry reconcile`);
  await page.waitForFunction(() => {
    const label = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('[data-connection-label]');
    return label?.textContent?.includes('Recovered phone') ?? false;
  }, undefined, {timeout: 5000});
  await page.waitForTimeout(700);
  const recovered = await readUi();
  const recoveredCommands = await commands();
  check(recovered.statusTitle === '已断开，Host 页面保留', `${viewport}: retry consumes confirmed native configured receipt`);
  check(recovered.connect?.disabled === false, `${viewport}: retry enables connect only after native configured receipt`);
  check(countOp(recoveredCommands, 'retry_connection_setup') === 1, `${viewport}: retry reconcile is sent exactly once`);
  check(countOp(recoveredCommands, 'import_connection') === 0 && countOp(recoveredCommands, 'repair_connection_import') === 0, `${viewport}: retry does not replay import`);
  await assertNoForbiddenCommands(recoveredCommands, `${viewport}: unresolved retry`);
  cases[`${viewport}-unresolved-retry`] = {blocked, recovered, commands: recoveredCommands};
  await capture(viewport, 'unresolved-retry');

  await chooseScenario('scenario-unresolved-still');
  await safeClick('#connection-retry', `${viewport} retry still-unresolved`);
  await page.waitForTimeout(700);
  const still = await readUi();
  const stillCommands = await commands();
  check(still.statusTitle === '连接配置提交状态未确认', `${viewport}: still-unresolved native receipt remains visible`);
  check(still.connect?.disabled === true, `${viewport}: still-unresolved does not guess success`);
  check(countOp(stillCommands, 'retry_connection_setup') === 1, `${viewport}: still-unresolved retry does not auto-repeat`);
  await assertNoForbiddenCommands(stillCommands, `${viewport}: still unresolved`);
  cases[`${viewport}-unresolved-still`] = {observed: still, commands: stillCommands};
  await capture(viewport, 'unresolved-still');
}

async function unavailableCase(viewport) {
  await chooseScenario('scenario-unavailable-repair');
  const allowed = await readUi();
  const allowedCommands = await commands();
  check(allowed.statusTitle === '连接配置不可用', `${viewport}: unavailable status is visible`);
  check(allowed.statusMeta?.includes('请显式替换连接包'), `${viewport}: unavailable copy exposes explicit repair`);
  check(allowed.label?.includes('Damaged phone'), `${viewport}: unavailable label is visible`);
  check(allowed.setup?.disabled === true && allowed.import?.disabled === true, `${viewport}: unavailable refuses ordinary setup/import`);
  check(allowed.connect?.disabled === true, `${viewport}: unavailable refuses ordinary connect`);
  check(allowed.retry === null, `${viewport}: unavailable has no implicit retry action`);
  check(allowed.repair?.disabled === false, `${viewport}: unavailable repair is enabled only by native permission`);
  await assertNoForbiddenCommands(allowedCommands, `${viewport}: unavailable`);
  await safeClick('#connection-repair', `${viewport} explicit repair import`);
  await page.waitForFunction(() => {
    const label = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('[data-connection-label]');
    return label?.textContent?.includes('Repaired phone') ?? false;
  }, undefined, {timeout: 5000});
  await page.waitForTimeout(700);
  const repaired = await readUi();
  const repairedCommands = await commands();
  check(repaired.connect?.disabled === false, `${viewport}: repair consumes confirmed native configured receipt`);
  check(countOp(repairedCommands, 'repair_connection_import') === 1, `${viewport}: repair intent is sent exactly once`);
  check(countOp(repairedCommands, 'import_connection') === 0, `${viewport}: repair is not rewritten as ordinary import`);
  await assertNoForbiddenCommands(repairedCommands, `${viewport}: repair`);
  cases[`${viewport}-unavailable-repair`] = {allowed, repaired, commands: repairedCommands};
  await capture(viewport, 'unavailable-repair');

  await chooseScenario('scenario-unavailable-no-repair');
  const denied = await readUi();
  const deniedCommands = await commands();
  check(denied.statusTitle === '连接配置不可用', `${viewport}: repair-denied status is visible`);
  check(denied.statusMeta?.includes('原生未允许替换连接包'), `${viewport}: repair-denied copy is truthful`);
  check(denied.setup?.disabled === true && denied.import?.disabled === true && denied.connect?.disabled === true, `${viewport}: repair-denied state refuses all ordinary actions`);
  check(denied.repair === null && denied.retry === null, `${viewport}: repair-denied state exposes no recovery action`);
  check(setupOps(deniedCommands).length === 0, `${viewport}: repair-denied state emits no setup intent`);
  await assertNoForbiddenCommands(deniedCommands, `${viewport}: repair denied`);
  cases[`${viewport}-unavailable-no-repair`] = {observed: denied, commands: deniedCommands};
  await capture(viewport, 'unavailable-no-repair');

  await chooseScenario('scenario-unavailable-repair-cancel');
  const beforeCancel = await readUi();
  await safeClick('#connection-repair', `${viewport} repair cancel once`);
  await page.waitForTimeout(700);
  const cancelled = await readUi();
  const cancelledCommands = await commands();
  check(beforeCancel.statusTitle === '连接配置不可用', `${viewport}: repair-cancel starts unavailable`);
  check(cancelled.statusTitle === '连接配置不可用', `${viewport}: cancelled picker keeps unavailable`);
  check(cancelled.repair?.disabled === false, `${viewport}: cancelled picker leaves repair reachable`);
  check(countOp(cancelledCommands, 'repair_connection_import') === 1, `${viewport}: cancelled picker sends one intent and no auto replay`);
  await safeClick('#connection-repair', `${viewport} repair after cancel`);
  await page.waitForFunction(() => {
    const label = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('[data-connection-label]');
    return label?.textContent?.includes('Repaired phone') ?? false;
  }, undefined, {timeout: 5000});
  const recovered = await readUi();
  const recoveredCommands = await commands();
  check(recovered.connect?.disabled === false, `${viewport}: second explicit repair can recover after cancel`);
  check(countOp(recoveredCommands, 'repair_connection_import') === 2, `${viewport}: cancel recovery uses a second explicit user action`);
  await assertNoForbiddenCommands(recoveredCommands, `${viewport}: repair cancel recovery`);
  cases[`${viewport}-unavailable-repair-cancel`] = {beforeCancel, cancelled, recovered, commands: recoveredCommands};
  await capture(viewport, 'unavailable-repair-cancel');
}

async function errorRecoveryCase(viewport) {
  await chooseScenario('scenario-error-legacy');
  const before = await readUi();
  check(String(before.error).includes('CONNECTION_SETUP_FAILED'), `${viewport}: legacy setup error remains visible`);
  check(before.setup?.disabled === false && before.import?.disabled === false, `${viewport}: legacy setup error keeps ordinary recovery reachable`);
  await safeClick('#connection-import', `${viewport} recover legacy setup error`);
  await page.waitForFunction(() => {
    const title = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('.status-title');
    return title?.textContent?.includes('已断开') ?? false;
  }, undefined, {timeout: 5000});
  const after = await readUi();
  const observedCommands = await commands();
  check(after.connect?.disabled === false, `${viewport}: recovered error consumes native configured receipt`);
  check(countOp(observedCommands, 'import_connection') === 1, `${viewport}: legacy error recovery sends one ordinary import`);
  await assertNoForbiddenCommands(observedCommands, `${viewport}: legacy error recovery`);
  cases[`${viewport}-error-recovery`] = {before, after, commands: observedCommands};
  await capture(viewport, 'error-recovery');
}

async function busyCase(viewport) {
  for (const scenario of ['opening', 'importing']) {
    await chooseScenario(`scenario-${scenario}`);
    const observed = await readUi();
    const observedCommands = await commands();
    check(observed.statusTitle === (scenario === 'opening' ? '正在打开连接设置' : '正在导入连接信息'), `${viewport}: ${scenario} status is visible`);
    check(observed.setup === null && observed.import === null, `${viewport}: ${scenario} renders no repeat setup action`);
    check(observed.connect?.disabled === true, `${viewport}: ${scenario} refuses connect`);
    check(setupOps(observedCommands).length === 0, `${viewport}: ${scenario} emits no setup intent`);
    await assertNoForbiddenCommands(observedCommands, `${viewport}: ${scenario}`);
    cases[`${viewport}-${scenario}`] = {observed, commands: observedCommands};
    await capture(viewport, scenario);
  }
}

async function capabilityCase(viewport) {
  await chooseScenario('scenario-capability-missing-setup');
  const missing = await readUi();
  const missingCommands = await commands();
  check(missing.setup?.disabled === true && missing.import?.disabled === true, `${viewport}: missing setup capability refuses setup/import`);
  check(missing.retry === null && missing.repair === null, `${viewport}: missing setup capability refuses retry/repair`);
  check(missing.connect?.disabled === true, `${viewport}: missing setup capability refuses connect`);
  check(String(missing.capabilityMessage).includes('未提供连接设置能力或投影'), `${viewport}: missing setup capability explains refusal`);
  check(setupOps(missingCommands).length === 0, `${viewport}: missing setup capability emits no setup intent`);
  await assertNoForbiddenCommands(missingCommands, `${viewport}: missing setup capability`);
  cases[`${viewport}-capability-missing`] = {observed: missing, commands: missingCommands};
  await capture(viewport, 'capability-missing');

  await chooseScenario('scenario-configured');
  await safeClick('#revoke-capabilities', `${viewport} revoke setup capability`);
  await page.waitForFunction(() => {
    const message = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('.capability-unavailable');
    return message?.textContent?.includes('未提供连接设置能力或投影') ?? false;
  }, undefined, {timeout: 5000});
  const falseCapability = await readUi();
  const falseCommands = await commands();
  check(falseCapability.setup?.disabled === true && falseCapability.import?.disabled === true, `${viewport}: false setup capability refuses setup/import`);
  check(falseCapability.retry === null && falseCapability.repair === null, `${viewport}: false setup capability refuses retry/repair`);
  check(falseCapability.connect?.disabled === true, `${viewport}: false setup capability refuses connect`);
  check(setupOps(falseCommands).length === 0, `${viewport}: false setup capability emits no setup intent`);
  await assertNoForbiddenCommands(falseCommands, `${viewport}: false setup capability`);
  cases[`${viewport}-capability-false`] = {observed: falseCapability, commands: falseCommands};
  await capture(viewport, 'capability-false');
}

async function publishedConnectionCase(viewport) {
  await chooseScenario('scenario-connected-unresolved');
  const unresolved = await readUi();
  const unresolvedCommands = await commands();
  check(unresolved.viewerReady === 'true' && unresolved.visibleContent === 'native-video', `${viewport}: unresolved preserves existing video`);
  check(unresolved.statusTitle === '连接配置提交状态未确认', `${viewport}: unresolved remains visible while connected`);
  check(unresolved.disconnect?.disabled === false, `${viewport}: unresolved keeps disconnect available`);
  check(unresolved.connect?.disabled === true, `${viewport}: unresolved refuses NEW connect while connected`);
  check(countOp(unresolvedCommands, 'connect') === 0, `${viewport}: unresolved connected state does not replay connect`);
  await assertNoForbiddenCommands(unresolvedCommands, `${viewport}: connected unresolved`);
  cases[`${viewport}-connected-unresolved`] = {observed: unresolved, commands: unresolvedCommands};
  await capture(viewport, 'connected-unresolved');

  await safeClick('#disconnect', `${viewport} disconnect unresolved connection`);
  await page.waitForFunction(() => {
    const header = document.querySelector('.consumer-header');
    return header?.textContent?.includes('scenario=unresolved-still') ?? false;
  }, undefined, {timeout: 5000});
  const disconnected = await readUi();
  const disconnectedCommands = await commands();
  check(disconnected.viewerReady === 'false', `${viewport}: disconnect removes existing video without resolving setup`);
  check(disconnected.statusTitle === '连接配置提交状态未确认', `${viewport}: disconnect preserves unresolved setup state`);
  check(countOp(disconnectedCommands, 'disconnect') === 1, `${viewport}: disconnect sends exactly one typed intent`);
  await assertNoForbiddenCommands(disconnectedCommands, `${viewport}: disconnected unresolved`);
  cases[`${viewport}-disconnected-unresolved`] = {observed: disconnected, commands: disconnectedCommands};

  await chooseScenario('scenario-connected-unavailable');
  const unavailable = await readUi();
  const unavailableCommands = await commands();
  check(unavailable.viewerReady === 'true' && unavailable.visibleContent === 'native-video', `${viewport}: unavailable preserves existing video`);
  check(unavailable.repair?.disabled === false, `${viewport}: unavailable keeps explicit repair available while connected`);
  check(countOp(unavailableCommands, 'repair_connection_import') === 0, `${viewport}: unavailable connected state does not replay repair`);
  await assertNoForbiddenCommands(unavailableCommands, `${viewport}: connected unavailable`);
  cases[`${viewport}-connected-unavailable`] = {observed: unavailable, commands: unavailableCommands};
  await capture(viewport, 'connected-unavailable');
}

async function longLabelCase(viewport) {
  await chooseScenario('scenario-configured-long-label');
  const observed = await readUi();
  const observedCommands = await commands();
  check(observed.label?.includes('PhoneLabel'), `${viewport}: long valid label is visible`);
  check(observed.label?.replace(/^连接：/, '').length === 64, `${viewport}: long label retains all 64 valid characters`);
  check(observed.horizontalOverflow !== null && observed.horizontalOverflow <= 1, `${viewport}: long label does not create horizontal overflow`);
  check(observed.drawerRect !== null && observed.drawerRect.right <= observed.viewport.width + 0.51, `${viewport}: long label keeps drawer inside viewport`);
  check(observed.sensitiveInputs === 0, `${viewport}: setup UI contains no password, textarea, or contenteditable field`);
  check(JSON.stringify(observed.inputs) === JSON.stringify([{id: 'address', type: 'url'}]), `${viewport}: only the address input remains`);
  await assertNoForbiddenCommands(observedCommands, `${viewport}: long label`);
  cases[`${viewport}-long-label`] = {observed, commands: observedCommands};
  await capture(viewport, 'long-label');
}

async function drawerGeometryCase(viewport) {
  await chooseScenario('scenario-connected-video');
  const opened = await readUi();
  check(opened.drawerOpen === 'true' && opened.presentation === 'overlay', `${viewport}: drawer opens from current native overlay receipt`);
  check(opened.safeTop === `${expectedSafeInset}px` && opened.safeBottom === `${expectedSafeInset}px`, `${viewport}: open drawer uses native safe insets`);
  check(near(opened.drawerRect?.top, expectedSafeInset) && near(opened.drawerRect?.bottom, opened.viewport.height - expectedSafeInset), `${viewport}: open drawer stays inside native safe bounds`);
  await page.press('#drawer-close', 'Escape');
  await waitForDrawer(false);
  const closed = await readUi();
  check(closed.drawerVisibility === 'hidden', `${viewport}: closed drawer does not obscure page content`);
  check(closed.viewerReady === 'true' && closed.visibleContent === 'native-video', `${viewport}: page content remains visible after drawer close`);
  await safeClick('#drawer-toggle', `${viewport} reopen drawer`);
  await waitForDrawer(true);
  const reopened = await readUi();
  check(reopened.safeTop === `${expectedSafeInset}px` && reopened.safeBottom === `${expectedSafeInset}px`, `${viewport}: reopened drawer preserves native safe insets`);
  check(near(reopened.drawerRect?.top, expectedSafeInset) && near(reopened.drawerRect?.bottom, reopened.viewport.height - expectedSafeInset), `${viewport}: reopened drawer preserves native safe bounds`);
  const observedCommands = await commands();
  check(observedCommands.filter(item => item.op === 'set_presentation').length === 3, `${viewport}: close/reopen uses explicit presentation receipts only`);
  await assertNoForbiddenCommands(observedCommands, `${viewport}: drawer geometry`);
  cases[`${viewport}-drawer-geometry`] = {opened, closed, reopened, commands: observedCommands};
  await capture(viewport, 'drawer-reopened');
}

async function desktopCases() {
  await requiredImportCase('desktop');
  await configuredReplaceCase('desktop');
  await unresolvedCase('desktop');
  await unavailableCase('desktop');
  await errorRecoveryCase('desktop');
  await busyCase('desktop');
  await capabilityCase('desktop');
  await publishedConnectionCase('desktop');
  await longLabelCase('desktop');
  await drawerGeometryCase('desktop');
}

async function phoneCases() {
  await requiredImportCase('phone');
  await unresolvedCase('phone');
  await unavailableCase('phone');
  await capabilityCase('phone');
  await publishedConnectionCase('phone');
  await longLabelCase('phone');
  await drawerGeometryCase('phone');
}

try {
  await setViewport(1280, 800, false);
  await navigate();
  await desktopCases();

  await setViewport(390, 844, true);
  await navigate();
  await phoneCases();
} catch (error) {
  failure = String(error?.stack ?? error);
} finally {
  if (finishTask) {
    try {
      finishReceipt = await task.finish({keep: []});
    } catch (error) {
      failure = failure ? `${failure}\n${String(error?.stack ?? error)}` : String(error?.stack ?? error);
    }
  }
}

const raw = {
  layer: 'UI_MODULE',
  spaceId: task.spaceId,
  url: baseUrl,
  sourceCommit,
  sourceTree,
  bundleDir,
  bundleHashes: await bundleHashes(),
  expectedSafeInset,
  finishReceipt,
  screenshots,
  cases,
  assertions,
  failure,
};
await mkdir(`${root}/raw`, {recursive: true});
await writeFile(`${root}/raw/connection-recovery-e2e.json`, JSON.stringify(raw, null, 2));
console.log(JSON.stringify(raw, null, 2));

if (failure || assertions.some(item => !item.ok)) process.exitCode = 1;

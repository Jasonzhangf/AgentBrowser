const { writeFile, mkdir, readFile } = await import('node:fs/promises');
const { createHash } = await import('node:crypto');
const { join } = await import('node:path');

const params = JSON.parse(await readFile(
  '/Volumes/Intel/playground/agentbrowser/m1-ui-receipt-geometry-r3-20261004/docs/runs/m1-ui-receipt-geometry-r3-20261004/blackbox/params.json',
  'utf8',
));
const spaceId = Number(params.spaceId);
const baseUrl = params.baseUrl;
const phase = params.phase ?? 'after';
const bundleDir = params.bundleDir ?? null;
const sourceCommit = params.sourceCommit ?? null;
const sourceTree = params.sourceTree ?? null;
const finishTask = params.finishTask !== false;
if (!Number.isInteger(spaceId) || spaceId <= 0) throw new Error('EGO_SPACE_ID_REQUIRED');
if (!baseUrl) throw new Error('E2E_URL_REQUIRED');

const task = await taskSpace(spaceId);
const page = task.page('p1');
const root = '/Volumes/Intel/playground/agentbrowser/m1-ui-receipt-geometry-r3-20261004/docs/runs/m1-ui-receipt-geometry-r3-20261004';
const expectedSafeInset = 24;
const expectedOverlayRect = {x: 0, y: 0, width: 390, height: 844};
const expectedBrowsingRect = {x: 0, y: 24, width: 390, height: 56};

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
  await page.waitForSelector('#scenario-connected-no-frame', {state: 'visible'});
  await page.waitForFunction(() => {
    const frame = document.querySelector('#consumer-frame');
    return Boolean(frame?.contentDocument?.querySelector('main'));
  }, undefined, {timeout: 5000});
}

async function chooseScenario(id) {
  const scenarioName = id.replace(/^scenario-/, '');
  await page.click(`#${id}`, {label: `select ${id}`});
  await page.waitForFunction((scenario) => {
    const header = document.querySelector('.consumer-header');
    return header?.textContent?.includes(`scenario=${scenario}`) ?? false;
  }, scenarioName, {timeout: 5000});
  await page.waitForTimeout(350);
}

async function ui() {
  return page.evaluate(() => {
    const frame = document.querySelector('#consumer-frame');
    const doc = frame?.contentDocument;
    const win = frame?.contentWindow;
    const main = doc?.querySelector('main');
    const viewer = doc?.querySelector('.viewer');
    const surface = doc?.querySelector('.viewer-surface');
    const drawer = doc?.querySelector('#browser-drawer');
    const error = doc?.querySelector('.chrome-error');
    const drawerRect = drawer?.getBoundingClientRect();
    return {
      viewport: win ? {width: win.innerWidth, height: win.innerHeight} : null,
      ready: viewer?.getAttribute('data-ready') ?? null,
      visibleContent: viewer?.getAttribute('data-visible-content') ?? null,
      surfaceVisibility: surface ? getComputedStyle(surface).visibility : null,
      surfaceOpacity: surface ? getComputedStyle(surface).opacity : null,
      emptyText: doc?.querySelector('.viewer-empty')?.textContent?.replace(/\s+/g, ' ').trim() ?? null,
      drawerOpen: main?.getAttribute('data-drawer-open') ?? null,
      presentation: main?.getAttribute('data-presentation') ?? null,
      drawerTop: drawer ? getComputedStyle(drawer).top : null,
      drawerRight: drawer ? getComputedStyle(drawer).right : null,
      drawerBottom: drawer ? getComputedStyle(drawer).bottom : null,
      safeTop: main ? getComputedStyle(main).getPropertyValue('--safe-top').trim() : null,
      safeRight: main ? getComputedStyle(main).getPropertyValue('--safe-right').trim() : null,
      safeBottom: main ? getComputedStyle(main).getPropertyValue('--safe-bottom').trim() : null,
      safeLeft: main ? getComputedStyle(main).getPropertyValue('--safe-left').trim() : null,
      drawerRect: drawerRect ? {
        top: drawerRect.top,
        right: drawerRect.right,
        bottom: drawerRect.bottom,
        left: drawerRect.left,
        width: drawerRect.width,
        height: drawerRect.height,
      } : null,
      error: error?.textContent?.replace(/\s+/g, ' ').trim() ?? null,
      pluginText: doc?.querySelector('#plugin')?.textContent ?? null,
      requestCount: win?.__w4Stats?.total ?? null,
      trace: win?.__w4Stats?.events ?? [],
    };
  });
}

async function commands() {
  return page.evaluate(() => JSON.parse(document.querySelector('#commands')?.textContent ?? '[]'));
}

async function startGeometryObserver() {
  const installed = await page.evaluate(() => {
    const frame = document.querySelector('#consumer-frame');
    const doc = frame?.contentDocument;
    const main = doc?.querySelector('main');
    if (!frame || !doc || !main) return false;
    const samples = [];
    let running = true;
    const sample = () => {
      const drawer = doc.querySelector('#browser-drawer');
      const rect = drawer?.getBoundingClientRect();
      const win = frame.contentWindow;
      samples.push({
        drawerOpen: main.getAttribute('data-drawer-open'),
        presentation: main.getAttribute('data-presentation'),
        safeTop: getComputedStyle(main).getPropertyValue('--safe-top').trim(),
        safeBottom: getComputedStyle(main).getPropertyValue('--safe-bottom').trim(),
        drawerTop: drawer ? getComputedStyle(drawer).top : null,
        drawerBottom: drawer ? getComputedStyle(drawer).bottom : null,
        drawerRect: rect ? {
          top: rect.top,
          right: rect.right,
          bottom: rect.bottom,
          left: rect.left,
          width: rect.width,
          height: rect.height,
        } : null,
        viewport: win ? {width: win.innerWidth, height: win.innerHeight} : null,
        error: doc.querySelector('.chrome-error')?.textContent?.replace(/\s+/g, ' ').trim() ?? null,
      });
    };
    const tick = () => {
      sample();
      if (running) window.requestAnimationFrame(tick);
    };
    window.__r3GeometryObserver = {
      samples,
      stop: () => {
        running = false;
        sample();
      },
    };
    sample();
    window.requestAnimationFrame(tick);
    return true;
  });
  if (!installed) throw new Error('GEOMETRY_OBSERVER_NOT_INSTALLED');
}

async function stopGeometryObserver() {
  return page.evaluate(() => {
    const observer = window.__r3GeometryObserver;
    if (!observer) return [];
    observer.stop();
    const samples = observer.samples.slice();
    delete window.__r3GeometryObserver;
    return samples;
  });
}

async function waitFor(predicate, message, timeout = 5000) {
  await page.waitForFunction(predicate, undefined, {timeout});
}

const assertions = [];
const screenshots = {};
const cases = {};
let finishReceipt = null;
let failure = null;

function check(condition, message, evidence) {
  assertions.push({ok: Boolean(condition), message, evidence});
}

function presentationCommands(items) {
  return items.filter(item => item.op === 'set_presentation');
}

function sameJson(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function hasTrace(trace, expected) {
  return trace.some(item => Object.entries(expected).every(([key, value]) => sameJson(item[key], value)));
}

// Layout uses CSS px with fractional iframe heights, so measured bounds can land
// within half a pixel of the integer safe boundary.
function near(value, expected, tolerance = 0.51) {
  return typeof value === 'number' && Math.abs(value - expected) <= tolerance;
}

function hasOpenGeometry(sample, expectedInset) {
  return sample.drawerOpen === 'true'
    && sample.safeTop === `${expectedInset}px`
    && sample.safeBottom === `${expectedInset}px`
    && sample.drawerTop === `${expectedInset}px`
    && sample.drawerBottom === `${expectedInset}px`
    && near(sample.drawerRect?.top, expectedInset)
    && near(sample.drawerRect?.bottom, sample.viewport?.height - expectedInset);
}

async function capture(name, viewport) {
  const path = `${root}/screenshots/${phase}-${viewport}-${name}.png`;
  screenshots[`${phase}-${viewport}-${name}`] = path;
  await page.screenshot({path});
  return path;
}

async function openStatusFailureCase(viewport) {
  await chooseScenario('scenario-presentation-open-status-error');
  await waitFor(() => {
    const main = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('main');
    return main?.getAttribute('data-drawer-open') === 'false'
      && main?.getAttribute('data-presentation') === 'browsing';
  }, `${viewport} open-status-error scenario is browsing`);

  await startGeometryObserver();
  await page.click('#drawer-toggle', {label: `${viewport} open drawer then fail status`});
  await waitFor(() => {
    const error = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('.chrome-error');
    return error?.textContent?.includes('STATUS_FAILED') ?? false;
  }, `${viewport} open status failure is explicit`);

  const observed = await ui();
  const geometrySamples = await stopGeometryObserver();
  const observedCommands = await commands();
  const screenshot = await capture('open-status-error', viewport);
  cases[`${viewport}-open-status-error`] = {observed, commands: observedCommands, geometrySamples, screenshot};

  check(observed.drawerOpen === 'true', `${viewport}: applied overlay remains open when status refresh fails`);
  check(observed.presentation === 'browsing', `${viewport}: old browsing projection is retained until current`);
  check(String(observed.error).includes('STATUS_FAILED'), `${viewport}: open status failure is visible`);
  check(presentationCommands(observedCommands).length === 1, `${viewport}: open emits one presentation command`);
  check(
    presentationCommands(observedCommands)[0]?.state === 'overlay',
    `${viewport}: open command requests overlay`,
  );
  check(
    hasTrace(observed.trace, {
      op: 'set_presentation',
      state: 'overlay',
      status: 'applied',
      revision: 8,
      webRect: expectedOverlayRect,
    }),
    `${viewport}: applied overlay receipt carries native webRect and revision`,
  );
  check(
    hasTrace(observed.trace, {op: 'status', status: 'error', error: 'STATUS_FAILED'}),
    `${viewport}: failed status request is observable`,
  );
  check(
    observedCommands.every(item => !/viewport|declare|resize/i.test(item.op ?? '')),
    `${viewport}: open/status failure emits no viewport command`,
  );
  check(observed.safeTop === '24px', `${viewport}: applied overlay safe-top is native 24px`);
  check(observed.safeBottom === '24px', `${viewport}: applied overlay safe-bottom is native 24px`);
  check(observed.drawerTop === '24px', `${viewport}: applied overlay drawer top is native 24px`);
  check(observed.drawerBottom === '24px', `${viewport}: applied overlay drawer bottom is native 24px`);
  check(near(observed.drawerRect?.top, 24), `${viewport}: applied overlay measured drawer top is 24px`);
  check(
    near(observed.drawerRect?.bottom, observed.viewport?.height - 24),
    `${viewport}: applied overlay measured drawer bottom is viewport height minus 24px`,
  );
  const openSamples = geometrySamples.filter(sample => sample.drawerOpen === 'true');
  check(openSamples.length > 0, `${viewport}: applied overlay produced observable geometry samples`);
  check(
    openSamples.every(sample => hasOpenGeometry(sample, expectedSafeInset)),
    `${viewport}: no drawer-open sample flashes zero safe geometry before status failure`,
  );

  await page.click('#presentation-status-recover', {label: `${viewport} recover open status projection`});
  await waitFor(() => {
    const main = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('main');
    return main?.getAttribute('data-drawer-open') === 'true'
      && main?.getAttribute('data-presentation') === 'overlay';
  }, `${viewport} current overlay projection supersedes open guard`);
  const recovered = await ui();
  cases[`${viewport}-open-status-error-recovered`] = {observed: recovered};
  check(recovered.drawerOpen === 'true', `${viewport}: recovered overlay keeps drawer open`);
  check(recovered.presentation === 'overlay', `${viewport}: recovered overlay projection is current`);
  check(recovered.safeTop === '24px' && recovered.safeBottom === '24px', `${viewport}: recovered overlay keeps native safe insets`);
  check(
    near(recovered.drawerRect?.top, 24) && near(recovered.drawerRect?.bottom, recovered.viewport?.height - 24),
    `${viewport}: recovered overlay measured drawer bounds match native safe boundary`,
  );
  await capture('open-status-error-recovered', viewport);
}

async function closeStatusFailureCase(viewport) {
  await chooseScenario('scenario-presentation-close-status-error');
  await waitFor(() => {
    const main = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('main');
    return main?.getAttribute('data-drawer-open') === 'true'
      && main?.getAttribute('data-presentation') === 'overlay';
  }, `${viewport} close-status-error scenario starts overlay`);

  await startGeometryObserver();
  await page.press('#drawer-close', 'Escape');
  await waitFor(() => {
    const error = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('.chrome-error');
    return error?.textContent?.includes('STATUS_FAILED') ?? false;
  }, `${viewport} close status failure is explicit`);

  const observed = await ui();
  const geometrySamples = await stopGeometryObserver();
  const observedCommands = await commands();
  const screenshot = await capture('close-status-error', viewport);
  cases[`${viewport}-close-status-error`] = {observed, commands: observedCommands, geometrySamples, screenshot};

  check(observed.drawerOpen === 'false', `${viewport}: applied browsing remains closed when status refresh fails`);
  check(observed.presentation === 'overlay', `${viewport}: old overlay projection is retained until current`);
  check(String(observed.error).includes('STATUS_FAILED'), `${viewport}: close status failure is visible`);
  check(presentationCommands(observedCommands).length === 1, `${viewport}: close emits one presentation command`);
  check(
    presentationCommands(observedCommands)[0]?.state === 'browsing',
    `${viewport}: close command requests browsing`,
  );
  check(
    hasTrace(observed.trace, {
      op: 'set_presentation',
      state: 'browsing',
      status: 'applied',
      revision: 9,
      webRect: expectedBrowsingRect,
    }),
    `${viewport}: applied browsing receipt carries native webRect and revision`,
  );
  check(
    hasTrace(observed.trace, {op: 'status', status: 'error', error: 'STATUS_FAILED'}),
    `${viewport}: failed close status request is observable`,
  );
  check(
    observedCommands.every(item => !/viewport|declare|resize/i.test(item.op ?? '')),
    `${viewport}: close/status failure emits no viewport command`,
  );
  check(
    geometrySamples.at(-1)?.drawerOpen === 'false',
    `${viewport}: close remains closed in the final observed geometry sample`,
  );

  await page.click('#presentation-status-recover', {label: `${viewport} recover close status projection`});
  await waitFor(() => {
    const main = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('main');
    return main?.getAttribute('data-drawer-open') === 'false'
      && main?.getAttribute('data-presentation') === 'browsing';
  }, `${viewport} current browsing projection supersedes close guard`);
  const recovered = await ui();
  cases[`${viewport}-close-status-error-recovered`] = {observed: recovered};
  check(recovered.drawerOpen === 'false', `${viewport}: recovered browsing keeps drawer closed`);
  check(recovered.presentation === 'browsing', `${viewport}: recovered browsing projection is current`);
  await capture('close-status-error-recovered', viewport);
}

async function regressionCases() {
  await chooseScenario('scenario-connected-no-frame');
  const noFrame = await ui();
  check(noFrame.ready === 'false', 'connected-no-frame keeps ready=false');
  check(noFrame.visibleContent === 'webview', 'connected-no-frame is webview visible content');
  check(noFrame.surfaceVisibility === 'visible', 'connected-no-frame keeps empty surface visible');
  check(String(noFrame.emptyText).includes('等待原生画面'), 'connected-no-frame shows waiting copy');
  await capture('connected-no-frame', 'desktop');

  await chooseScenario('scenario-connected-video');
  const video = await ui();
  check(video.ready === 'true', 'valid displayed frame sets native-video ready');
  check(video.visibleContent === 'native-video', 'valid frame projects native-video');
  check(video.surfaceVisibility === 'hidden' && video.surfaceOpacity === '0', 'native-video hides empty surface');
  await capture('connected-video', 'desktop');

  for (const scenario of ['scenario-waiting', 'scenario-operation-pending', 'scenario-control']) {
    await chooseScenario(scenario);
    const state = await ui();
    check(state.ready === 'true', `${scenario} keeps video visible`);
  }

  await chooseScenario('scenario-presentation-rejected');
  await page.click('#drawer-toggle', {label: 'request overlay rejection once'});
  await waitFor(() => {
    const error = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('.chrome-error');
    return error?.textContent?.includes('打开抽屉被拒绝') ?? false;
  }, 'rejection remains explicit');
  const rejected = await ui();
  check(rejected.drawerOpen === 'false', 'rejected overlay does not fake open drawer');
  check(String(rejected.error).includes('NO_AUTHORITY'), 'rejection reason is visible');
  await capture('presentation-rejected', 'desktop');

  await page.click('#drawer-toggle', {label: 'retry overlay after rejection'});
  await waitFor(() => {
    const main = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('main');
    return main?.getAttribute('data-drawer-open') === 'true'
      && main?.getAttribute('data-presentation') === 'overlay';
  }, 'retry applies overlay');
  const retried = await ui();
  check(retried.drawerOpen === 'true' && retried.presentation === 'overlay', 'retry opens drawer after applied receipt');
  const retryPresentation = presentationCommands(await commands());
  check(
    retryPresentation.length === 2
      && retryPresentation[0].state === 'overlay'
      && retryPresentation[1].state === 'overlay',
    'retry records rejected then applied overlay commands',
  );
  await page.click('#drawer-close', {label: 'close drawer after retry'});
  await waitFor(() => {
    const main = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('main');
    return main?.getAttribute('data-drawer-open') === 'false'
      && main?.getAttribute('data-presentation') === 'browsing';
  }, 'post-retry close applies browsing');
}

async function disposeCase() {
  await chooseScenario('scenario-connected-video');
  await page.click('#drawer-toggle', {label: 'open drawer before dispose'});
  await waitFor(() => {
    const main = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('main');
    return main?.getAttribute('data-drawer-open') === 'true'
      && main?.getAttribute('data-presentation') === 'overlay';
  }, 'drawer open before dispose');
  const beforeDisposeCommands = (await commands()).length;
  await page.click('#plugin', {label: 'unmount UI plugin while overlay applied'});
  await page.waitForFunction(() => {
    const doc = document.querySelector('#consumer-frame')?.contentDocument;
    return doc?.querySelector('#plugin')?.textContent?.includes('重新加载') ?? false;
  }, undefined, {timeout: 5000});
  const requestsRightAfterDispose = (await ui()).requestCount;
  await page.waitForTimeout(800);
  const requestsAfterIdle = (await ui()).requestCount;
  const afterDisposeCommands = (await commands()).length;
  check(afterDisposeCommands === beforeDisposeCommands, 'dispose emits no new public commands');
  check(
    requestsRightAfterDispose !== null && requestsAfterIdle === requestsRightAfterDispose,
    'dispose stops the status-poll resource',
  );
  const disposed = await page.evaluate(() => {
    const doc = document.querySelector('#consumer-frame')?.contentDocument;
    const plugin = doc?.querySelector('#plugin');
    return {
      mainExists: Boolean(doc?.querySelector('main')),
      toggleText: plugin?.textContent ?? null,
      toggleMounted: plugin?.dataset?.mounted ?? null,
    };
  });
  check(disposed.mainExists === false, 'dispose unmounts the UI plugin');
  check(
    String(disposed.toggleText).includes('重新加载') && disposed.toggleMounted === 'false',
    'dispose releases once and returns to reload state',
  );
  await capture('dispose', 'desktop');
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

try {
  await setViewport(1280, 800, false);
  await navigate();
  await regressionCases();
  await openStatusFailureCase('desktop');
  await closeStatusFailureCase('desktop');

  await setViewport(390, 844, true);
  await navigate();
  await openStatusFailureCase('phone');
  await closeStatusFailureCase('phone');

  await setViewport(1280, 800, false);
  await disposeCase();
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
  phase,
  spaceId,
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
await writeFile(`${root}/raw/receipt-${phase}.json`, JSON.stringify(raw, null, 2));
console.log(JSON.stringify(raw, null, 2));

if (failure || assertions.some(item => !item.ok)) {
  process.exitCode = 1;
}

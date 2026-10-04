const { writeFile, mkdir } = await import('node:fs/promises');

const spaceId = Number(process.env.EGO_SPACE_ID);
const baseUrl = process.env.E2E_URL;
if (!Number.isInteger(spaceId) || spaceId <= 0) throw new Error('EGO_SPACE_ID_REQUIRED');
if (!baseUrl) throw new Error('E2E_URL_REQUIRED');

const task = await taskSpace(spaceId);
const page = task.page('p1');
const root = '/Volumes/Intel/playground/agentbrowser/m1-ui-presentation-layout-r1-20261004/docs/runs/m1-ui-presentation-layout-r1-20261004';

async function setViewport(width, height, mobile) {
  await page.cdp('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: mobile ? 2 : 1, mobile});
}

async function navigate() {
  await page.goto(baseUrl);
  await page.waitForSelector('#scenario-connected-no-frame', {state: 'visible'});
}

async function chooseScenario(id) {
  await page.click(`#${id}`, {label: `select ${id}`});
  await page.waitForTimeout(350);
}

async function ui() {
  return page.evaluate(() => {
    const frame = document.querySelector('#consumer-frame');
    const doc = frame?.contentDocument;
    const main = doc?.querySelector('main');
    const viewer = doc?.querySelector('.viewer');
    const surface = doc?.querySelector('.viewer-surface');
    const drawer = doc?.querySelector('#browser-drawer');
    const error = doc?.querySelector('.chrome-error');
    return {
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
      safeBottom: main ? getComputedStyle(main).getPropertyValue('--safe-bottom').trim() : null,
      error: error?.textContent?.replace(/\s+/g, ' ').trim() ?? null,
      pluginText: doc?.querySelector('#plugin')?.textContent ?? null,
    };
  });
}

async function commands() {
  return page.evaluate(() => JSON.parse(document.querySelector('#commands')?.textContent ?? '[]'));
}

async function nativeRequestCount() {
  return page.evaluate(() => {
    const win = document.querySelector('#consumer-frame')?.contentWindow;
    return win?.__w4Stats?.total ?? null;
  });
}

async function waitFor(predicate, message, timeout = 5000) {
  await page.waitForFunction(predicate, undefined, {timeout});
}

function assert(condition, message, evidence) {
  if (!condition) throw new Error(message);
  evidence.push(message);
}

const evidence = [];
const screenshots = {};
let finishReceipt = null;

try {
  await setViewport(1280, 800, false);
  await navigate();

  await chooseScenario('scenario-connected-no-frame');
  const noFrame = await ui();
  assert(noFrame.ready === 'false', 'connected-no-frame keeps ready=false', evidence);
  assert(noFrame.visibleContent === 'webview', 'connected-no-frame is webview visible content', evidence);
  assert(noFrame.surfaceVisibility === 'visible', 'connected-no-frame keeps empty surface visible', evidence);
  assert(String(noFrame.emptyText).includes('等待原生画面'), 'connected-no-frame shows waiting copy', evidence);
  screenshots.noFrame = await page.screenshot({path: `${root}/screenshots/desktop-connected-no-frame.png`});

  await chooseScenario('scenario-connected-video');
  const video = await ui();
  assert(video.ready === 'true', 'valid displayed frame sets native-video ready', evidence);
  assert(video.visibleContent === 'native-video', 'valid frame projects native-video', evidence);
  assert(video.surfaceVisibility === 'hidden' && video.surfaceOpacity === '0', 'native-video hides empty surface', evidence);
  screenshots.video = await page.screenshot({path: `${root}/screenshots/desktop-connected-video.png`});

  for (const scenario of ['scenario-waiting', 'scenario-operation-pending', 'scenario-control']) {
    await chooseScenario(scenario);
    const state = await ui();
    assert(state.ready === 'true', `${scenario} keeps video visible`, evidence);
  }

  await chooseScenario('scenario-connected-video');
  await page.click('#drawer-toggle', {label: 'open drawer with applied receipt'});
  await waitFor(() => {
    const main = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('main');
    return main?.getAttribute('data-drawer-open') === 'true' && main?.getAttribute('data-presentation') === 'overlay';
  }, 'drawer-open waits for overlay applied');
  const open = await ui();
  const openCommands = await commands();
  assert(open.drawerOpen === 'true' && open.presentation === 'overlay', 'drawer open reflects applied overlay', evidence);
  assert(open.safeTop === '24px' && open.drawerTop === '24px' && open.drawerBottom === '24px', 'drawer consumes native safe insets', evidence);
  const presentationCommands = openCommands.filter(c => c.op === 'set_presentation');
  assert(presentationCommands.length === 1 && presentationCommands[0].state === 'overlay', 'open emits one set_presentation overlay', evidence);
  assert(openCommands.every(c => !/viewport|declare|resize/i.test(c.op ?? '')), 'no viewport commands are emitted', evidence);
  screenshots.drawerOpen = await page.screenshot({path: `${root}/screenshots/desktop-drawer-open.png`});

  await page.press('#drawer-close', 'Escape');
  await waitFor(() => {
    const main = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('main');
    return main?.getAttribute('data-drawer-open') === 'false' && main?.getAttribute('data-presentation') === 'browsing';
  }, 'escape close waits for browsing applied');
  const escapeClosed = await commands();
  const escapePresentation = escapeClosed.filter(c => c.op === 'set_presentation');
  assert(escapePresentation.length === 2 && escapePresentation[1].state === 'browsing', 'escape close emits one set_presentation browsing', evidence);

  await chooseScenario('scenario-presentation-rejected');
  await page.click('#drawer-toggle', {label: 'request overlay rejection once'});
  await waitFor(() => {
    const error = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('.chrome-error');
    return error?.textContent?.includes('打开抽屉被拒绝');
  }, 'rejection remains explicit');
  const rejected = await ui();
  assert(rejected.drawerOpen === 'false', 'rejected overlay does not fake open drawer', evidence);
  assert(String(rejected.error).includes('NO_AUTHORITY'), 'rejection reason is visible', evidence);
  screenshots.rejected = await page.screenshot({path: `${root}/screenshots/desktop-presentation-rejected.png`});

  await page.click('#drawer-toggle', {label: 'retry overlay after rejection'});
  await waitFor(() => {
    const main = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('main');
    return main?.getAttribute('data-drawer-open') === 'true' && main?.getAttribute('data-presentation') === 'overlay';
  }, 'retry applies overlay');
  const retried = await ui();
  assert(retried.drawerOpen === 'true' && retried.presentation === 'overlay', 'retry opens drawer after applied receipt', evidence);
  const retryCommands = await commands();
  const retryPresentation = retryCommands.filter(c => c.op === 'set_presentation');
  assert(retryPresentation.length === 2 && retryPresentation[0].state === 'overlay' && retryPresentation[1].state === 'overlay', 'retry records rejected then applied overlay commands', evidence);
  await page.click('#drawer-close', {label: 'close drawer after retry'});
  await waitFor(() => {
    const main = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('main');
    return main?.getAttribute('data-drawer-open') === 'false' && main?.getAttribute('data-presentation') === 'browsing';
  }, 'post-retry close applies browsing');

  await chooseScenario('scenario-connected-video');
  await page.click('#drawer-toggle', {label: 'open drawer before dispose'});
  await waitFor(() => {
    const main = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('main');
    return main?.getAttribute('data-drawer-open') === 'true' && main?.getAttribute('data-presentation') === 'overlay';
  }, 'drawer open before dispose');
  const beforeDisposeCommands = (await commands()).length;
  await page.click('#plugin', {label: 'unmount UI plugin while overlay applied'});
  await page.waitForFunction(() => {
    const doc = document.querySelector('#consumer-frame')?.contentDocument;
    return doc?.querySelector('#plugin')?.textContent?.includes('重新加载');
  }, undefined, {timeout: 5000});
  const requestsRightAfterDispose = await nativeRequestCount();
  await page.waitForTimeout(800);
  const requestsAfterIdle = await nativeRequestCount();
  const afterDisposeCommands = (await commands()).length;
  assert(afterDisposeCommands === beforeDisposeCommands, 'dispose emits no new public commands', evidence);
  assert(
    requestsRightAfterDispose !== null && requestsAfterIdle === requestsRightAfterDispose,
    'dispose stops the status-poll resource (native request count frozen)',
    evidence,
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
  assert(disposed.mainExists === false, 'dispose unmounts the UI plugin', evidence);
  assert(String(disposed.toggleText).includes('重新加载') && disposed.toggleMounted === 'false', 'dispose releases once and returns to reload state', evidence);
  screenshots.dispose = await page.screenshot({path: `${root}/screenshots/desktop-dispose.png`});

  await setViewport(390, 844, true);
  await navigate();
  await chooseScenario('scenario-connected-video');
  await page.click('#drawer-toggle', {label: 'open drawer on phone viewport'});
  await waitFor(() => {
    const main = document.querySelector('#consumer-frame')?.contentDocument?.querySelector('main');
    return main?.getAttribute('data-drawer-open') === 'true' && main?.getAttribute('data-presentation') === 'overlay';
  }, 'phone drawer opens with applied receipt');
  const phone = await ui();
  assert(phone.drawerOpen === 'true' && phone.presentation === 'overlay', 'phone drawer reflects applied overlay', evidence);
  assert(phone.safeTop === '24px' && phone.drawerTop === '24px' && phone.drawerBottom === '24px', 'phone drawer consumes native safe insets', evidence);
  screenshots.phone = await page.screenshot({path: `${root}/screenshots/phone-drawer-open.png`});

  finishReceipt = await task.finish({keep: []});

  const raw = {
    spaceId,
    url: baseUrl,
    finishReceipt,
    screenshots,
    evidence,
  };
  await mkdir(`${root}/raw`, {recursive: true});
  await writeFile(`${root}/raw/e2e.json`, JSON.stringify(raw, null, 2));
  console.log(JSON.stringify(raw, null, 2));
} catch (error) {
  console.error(String(error?.stack ?? error));
  throw error;
}

// Auxiliary source-contract checks for the browser chrome boundary.
// These are white-box guards for the refactor (no duplicate page editing, typed
// commands, drawer layering, capability/quality algebra). They do NOT replace
// the public-consumer black-box evidence in docs/runs/m1-20261002/w4-delivery.md.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const main = await readFile(new URL('./main.tsx', import.meta.url), 'utf8');
const css = await readFile(new URL('./probe.css', import.meta.url), 'utf8');
const probe = await readFile(new URL('../client-domain/probe.ts', import.meta.url), 'utf8');
const consumerFrame = await readFile(new URL('./consumer-frame.ts', import.meta.url), 'utf8');

test('no duplicate in-UI page editing entry survives', () => {
  for (const token of ['input-text', 'send-text', 'TextWatcher', 'onBeforeInput', 'contentEditable', '<textarea']) {
    assert.equal(main.includes(token), false, `chrome must not carry page-editing token: ${token}`);
  }
  const inputs = main.match(/<input\b/g) ?? [];
  assert.equal(inputs.length, 1, 'only the local address bar input remains');
  assert.match(main, /id="address"/);
  assert.match(main, /onCompositionStart=\{startAddressComposition\}/);
  assert.match(main, /onCompositionEnd=\{endAddressComposition\}/);
});

test('address bar submits a normalized typed navigate intent on Enter', () => {
  assert.match(main, /export function normalizeAddress/);
  assert.match(main, /standardSchemes/);
  assert.match(main, /opaquePayload\.startsWith\('\/\/'\)/);
  assert.match(main, /scheme\[1\]\.includes\('\+'\)/);
  assert.match(main, /!\/\^\\d\+\(\?:\[\/\?#\]\|\$\)\/\.test\(opaquePayload\)/);
  assert.match(main, /onKeyDown=\{submitAddressOnEnter\}/);
  assert.match(main, /request\(\{op: 'navigate', epoch: current\.epoch!, url: target\}\)/);
  assert.match(main, /requestSubmit\(\)/);
});

test('command rejection and status polling keep separate result owners', () => {
  assert.match(main, /const \[commandProblem, setCommandProblem\]/);
  assert.match(main, /const \[statusProblem, setStatusProblem\]/);
  assert.match(main, /setStatusProblem\(''\)/);
  assert.match(main, /setCommandProblem\(''\)/);
  assert.match(main, /snapshot\.error \|\| commandProblem \|\| statusProblem \|\| setup\.error/);
});

test('tab mutations require a current epoch and re-read control context before sending', () => {
  assert.match(main, /function canManageTabsSnapshot/);
  assert.match(main, /snapshot\.epoch !== undefined/);
  assert.match(main, /function requestTabCommand/);
  assert.match(main, /ctx\.probe\.request\(\{op: 'status'\}\)/);
  const requestTabCommand = main.slice(main.indexOf('function requestTabCommand'), main.indexOf('function requestQuality'));
  assert.doesNotMatch(requestTabCommand, /setCommandProblem\(''\)/);
  assert.match(main, /requestTabCommand\(epoch => \(\{op: 'new_tab', epoch\}\)\)/);
  assert.match(main, /requestTabCommand\(epoch => \(\{op: 'select_tab', epoch, tabId: tab\.id\}\)\)/);
  assert.match(main, /requestTabCommand\(epoch => \(\{op: 'close_tab', epoch, tabId: tab\.id\}\)\)/);
});

test('send-time epoch removal keeps control and capability context valid', () => {
  const start = consumerFrame.indexOf("state.scenario === 'tabs-context-revoke-on-send'");
  const end = consumerFrame.indexOf("state.scenario === 'tabs-control-revoke-on-send'", start);
  assert.notEqual(start, -1, 'epoch-removal status branch must exist');
  assert.notEqual(end, -1, 'control-revocation status branch must follow it');
  const branch = consumerFrame.slice(start, end);
  assert.match(branch, /dropEpochOnNextStatus/);
  assert.match(branch, /const \{epoch: _epoch, \.\.\.withoutEpoch\} = connectedBase\(\)/);
  assert.match(branch, /return \{\.\.\.withoutEpoch, controlMode: 'control'\}/);
  assert.doesNotMatch(branch, /controlMode: 'observe'/);
  assert.match(consumerFrame, /event\.target\.closest\('#new-tab'\)\) state\.dropEpochOnNextStatus = true/);
});

test('address IME confirmation Enter does not submit and explicit Enter submits once', () => {
  assert.match(main, /event\.nativeEvent\.isComposing/);
  assert.match(main, /event\.nativeEvent\.keyCode === 229/);
  assert.match(main, /addressCompositionJustEndedRef\.current/);
  assert.match(main, /event\.currentTarget\.form\?\.requestSubmit\(\)/);
});

test('the viewer is declared as the native video/IME input surface', () => {
  assert.match(main, /data-input-path="native-video-surface"/);
  assert.match(main, /aria-label="原生视频区域"/);
});

test('drawer is layered over the page but not persistent when closed', () => {
  assert.match(css, /\.drawer \{[\s\S]*?position: fixed;/);
  assert.match(css, /\.drawer-backdrop \{[\s\S]*?position: fixed;/);
  assert.match(css, /\.drawer \{[\s\S]*?visibility: hidden;/);
  assert.match(css, /\[data-drawer-open="true"\] \.drawer \{[\s\S]*?visibility: visible;/);
  assert.match(main, /inert=\{!drawerOpen\}/);
  assert.match(main, /aria-hidden=\{!drawerOpen\}/);
});

test('drawer close only clears local focus and never cancels remote operations', () => {
  const closeDrawer = main.slice(main.indexOf('function closeDrawer'), main.indexOf('function handleDrawerBackdropClick'));
  assert.match(closeDrawer, /setDrawerOpen\(false\)/);
  for (const token of ['release', 'disconnect', 'cancel', 'abort']) {
    assert.equal(closeDrawer.includes(token), false, `drawer close must not send ${token}`);
  }
});

test('missing capabilities render explicit unavailable states', () => {
  assert.match(main, /capabilities\?\.navigation === true/);
  assert.match(main, /capabilities\?\.tabs === true/);
  assert.match(main, /capabilities\?\.profiles === true/);
  assert.match(main, /capabilities\?\.connectionSetup === true/);
  assert.match(main, /canRequestQualitySnapshot/);
  assert.match(main, /标签页能力不可用/);
  assert.match(main, /Profile 能力不可用/);
  assert.match(main, /当前连接未提供画质能力/);
  assert.match(css, /\.capability-unavailable/);
});

test('quality keeps requested and effective as separate algebra', () => {
  assert.match(main, /requested\.profile/);
  assert.match(main, /effective\.generation === null \? '未确认'/);
  assert.match(main, /generation: currentQuality\.requested\.generation/);
  assert.match(main, /画质请求失败：\{qualityProblem\}。生效状态未在本地修改。/);
});

test('only typed chrome commands cross the native port', () => {
  for (const op of ['go_back', 'go_forward', 'reload', 'new_tab', 'select_tab', 'close_tab', 'open_connection_setup', 'import_connection', 'select_profile', 'navigate']) {
    assert.match(probe, new RegExp(`op: '${op}'`), `expected typed command ${op}`);
  }
  for (const op of ['input_text', 'send_text', 'click', 'scroll', 'play', 'stop', 'remote_ime', 'chrome_inset']) {
    assert.equal(probe.includes(`op: '${op}'`), false, `legacy command must be gone: ${op}`);
  }
});

'use strict';

const http = require('http');

function getJson(path) {
  return new Promise((resolve, reject) => {
    http.get({host: '127.0.0.1', port: 9332, path}, res => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', chunk => { body += chunk; });
      res.on('end', () => resolve(JSON.parse(body)));
    }).on('error', reject);
  });
}

(async () => {
  const targets = await getJson('/json/list');
  const page = targets.find(target => target.type === 'page');
  if (!page) throw new Error('NO_PAGE_TARGET');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let nextId = 1;
  const pending = new Map();
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    pending.get(message.id)(message);
    pending.delete(message.id);
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, message => message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result));
    ws.send(JSON.stringify({id, method, params}));
  });
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, {once: true});
    ws.addEventListener('error', reject, {once: true});
  });
  const expression = `(() => {
    const text = id => document.getElementById(id)?.textContent ?? null;
    const alert = document.querySelector('.drawer-section .error');
    let status = null;
    try { status = window.ProbeNative?.request(JSON.stringify({op: 'status'})); } catch (error) { status = String(error); }
    return JSON.stringify({
      drawerOpen: document.getElementById('browser-drawer')?.dataset.open ?? null,
      statusText: text('connection-title'),
      statusSummary: document.querySelector('.status-card .status-title')?.textContent ?? null,
      statusMeta: document.querySelector('.status-card .status-meta')?.textContent ?? null,
      error: alert?.textContent ?? null,
      repair: document.getElementById('connection-repair')?.textContent ?? null,
      setup: document.getElementById('connection-setup')?.textContent ?? null,
      importButton: document.getElementById('connection-import')?.textContent ?? null,
      status
    });
  })()`;
  const result = await send('Runtime.evaluate', {expression, returnByValue: true});
  process.stdout.write(result.result.value + '\n');
  ws.close();
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});

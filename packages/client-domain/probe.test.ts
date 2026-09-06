import { test } from 'node:test';
import assert from 'node:assert/strict';
import { androidPort, parseSnapshot } from './probe';
test('missing native capability fails explicitly', () => assert.throws(() => androidPort(undefined), /UNAVAILABLE/));
test('malformed native response cannot become ready', () => {
  assert.throws(() => parseSnapshot('{"state":"playing"}'), /INVALID/);
  assert.throws(() => parseSnapshot('not json'));
});
test('only typed local commands cross native bridge', () => {
  let request = '';
  const snapshot = { state: 'idle', generation: 0, renderedFrames: 0, released: true, codec: '', error: null };
  const port = androidPort({ request(raw) { request = raw; return JSON.stringify(snapshot); } });
  assert.deepEqual(port.request({op:'play',sample:'portrait'}), snapshot);
  assert.equal(request, '{"op":"play","sample":"portrait"}');
});
test('navigation keeps the user URL unchanged across the native port', () => {
  let request = '';
  const snapshot = { state: 'idle', generation: 0, renderedFrames: 0, released: true, codec: '', error: null };
  const port = androidPort({ request(raw) { request = raw; return JSON.stringify(snapshot); } });
  port.request({op:'navigate',epoch:4,url:'HTTP://example.invalid/path?q=1'});
  assert.equal(request, '{"op":"navigate","epoch":4,"url":"HTTP://example.invalid/path?q=1"}');
});

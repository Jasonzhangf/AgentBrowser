import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const source = readFileSync(join(root, 'scripts/macos-admission.mjs'), 'utf8');

assert(!source.includes('if (false)'), 'admission must not retain dead code');
assert(!source.includes("ids.reconnect"), 'receipt ids must not reference reconnect');
assert(!source.includes("reconnect: `${directory}"), 'console receipt must not claim reconnect');
assert.match(source, /pressAccessibilityButton\(firstApp\.pid, '打开菜单'/);
assert.match(source, /pressAccessibilityButton\(firstApp\.pid, '连接'/);
assert(!source.includes("'断开'"), 'admission must not press disconnect in narrowed slice');
assert(!source.includes('videoSurfaceGeometry'), 'unreachable video helper must be removed');
assert(!source.includes('focusAccessibilityTextField'), 'unreachable input helper must be removed');
assert(!source.includes('postUnicodeText'), 'unreachable text helper must be removed');
assert.match(source, /ax_target: '连接'/);
console.log('mac admission scope fixture: pass');

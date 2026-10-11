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
assert.match(source, /operation: 'connect'/);
assert.match(source, /skipped_operations/);
console.log('mac admission scope fixture: pass');

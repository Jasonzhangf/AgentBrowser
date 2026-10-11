import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const source = readFileSync(join(root, 'scripts/macos-admission.mjs'), 'utf8');

assert(!source.includes('if (false)'), 'admission must not retain dead code');
assert(!source.includes("'断开'"), 'admission must not press disconnect');
const menu = source.indexOf("pressAccessibilityButton(firstApp.pid, '打开菜单'");
const connect = source.indexOf("pressAccessibilityButton(firstApp.pid, '连接'");
assert(menu >= 0 && connect > menu, 'AX menu must precede connect');
assert.match(source, /operations: \['connect', 'restart'\]/);
assert.match(source, /skipped_operations: \['h264_display'/);
assert.match(source, /ax_actions:/);
assert.match(source, /connect_receipt:/);
console.log('mac admission scope fixture: pass');

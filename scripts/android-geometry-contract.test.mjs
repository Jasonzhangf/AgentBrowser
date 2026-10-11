import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync('apps/android/app/src/androidTest/java/com/agentbrowser/probe/NetworkDeviceTest.java', 'utf8');
const importPath = source.slice(source.indexOf('private void importOrdinaryPairing'), source.indexOf('public void testOrdinaryProtectedImport'));

assert.match(importPath, /touchChrome\("drawer-toggle"\)/, 'ordinary admission must map the drawer from live WebView geometry');
assert.match(importPath, /touchChrome\("connection-import"\)/, 'ordinary admission must map import from live WebView geometry');
assert.doesNotMatch(importPath, /device\.click\(\s*\d+\s*,\s*\d+\s*\)/, 'ordinary admission must not use device constants');
assert.doesNotMatch(importPath, /By\.text\("导入"\)/, 'ordinary admission must not depend on inaccessible WebView text');
assert.match(source, /getBoundingClientRect\(\)/, 'mapping must derive coordinates from the live DOM');
assert.match(source, /getLocationOnScreen\(origin\)/, 'mapping must account for native WebView screen origin');
console.log('Android live geometry contract: 6 passed');

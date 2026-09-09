import {cpSync, mkdirSync, mkdtempSync, rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

// tsc must succeed before packaging. Only ws is needed at runtime.
execFileSync(process.execPath, [fileURLToPath(new URL('../../protocol/relay/validate.mjs', import.meta.url)), '--check']);
const source = new URL('./', import.meta.url);
const output = new URL('../../generated/modules/relay-service/lib/', source);
mkdirSync(output, {recursive: true});
const stage = mkdtempSync(join(tmpdir(), 'relay-package-'));
try {
  cpSync(new URL('dist/', source), join(stage, 'dist'), {recursive: true});
  cpSync(new URL('package.json', source), join(stage, 'package.json'));
  mkdirSync(join(stage, 'node_modules'));
  cpSync(new URL('node_modules/ws/', source), join(stage, 'node_modules/ws'), {recursive: true});
  execFileSync('tar', ['-cf', fileURLToPath(new URL('relay.tar', output)), '-C', stage, '.']);
} finally { rmSync(stage, {recursive: true, force: true}); }

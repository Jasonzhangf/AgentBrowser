// Real relay replay: build the module, run its whitebox suite, then run the
// unpacked HTTPS/WSS entrypoint from the compiled CLI. This adapter writes no
// lifecycle records and binds no artifact identity.
import {spawnSync} from 'node:child_process';
import {existsSync, mkdirSync, writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';

const moduleId = 'relay-service';
function run(program, args, log, cwd = '.') {
  const result = spawnSync(program, args, {cwd, maxBuffer: 32 * 1024 * 1024, timeout: 120000});
  if (log) writeFileSync(log, Buffer.concat([result.stdout ?? Buffer.alloc(0), result.stderr ?? Buffer.alloc(0)]));
  assert(!result.error && result.status === 0, `${program} failed: ${result.error ?? result.stderr}`);
  return result.stdout;
}
const git = (...args) => run('git', args).toString().trim();
assert(!['main', 'master'].includes(git('branch', '--show-current')), 'Owner branch required');
assert.equal(git('status', '--porcelain'), '', 'Commit the complete candidate before replay');
const directory = `evidence/relay-replay-${git('rev-parse', '--short=12', 'HEAD')}-${Date.now()}`;
mkdirSync(directory, {recursive: true});

run('appsdk', ['compile-module', '--module', moduleId], `${directory}/compile.log`);
const archive = `generated/modules/${moduleId}/lib/relay.tar`;
assert(existsSync(archive), `Compiled relay archive missing: ${archive}`);
run('npm', ['--prefix', 'services/relay', 'test'], `${directory}/whitebox.log`);
run('node_modules/.bin/tsx', ['--test', '--test-name-pattern=compiled CLI', 'test/server.test.ts'],
  `${directory}/blackbox.log`, 'services/relay');
console.log(JSON.stringify({directory, archive, result: 'pass'}, null, 2));

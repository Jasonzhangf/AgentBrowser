// W4 UI chrome public consumer build. Isolated from the shared build scripts so
// it can bundle the real UI plugin plus the minimal NativePort consumer without
// touching product build outputs. Output defaults to .w4-tmp/ui.
import { build } from 'esbuild';
import { mkdir, copyFile } from 'node:fs/promises';

const out = process.argv[2] ?? '.w4-tmp/ui';
await mkdir(out, { recursive: true });

const shared = {
  bundle: true,
  platform: 'browser',
  target: 'chrome110',
  minify: true,
  define: {'process.env.NODE_ENV': '"production"'},
};

// Real chrome UI bundle. The CSS import is emitted as a sibling app.css.
await build({...shared, entryPoints: ['packages/ui-plugins/main.tsx'], outfile: `${out}/app.js`});
// Public consumer host page and the NativePort frame that loads the real UI.
await build({...shared, entryPoints: ['packages/ui-plugins/browser-consumer.tsx'], outfile: `${out}/consumer.js`});
await build({...shared, entryPoints: ['packages/ui-plugins/consumer-frame.ts'], outfile: `${out}/consumer-frame.js`});

await copyFile('packages/ui-plugins/consumer.html', `${out}/consumer.html`);
await copyFile('packages/ui-plugins/ui-frame.html', `${out}/ui-frame.html`);

console.log(JSON.stringify({output: out}));

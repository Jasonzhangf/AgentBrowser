import { build } from 'esbuild';
import { mkdir, copyFile } from 'node:fs/promises';
const out = 'apps/android/app/build/generated/probeAssets/ui';
await mkdir(out, { recursive: true });
await build({ entryPoints: ['packages/ui-plugins/main.tsx'], bundle: true, outfile: `${out}/app.js`, platform: 'browser', target: 'chrome110', minify: true, define: {'process.env.NODE_ENV':'"production"'} });
await copyFile('packages/ui-plugins/index.html', `${out}/index.html`);

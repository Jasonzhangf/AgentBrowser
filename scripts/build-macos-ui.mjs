import { build } from 'esbuild';
import { copyFile, mkdir } from 'node:fs/promises';

const output = 'apps/macos/build/ui';
await mkdir(output, { recursive: true });
await build({
  entryPoints: ['packages/ui-plugins/main.tsx'],
  bundle: true,
  outfile: `${output}/app.js`,
  platform: 'browser',
  target: 'safari17',
  minify: true,
  define: { 'process.env.NODE_ENV': '"production"' },
});
await copyFile('packages/ui-plugins/index.html', `${output}/index.html`);

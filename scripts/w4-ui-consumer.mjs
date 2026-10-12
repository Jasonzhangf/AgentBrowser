import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';

const root = process.argv[2];
if (!root) throw new Error('W4_OUTPUT_ROOT_REQUIRED');

const types = new Map([
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.css', 'text/css; charset=utf-8'],
  ['.png', 'image/png'],
]);

const server = createServer(async (request, response) => {
  try {
    const pathname = new URL(request.url ?? '/', 'http://127.0.0.1').pathname;
    const requested = pathname === '/' ? '/consumer.html' : pathname;
    const safe = normalize(requested).replace(/^(\.\.(\/|\\|$))+/, '');
    const body = await readFile(join(root, safe));
    response.writeHead(200, {'content-type': types.get(extname(safe)) ?? 'application/octet-stream', 'cache-control': 'no-store'});
    response.end(body);
  } catch {
    response.writeHead(404, {'content-type': 'text/plain; charset=utf-8'});
    response.end('not found');
  }
});

server.listen(0, '127.0.0.1', () => {
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('W4_CONSUMER_ADDRESS_UNAVAILABLE');
  console.log(JSON.stringify({url: `http://127.0.0.1:${address.port}/consumer.html`, pid: process.pid}));
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => server.close(() => process.exit(0)));
}

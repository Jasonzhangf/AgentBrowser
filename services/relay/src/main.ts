import {readFileSync} from 'node:fs';
import {RelayStore} from './store.js';
import {createRelayServer} from './server.js';

async function main() {
  const [command, ...args] = process.argv.slice(2);
  if (command === 'account-add' && args.length === 2) {
    const [db, username] = args as [string, string];
    const chunks: Buffer[] = []; let length = 0;
    for await (const chunk of process.stdin) {
      length += chunk.length;
      if (length > 4096) throw new Error('Password input too large');
      chunks.push(chunk);
    }
    const password = Buffer.concat(chunks).toString('utf8').replace(/\r?\n$/, '');
    const store = new RelayStore(db);
    try { await store.createAccount(username, password); process.stdout.write('Account created\n'); }
    finally { store.close(); }
    return;
  }
  if (command !== 'serve' || args.length !== 3) throw new Error('Usage: relay account-add <db> <username> (password on stdin), or relay serve <db> <cert.pem> <key.pem>');
  const port = Number(process.env.RELAY_PORT ?? 8443);
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('Invalid RELAY_PORT');
  const store = new RelayStore(args[0]!);
  const relay = createRelayServer({store, tls: {cert: readFileSync(args[1]!), key: readFileSync(args[2]!)}});
  let stopping = false;
  const stop = async () => {
    if (stopping) return;
    stopping = true;
    try { await relay.close(); } finally { store.close(); }
  };
  try {
    const address = await relay.listen(port, process.env.RELAY_BIND ?? '127.0.0.1');
    process.stdout.write(JSON.stringify({event: 'listening', address}) + '\n');
  } catch (error) { await stop(); throw error; }
  process.once('SIGTERM', () => { void stop().catch(error => {console.error(error); process.exitCode = 1;}); });
  process.once('SIGINT', () => { void stop().catch(error => {console.error(error); process.exitCode = 1;}); });
}
main().catch(error => {console.error(error instanceof Error ? error.message : 'Relay failed'); process.exitCode = 1;});

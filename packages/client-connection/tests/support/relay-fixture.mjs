import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {mkdirSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join, resolve} from 'node:path';
import {generateKeyPairSync, sign} from 'node:crypto';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../../..');
const require = createRequire(pathToFileURL(join(root, 'services/relay/package.json')));
const {WebSocket} = require('ws');
const {RelayStore, digest} = await import(pathToFileURL(join(root, 'services/relay/src/store.ts')).href);
const {createRelayServer} = await import(pathToFileURL(join(root, 'services/relay/src/server.ts')).href);
const {authTranscript} = await import(pathToFileURL(join(root, 'protocol/relay/index.ts')).href);

const passwords = {alice: 'alice-password-123', bob: 'bob-password-123'};
const fixtureRoot = join(tmpdir(), `agentbrowser-relay-client-${process.pid}-${Date.now()}`);
mkdirSync(fixtureRoot, {recursive: true, mode: 0o700});
const keyFile = join(fixtureRoot, 'relay-key.pem');
const certFile = join(fixtureRoot, 'relay-cert.pem');
const caKeyFile = join(fixtureRoot, 'relay-ca-key.pem');
const caFile = join(fixtureRoot, 'relay-ca.pem');
const csrFile = join(fixtureRoot, 'relay.csr');
const extFile = join(fixtureRoot, 'relay.ext');
execFileSync('openssl', [
  'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
  '-keyout', caKeyFile, '-out', caFile, '-days', '1', '-subj', '/CN=Relay test CA',
  '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0',
  '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
], {stdio: 'ignore'});
execFileSync('openssl', [
  'req', '-newkey', 'rsa:2048', '-nodes',
  '-keyout', keyFile, '-out', csrFile, '-days', '1', '-subj', '/CN=localhost',
], {stdio: 'ignore'});
writeFileSync(extFile,
  'subjectAltName=DNS:localhost,IP:127.0.0.1\n' +
  'basicConstraints=critical,CA:FALSE\n' +
  'keyUsage=critical,digitalSignature,keyEncipherment\n' +
  'extendedKeyUsage=serverAuth\n');
execFileSync('openssl', [
  'x509', '-req', '-in', csrFile, '-CA', caFile, '-CAkey', caKeyFile,
  '-CAcreateserial', '-out', certFile, '-days', '1', '-extfile', extFile,
], {stdio: 'ignore'});

const store = new RelayStore(join(fixtureRoot, 'relay.sqlite'));
await store.createAccount('alice', passwords.alice);
await store.createAccount('bob', passwords.bob);
const hostLogin = await store.login('alice', passwords.alice);
const hostAccount = store.authenticate(hostLogin.token);
const hostKeys = generateKeyPairSync('ed25519');
const hostDevice = store.addDevice(
  hostAccount,
  'loopback-host',
  hostKeys.publicKey.export({type: 'spki', format: 'pem'}).toString(),
);
const host = store.addHost(hostAccount, hostDevice.id);
const relay = createRelayServer({store, ticketTtlMs: 10_000, sweepMs: 50, tls: {
  key: readFileSync(keyFile),
  cert: readFileSync(certFile),
}});
const origin = await relay.listen(0, '127.0.0.1');
const wsOrigin = origin.replace(/^https:/, 'wss:');
const sockets = [];
let hostControl;
let shuttingDown;

function connect(url, options = {}) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url, {...options, ca: readFileSync(caFile)});
    const fail = error => reject(error);
    ws.once('error', fail);
    ws.once('open', () => {
      ws.off('error', fail);
      resolve(ws);
    });
  });
}

async function pairTunnel(offer) {
  for (const channel of ['control', 'media']) {
    const entry = offer.channels[channel];
    const socket = await connect(`${wsOrigin}${entry.path}`, {
      headers: {authorization: `Bearer ${entry.ticket}`},
    });
    sockets.push(socket);
    socket.on('message', (raw, binary) => {
      if (binary) socket.send(raw, {binary: true});
    });
  }
}

async function startHost() {
  hostControl = await connect(`${wsOrigin}/v2/control/host/${host.id}`);
  sockets.push(hostControl);
  return new Promise((resolveReady, reject) => {
    let ready = false;
    const fail = error => { if (!ready) reject(error); };
    hostControl.on('error', fail);
    hostControl.on('message', raw => {
      const message = JSON.parse(raw.toString());
      if (message.type === 'auth.challenge') {
        const signature = sign(
          null,
          authTranscript(message.nonce, `/v2/control/host/${host.id}`, hostDevice.id, digest(hostLogin.token)),
          hostKeys.privateKey,
        ).toString('base64url');
        hostControl.send(JSON.stringify({
          type: 'auth.prove', token: hostLogin.token, deviceId: hostDevice.id, signature,
        }));
      } else if (message.type === 'auth.ready' && !ready) {
        ready = true;
        hostControl.send(JSON.stringify({type: 'host.publish', snapshot: {
          incarnation: 'relay-fixture', revision: 1, endpoints: [], sessions: [{id: 'fixture-session'}],
        }}));
        resolveReady();
      } else if (message.type === 'tunnel.offer') {
        void pairTunnel(message).catch(error => {
          process.stderr.write(`host tunnel pairing failed: ${error.message}\n`);
          hostControl.terminate();
        });
      }
    });
  });
}

await startHost();
await new Promise(resolve => setTimeout(resolve, 100));
process.stdout.write(`${JSON.stringify({
  event: 'ready',
  origin,
  caPath: caFile,
  databasePath: join(fixtureRoot, 'relay.sqlite'),
  hostId: host.id,
  hostDeviceId: hostDevice.id,
  alice: {username: 'alice', password: passwords.alice},
  bob: {username: 'bob', password: passwords.bob},
})}\n`);

async function cleanup() {
  if (shuttingDown) return shuttingDown;
  shuttingDown = (async () => {
    for (const socket of sockets) socket.terminate();
    await relay.close();
    store.close();
    rmSync(fixtureRoot, {recursive: true, force: true});
  })();
  return shuttingDown;
}

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  input += chunk;
  let newline;
  while ((newline = input.indexOf('\n')) >= 0) {
    const line = input.slice(0, newline);
    input = input.slice(newline + 1);
    if (line.trim() === '{"command":"shutdown"}') {
      void cleanup().then(() => process.exit(0));
    }
  }
});
process.once('SIGTERM', () => { void cleanup().then(() => process.exit(0)); });
process.once('SIGINT', () => { void cleanup().then(() => process.exit(0)); });

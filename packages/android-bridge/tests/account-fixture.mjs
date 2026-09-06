import {createServer as createHttpsServer} from 'node:https';
import {execFileSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {mkdirSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, join, resolve} from 'node:path';
import {generateKeyPairSync, sign} from 'node:crypto';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const require = createRequire(pathToFileURL(join(root, 'services/relay/package.json')));
const {RelayStore, digest} = await import(pathToFileURL(join(root, 'services/relay/src/store.ts')).href);
const {createRelayServer} = await import(pathToFileURL(join(root, 'services/relay/src/server.ts')).href);
const {authTranscript} = await import(pathToFileURL(join(root, 'protocol/relay/index.ts')).href);

const bindHost = process.env.ACCOUNT_RELAY_BIND_HOST;
const advertiseHost = process.env.ACCOUNT_RELAY_ADVERTISE_HOST;
if (!bindHost || !advertiseHost) throw new Error('ACCOUNT_RELAY_BIND_HOST and ACCOUNT_RELAY_ADVERTISE_HOST are required');
const failRevoke = process.argv.includes('--fail-revoke');
const username = 'account-alice';
const password = 'account-password-123';
const fixtureRoot = join(tmpdir(), `agentbrowser-account-${process.pid}-${Date.now()}`);
mkdirSync(fixtureRoot, {recursive: true, mode: 0o700});
const keyFile = join(fixtureRoot, 'relay-key.pem');
const certFile = join(fixtureRoot, 'relay-cert.pem');
const caKeyFile = join(fixtureRoot, 'relay-ca-key.pem');
const caFile = join(fixtureRoot, 'relay-ca.pem');
const caDerFile = join(fixtureRoot, 'relay-ca.der');
const wrongCaKeyFile = join(fixtureRoot, 'wrong-ca-key.pem');
const wrongCaFile = join(fixtureRoot, 'wrong-ca.pem');
const wrongCaDerFile = join(fixtureRoot, 'wrong-ca.der');
const csrFile = join(fixtureRoot, 'relay.csr');
const extFile = join(fixtureRoot, 'relay.ext');

execFileSync('openssl', [
  'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', caKeyFile, '-out', caFile,
  '-days', '1', '-subj', '/CN=AgentBrowser account fixture CA',
  '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0',
  '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
], {stdio: 'ignore'});
execFileSync('openssl', [
  'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', wrongCaKeyFile, '-out', wrongCaFile,
  '-days', '1', '-subj', '/CN=AgentBrowser wrong account fixture CA',
  '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0',
  '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
], {stdio: 'ignore'});
execFileSync('openssl', ['x509', '-in', caFile, '-outform', 'der', '-out', caDerFile], {stdio: 'ignore'});
execFileSync('openssl', ['x509', '-in', wrongCaFile, '-outform', 'der', '-out', wrongCaDerFile], {stdio: 'ignore'});
execFileSync('openssl', [
  'req', '-newkey', 'rsa:2048', '-nodes', '-keyout', keyFile, '-out', csrFile,
  '-days', '1', '-subj', '/CN=AgentBrowser account fixture Relay',
], {stdio: 'ignore'});
writeFileSync(extFile,
  `subjectAltName=DNS:localhost,IP:127.0.0.1,IP:${advertiseHost}\n` +
  'basicConstraints=critical,CA:FALSE\n' +
  'keyUsage=critical,digitalSignature,keyEncipherment\n' +
  'extendedKeyUsage=serverAuth\n');
execFileSync('openssl', [
  'x509', '-req', '-in', csrFile, '-CA', caFile, '-CAkey', caKeyFile,
  '-CAcreateserial', '-out', certFile, '-days', '1', '-extfile', extFile,
], {stdio: 'ignore'});

const store = new RelayStore(join(fixtureRoot, 'relay.sqlite'));
await store.createAccount(username, password);
const hostLogin = await store.login(username, password);
const hostAccount = store.authenticate(hostLogin.token);
const hostKeys = generateKeyPairSync('ed25519');
const hostDevice = store.addDevice(
  hostAccount,
  'account-fixture-host',
  hostKeys.publicKey.export({type: 'spki', format: 'pem'}).toString(),
);
const host = store.addHost(hostAccount, hostDevice.id);
if (failRevoke) store.revoke = () => { throw new Error('ACCOUNT_FIXTURE_REVOKE_FAILURE'); };
const relay = createRelayServer({store, directoryTtlMs: 30_000, ticketTtlMs: 10_000, sweepMs: 50, tls: {
  key: readFileSync(keyFile),
  cert: readFileSync(certFile),
}});
const relayOrigin = await relay.listen(0, bindHost);
const relayPort = new URL(relayOrigin).port;
const localOrigin = `https://127.0.0.1:${relayPort}`;
const origin = `https://${advertiseHost}:${relayPort}`;
const sockets = [];
let hostControl;
let shuttingDown;

function connect(url, options = {}) {
  return new Promise((resolveConnection, reject) => {
    const socket = new (require('ws').WebSocket)(url, {...options, ca: readFileSync(caFile)});
    const fail = error => reject(error);
    socket.once('error', fail);
    socket.once('open', () => {
      socket.off('error', fail);
      resolveConnection(socket);
    });
  });
}

async function startHost() {
  hostControl = await connect(`${localOrigin.replace(/^https:/, 'wss:')}/v2/control/host/${host.id}`);
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
          incarnation: 'account-fixture', revision: 1, endpoints: [], sessions: [{id: 'account-fixture-session'}],
        }}));
        resolveReady();
      }
    });
  });
}

const control = createHttpsServer({key: readFileSync(keyFile), cert: readFileSync(certFile)}, (request, response) => {
  if (request.method === 'POST' && request.url === '/drop-host') {
    hostControl?.terminate();
    hostControl = undefined;
    response.writeHead(200, {'content-type': 'application/json'});
    response.end('{"ok":true}');
    return;
  }
  response.writeHead(404, {'content-type': 'application/json'});
  response.end('{"error":"not found"}');
});

await startHost();
await new Promise((resolveControl, rejectControl) => {
  control.once('error', rejectControl);
  control.listen(0, bindHost, () => {
    control.off('error', rejectControl);
    resolveControl();
  });
});
const controlAddress = control.address();
if (!controlAddress || typeof controlAddress === 'string') throw new Error('Invalid account fixture control address');
process.stdout.write(`${JSON.stringify({
  event: 'ready', scenario: failRevoke ? 'revoke-failure' : 'directory', origin,
  controlUrl: `https://${advertiseHost}:${controlAddress.port}`,
  caPath: caFile, caDerPath: caDerFile, wrongCaDerPath: wrongCaDerFile,
  hostId: host.id, hostDeviceId: hostDevice.id,
})}\n`);

async function cleanup() {
  if (shuttingDown) return shuttingDown;
  shuttingDown = (async () => {
    for (const socket of sockets) socket.terminate();
    await relay.close();
    await new Promise(resolveControl => control.close(() => resolveControl()));
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
    if (line.trim() === '{"command":"shutdown"}') void cleanup().then(() => process.exit(0));
  }
});
process.once('SIGTERM', () => { void cleanup().then(() => process.exit(0)); });
process.once('SIGINT', () => { void cleanup().then(() => process.exit(0)); });

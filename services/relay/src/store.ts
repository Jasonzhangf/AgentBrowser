import {createHash, createPublicKey, randomBytes, randomUUID, scrypt, timingSafeEqual} from 'node:crypto';
import {DatabaseSync} from 'node:sqlite';
import {RelayError, text} from '../../../protocol/relay/index.js';

export const digest = (value: string) => createHash('sha256').update(value).digest('hex');
const derive = (password: string, salt: string) => new Promise<Buffer>((resolve, reject) => {
  scrypt(password, salt, 32, (error, value) => error ? reject(error) : resolve(value));
});
export interface Account {id: string; expiresAt: number}
export interface Device {id: string; accountId: string; name: string; publicKey: string}
export interface Host {id: string; accountId: string; deviceId: string}

export class RelayStore {
  private readonly db: DatabaseSync;
  readonly now: () => number;
  readonly tokenTtlMs: number;
  constructor(path: string, options: {now?: () => number; tokenTtlMs?: number} = {}) {
    this.now = options.now ?? Date.now;
    this.tokenTtlMs = options.tokenTtlMs ?? 15 * 60_000;
    if (!Number.isSafeInteger(this.tokenTtlMs) || this.tokenTtlMs < 1) throw new Error('Invalid token TTL');
    this.db = new DatabaseSync(path);
    this.db.exec(`PRAGMA foreign_keys=ON;
      CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, hash TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS tokens(hash TEXT PRIMARY KEY, accountId TEXT NOT NULL REFERENCES accounts(id), expiresAt INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, accountId TEXT NOT NULL REFERENCES accounts(id), name TEXT NOT NULL, publicKey TEXT UNIQUE NOT NULL);
      CREATE TABLE IF NOT EXISTS hosts(id TEXT PRIMARY KEY, accountId TEXT NOT NULL REFERENCES accounts(id), deviceId TEXT UNIQUE NOT NULL REFERENCES devices(id));`);
  }

  async createAccount(username: string, password: string) {
    if (!/^[a-zA-Z0-9_-]{3,64}$/.test(username)) throw new RelayError('INVALID_USERNAME', 'Invalid username');
    if (text(password, 'password', 1024).length < 12) throw new RelayError('WEAK_PASSWORD', 'Password must contain at least 12 characters');
    if (this.db.prepare('SELECT id FROM accounts WHERE username=?').get(username)) throw new RelayError('ACCOUNT_EXISTS', 'Account exists', 409);
    const salt = randomBytes(16).toString('hex');
    const hash = (await derive(password, salt)).toString('hex');
    this.db.prepare('INSERT INTO accounts VALUES(?,?,?,?)').run(randomUUID(), username, salt, hash);
  }

  async login(username: string, password: string) {
    text(username, 'username', 64); text(password, 'password', 1024);
    const row = this.db.prepare('SELECT * FROM accounts WHERE username=?').get(username) as {id: string; salt: string; hash: string} | undefined;
    const value = await derive(password, row?.salt ?? 'missing-account-salt');
    if (!row || !timingSafeEqual(value, Buffer.from(row.hash, 'hex'))) throw new RelayError('UNAUTHORIZED', 'Invalid credentials', 401);
    this.db.prepare('DELETE FROM tokens WHERE expiresAt<=?').run(this.now());
    const count = this.db.prepare('SELECT count(*) AS n FROM tokens WHERE accountId=?').get(row.id) as {n: number};
    if (count.n >= 32) throw new RelayError('TOKEN_LIMIT', 'Account token limit reached', 429);
    const token = randomBytes(32).toString('base64url');
    const expiresAt = this.now() + this.tokenTtlMs;
    this.db.prepare('INSERT INTO tokens VALUES(?,?,?)').run(digest(token), row.id, expiresAt);
    return {token, expiresAt};
  }

  authenticate(token: string): Account {
    if (typeof token !== 'string' || token.length > 256) throw new RelayError('UNAUTHORIZED', 'Token expired or invalid', 401);
    const row = this.db.prepare('SELECT accountId, expiresAt FROM tokens WHERE hash=?').get(digest(token)) as {accountId: string; expiresAt: number} | undefined;
    if (!row || row.expiresAt <= this.now()) throw new RelayError('UNAUTHORIZED', 'Token expired or invalid', 401);
    return {id: row.accountId, expiresAt: row.expiresAt};
  }

  revoke(token: string) { this.db.prepare('DELETE FROM tokens WHERE hash=?').run(digest(token)); }

  addDevice(account: Account, name: string, publicKey: string): Device {
    text(name, 'device name', 64); text(publicKey, 'public key', 1024);
    let normalized: string;
    try {
      const key = createPublicKey(publicKey);
      if (key.asymmetricKeyType !== 'ed25519') throw new Error('Ed25519 required');
      normalized = key.export({type: 'spki', format: 'pem'}).toString();
    } catch { throw new RelayError('INVALID_KEY', 'Ed25519 public key required'); }
    const old = this.db.prepare('SELECT * FROM devices WHERE publicKey=?').get(normalized) as Device | undefined;
    if (old) {
      if (old.accountId !== account.id) throw new RelayError('DEVICE_CONFLICT', 'Key already registered', 409);
      return old;
    }
    const count = this.db.prepare('SELECT count(*) AS n FROM devices WHERE accountId=?').get(account.id) as {n: number};
    if (count.n >= 32) throw new RelayError('DEVICE_LIMIT', 'Device limit reached', 429);
    const device = {id: randomUUID(), accountId: account.id, name, publicKey: normalized};
    this.db.prepare('INSERT INTO devices VALUES(?,?,?,?)').run(device.id, account.id, name, normalized);
    return device;
  }

  device(account: Account, id: string): Device {
    const row = this.db.prepare('SELECT * FROM devices WHERE id=? AND accountId=?').get(id, account.id) as Device | undefined;
    if (!row) throw new RelayError('NOT_FOUND', 'Device unavailable', 404);
    return row;
  }

  addHost(account: Account, deviceId: string): Host {
    this.device(account, text(deviceId, 'device id'));
    const old = this.db.prepare('SELECT * FROM hosts WHERE deviceId=?').get(deviceId) as Host | undefined;
    if (old) return old;
    const host = {id: randomUUID(), accountId: account.id, deviceId};
    this.db.prepare('INSERT INTO hosts VALUES(?,?,?)').run(host.id, account.id, deviceId);
    return host;
  }

  host(account: Account, id: string): Host {
    const row = this.db.prepare('SELECT * FROM hosts WHERE id=? AND accountId=?').get(id, account.id) as Host | undefined;
    if (!row) throw new RelayError('NOT_FOUND', 'Host unavailable', 404);
    return row;
  }

  close() { this.db.close(); }
}

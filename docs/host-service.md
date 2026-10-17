# Mac Host service entrypoint

Status: AgentBrowser-side local lifecycle entrypoint for the Obscura Host.
Owner: `scripts/host-service/`. This slice does not own the browser Session,
the Obscura engine, the direct endpoint, or the Mac UI.

The frozen connection contract is `docs/m1/connection-minimum.md` §7. This
document describes the entrypoint that implements it. Where this document and
§7 disagree, §7 is the contract.

## Connection minimum

One credential, one comparison. The service owns two control resources under
`<root>/trust`, plus the endpoint's own listener identity:

```text
trust/
  endpoint.json    0600  endpoint locator: host, host kind, port, generation
  token            0600  the single access token
  server.der       0644  endpoint listener TLS identity, self-signed
  server-key.der   0600  its private key, never exported
```

- `endpoint.json` holds exactly `endpoint_host`, `endpoint_host_kind`,
  `endpoint_port`, and `endpoint_generation`.
- `token` is the whole credential. It is an opaque URL-safe string.
- `server.der` and `server-key.der` are the listener's own TLS identity. The
  client does not verify the server certificate, because the user holds no CA
  file. These are not a client credential and never leave the service root.

There is no certificate authority, no client certificate, no PKCS#12 container,
no passphrase, no transfer code, no scope, no profile grant, no device registry,
no authorization projection, and no device id. None of those exist anywhere on
the connection path.

The Host is the only token comparator. It compares the presented token and
nothing else: equal admits the session, unequal answers `REMOTE_TOKEN_INVALID`.
`obscura-endpoint` forwards the `Authorization: Bearer` value verbatim and maps
the Host's denial to the client error frame. The same secret is compared once.

## Lifecycle order (frozen, §7.7)

```text
install  ->  start  ->  panel (trust init -> trust endpoint-refresh -> trust export)
         ->  phone import  ->  connect
```

`trust init` is **not** a precondition for `start`. `install` creates the access
token idempotently and never rotates it. `start` reads the token from
`<root>/trust/token`; only a genuinely absent token is a hard error
(`TRUST_NOT_INITIALIZED`). `trust init` records where the endpoint is reachable
and is a panel step, not a lifecycle prerequisite.

The user path is therefore: install the service, start it, open the panel,
export one connection file, import it on the phone, connect.

## Runtime contract

The service starts the real Obscura Host binary with one fresh socket directory
and the access token:

```text
obscura-host --socket-dir <fresh-private-directory>
              --access-token <token>
              [--profile-root <canonical-absolute-directory>]
              [--allow-private-network]
```

The flag order is fixed, and each optional flag appears at most once. The
service uses that exact argv for ownership comparison, so a PID whose command
line carries an unexpected argument is classified as foreign and is never
signalled.

`--profile-root` is passed only when the service was installed with
`--profile-root`; the service never substitutes a default shared root and never
modifies an existing profile directory. `--allow-private-network` is passed only
when enabled in the typed config. The service does not start `obscura serve`,
does not create a second Session owner, and does not replace
`obscura-endpoint`. The endpoint is a separate transport process that connects
to an already-running Host.

The socket directory name is short (`host-<8 hex>`). The real Host binds an
additional `session-<36-char-uuid>.sock` inside that directory, and the combined
path must stay under the 104-byte macOS Unix-socket bound. The service keeps its
owned directory name short so an ordinary private runtime root still fits, and
it lets the Host report an explicit error when a caller supplies a runtime root
that cannot.

Reinstalling without `--profile-root` reuses the canonical root already stored
in a v2 config. It never silently drops that root or modifies the existing
profile directory; switching to a different explicit root requires passing the
new root to `install`.

The Host itself owns `Session`, `Page`, attachment admission, operation identity,
viewport state, and `host.sock`/`frames.sock`. A client closing its Unix socket
only detaches that attachment. The Host process and its Page continue until the
service is explicitly stopped or the Host exits. Reconnecting to the same daemon
sees the same Session identity.

The Host ready response is checked against the expected protocol version bound at
install time. The expected value is a positive `u32` supplied by the deployment
owner as `--protocol-version`; it is stored in typed config and is the only
source used by ready validation and status projection. A parsed ready response
with another version fails immediately and the owned child/runtime are cleaned
through the normal startup failure path. The service does not infer the expected
version from the first ready response, logs, environment, or payloads, and it
does not retry another version.

Startup records the actual `session_id`, binary path, binary SHA-256, service
label, config ID, config hash, and configured protocol version in service state.
`status` reports the configured version as typed config projection; it never
reconstructs ownership or health from log text.

The Host ready response is also the first message on the local Unix maintenance
connection. For a persistent service, the manager sends the Host-local
`flush_profiles` request before stop or restart and requires the matching typed
`ProfileFlush` result with `flushed: true`. A busy, storage-failed, timed-out, or
otherwise unknown result fails explicitly; the manager does not signal the Host,
remove state, start a replacement, or report `saved`.

That flush only runs for a Host that still answers the ready handshake. The
manager probes it first with the same one-second probe `status` uses. A Host that
answers the probe keeps the flush and its failure rules. A typed ready rejection
also keeps the flush, so its error is unchanged. An owned Host that does not
answer the probe cannot confirm a flush either: the skipped flush is reported on
stderr and the stop continues to the verified-PID stop and reap path, so the
service state and runtime are released instead of the Host surviving
unsignalled. A dead or foreign recorded PID is classified before the probe and
keeps its existing result.

`status` validates a ready response through the same configured-version rule as
startup and rejects it when the ready session does not match the state. A dead
recorded PID is reported as `stale`; a mismatching ready response is reported as
terminal `ready_rejected`, both with a non-zero status exit. Neither terminal
state is interpreted as successful startup progress.

## Profile boundary

`profile_id` identifies the service configuration. The install argument
`--profile-root` is optional. With no explicit root, config version 2 projects
`profile_mode: in_memory` and `profile_root: null`; the Host runs one in-memory
profile and a restart creates a new Session identity. With an explicit root,
config and state project `profile_mode: persistent` and the canonical
`profile_root`; the service passes that exact root to the Host on both subprocess
and launchd starts.

The Host owns the profile catalog, profile selection, profile storage and the
meaning of a successful flush. The service owns only the process/config and
consumes the typed Host maintenance result before lifecycle termination. It does
not read profile files, infer saved state from logs or cookies, or treat ordinary
UI profile selection as a service configuration change or restart.

Config version 1 is accepted only as the legacy in-memory shape and is normalized
to an explicit null root for downstream projection. A v2 config must contain
`profile_root`; a persistent mode without a root, a root with in-memory mode, or
a non-canonical root fails explicitly. Credentials, bearer tokens, and client
certificates are not accepted as service config and are not written to service
logs or the launchd plist. The access token lives only in `trust/token`; it never
enters `config.json`, argv that is logged, or the plist.

## Commands

The executable launcher is `scripts/host-service/host-service`. `--root` is
accepted before the command or on the command itself. The default root is
`$HOME/Library/Application Support/AgentBrowser/host-service`.

### Lifecycle

```sh
scripts/host-service/host-service install \
  --root "$ROOT" \
  --binary /absolute/path/to/obscura-host \
  --protocol-version 11 \
  [--profile-root /absolute/private/profile-root] \
  [--runtime-root /short/private/runtime-root] \
  [--launchd-root /absolute/launchd-root] \
  [--service-label com.agentbrowser.obscura-host] \
  [--profile-id default] \
  [--allow-private-network]

scripts/host-service/host-service start   --root "$ROOT" [--ready-timeout 60]
scripts/host-service/host-service status  --root "$ROOT"
scripts/host-service/host-service stop    --root "$ROOT" [--service-label com.agentbrowser.obscura-host]
scripts/host-service/host-service restart --root "$ROOT" [--ready-timeout 60]
```

`install` binds one executable and writes private config, runtime/log roots, an
unloaded launchd plist, and the access token. It validates the executable's
actual `--help` entrypoint, validates the expected protocol version as a positive
`u32`, records the binary SHA-256, and refuses to start a changed binary until
reinstalled. It creates `<root>/trust/token` at `0600` if it does not exist, and
leaves an existing token byte-for-byte unchanged. Reinstalling therefore never
rotates the one secret the Host compares. The service root may contain spaces;
every derived flag and the ownership check use argv lists, so the exact path is
preserved without re-parsing it through a shell. The runtime root should use a
short absolute path because macOS limits Unix-domain socket path length; its
default is a private per-user directory under `/private/tmp`.

`install` prints one JSON object on stdout and exits 0:

```json
{"state": "installed", "config": "...", "service_label": "...",
 "host_binary": "...", "binary_sha256": "...", "protocol_version": 11,
 "profile_id": "default", "profile_mode": "in_memory", "profile_root": null,
 "runtime_root": "...", "launchd_plist": "...", "launchd_loaded": false}
```

`start` prints the running projection: `state`, `service_label`, `pid`,
`session_id`, `protocol_version`, `profile_id`, `profile_mode`, `profile_root`,
`socket_dir`, `config_id`, and `config_sha256`.

`status` distinguishes `stopped`, `running`, `starting`, `stale`,
`foreign_pid`, `identity_mismatch`, and `ready_rejected`. A running result
includes the current PID, Session ID, protocol version, config identity, binary
identity, and socket permissions. It does not report a daemon as persistent
merely because a socket file or old log exists.

Duplicate starts fail with the recorded PID. Lifecycle mutations share one lock,
and `stop` revalidates the verified PID immediately before both termination
signals. The command line must still contain the configured binary, the exact
socket directory, and the exact access token; the service never uses
process-name or process-group killing. Stale service state is cleaned only after
that PID is dead and the runtime directory matches the service-owned `host-*`
path shape.

Before any ownership check, signal, or runtime cleanup, `state.json` must match
the complete typed schema, including canonical binary/runtime/socket identities.
Malformed state fails explicitly and is never used to signal a PID or remove a
runtime directory. A recorded state whose committed token is missing cannot be
confirmed as this service's Host, so it is classified as foreign and is never
signalled.

For a persistent service, `stop` probes the live ready handshake and then sends
the local `flush_profiles` request, as the runtime contract above defines. Only a
matching `ProfileFlush { flushed: true, profiles: <u32> }` permits the existing
verified-PID termination path. `restart` performs the same stop-and-confirm
sequence and then starts a fresh Host with the same explicit root; it never
reuses the old Session or epoch. A rejected or unknown maintenance result leaves
the running Host and its recorded state in place.

Startup records a typed `subprocess_starting` state before waiting for the Host
ready response, then replaces it with the Session-bearing state. Failed startup
uses the current child handle for terminate/wait; if that cleanup cannot be
verified, the starting record and runtime are retained for an explicit later stop
instead of abandoning an untracked Host.

Service and runtime directories are created private. Existing directories must
already be `0700`; the manager refuses a shared directory and never chmods it as
a side effect. The launchd plist directory is only checked for being a directory,
while the plist itself is always `0600`.

### Trust

```sh
scripts/host-service/host-service trust init \
  --root "$ROOT" --endpoint-host <host> [--endpoint-port 8443]
scripts/host-service/host-service trust status --root "$ROOT"
scripts/host-service/host-service trust endpoint-refresh --root "$ROOT" [--timeout 30]
scripts/host-service/host-service trust export --root "$ROOT" --output <private-dir>/pairing.abconnection
```

These four commands are the whole trust surface. The removed `trust provision`
and `trust revoke` commands do not exist, and neither do their enrollment flags
`--device-id`, `--scope`, `--label`, `--initial-profile`, and
`--include-transfer-code`. (`--service-label` on `install` and `stop` is a
different flag: it names the launchd service, not a device.)

`trust init` records the endpoint locator in `trust/endpoint.json` and emits:

```json
{"state": "initialized", "endpoint": "100.64.0.1:8443",
 "endpoint_host": "100.64.0.1", "endpoint_port": 8443,
 "token_sha256": "...", "server_sha256": "..."}
```

It is idempotent. It creates the token if the root has none, but it never
rotates an existing token and never regenerates an existing listener identity.
Only the locator is rewritten, and it preserves the committed
`endpoint_generation`. `--endpoint-host` accepts a DNS name or an IP literal;
the accepted value is kept verbatim because it is what the listener certificate
SAN must carry. An unusable port is refused with `ENDPOINT_PORT_INVALID` before
anything is written.

`trust status` is the single public status calculation for the connection
configuration. It emits:

```json
{"state": "trust", "endpoint": "100.64.0.1:8443",
 "endpoint_host": "100.64.0.1", "endpoint_port": 8443,
 "endpoint_generation": 0, "endpoint_state": "not_refreshed",
 "endpoint_pid": null, "token_sha256": "..."}
```

`endpoint_state` is `refreshed` with the endpoint PID and generation, or
`not_refreshed` with `endpoint_pid: null`. It is never inferred from file
publication, a PID file, a health probe, or a log. A root with no committed
locator fails with `TRUST_NOT_INITIALIZED`.

`trust export` writes the connection bundle and emits it:

```json
{"endpoint": "100.64.0.1:8443", "token": "<opaque>"}
```

Those two fields are the whole bundle. There is no `format` and no `version`:
they existed only so the client could validate them, and the client validates
nothing. It is written to the `--output` path at
`0600`, newline-terminated. Export requires a running endpoint whose applied
receipt matches the current generation. Without a usable receipt it refuses:
`ENDPOINT_NOT_REFRESHED` when no applied receipt exists for the committed
generation, and `ENDPOINT_REFRESH_REQUIRED` when the locator carries no usable
generation. A write failure is `BUNDLE_WRITE_FAILED`. The output directory is the
caller's responsibility and must be private.

### launchd

`install` writes `<launchd-root>/<service-label>.plist` with `RunAtLoad` and
`KeepAlive` with failed successful exits restarted by launchd. It reports
`launchd_loaded: false`. This worker does not load a plist into the user's real
launchd domain. The plist's program is the same service manager in `launchd`
mode; that mode allocates a fresh runtime socket directory, validates the
configured Host ready version against the real socket, and only then records the
running launchd-owned Host identity. The manager waits for that child while it
remains the service owner. SIGINT/SIGTERM for a persistent service first request
the same typed Host flush in the main thread; only a confirmed result terminates
the owned child and removes its owned socket runtime and state.

A launchd protocol rejection uses `terminate`/`wait` on the already spawned
child, removes its owned socket runtime, and writes a terminal `ready_rejected`
status with the configured error. The evidence state retains the launchd manager
PID, but `stop` refuses cleanup if that PID is live. This state never causes a
guessed process signal or runtime deletion. The main integration owner must
perform real `LaunchAgents` installation and `launchctl bootstrap` serially
during product acceptance, then collect separate install/restart and
deployed-entrypoint evidence.

While a live state is marked `launchd`, `stop` and `restart` refuse to send a
subprocess signal or start an unmanaged replacement. The integration owner must
first use the matching `launchctl bootout`; once the recorded PID is dead, the
manager may clean the stale runtime/state and start again.

## Endpoint refresh and the applied receipt

The service owns the endpoint process lifecycle and the refresh sequence. One
declared entry implements it exactly once:

```sh
scripts/host-service/host-service trust endpoint-refresh --root "$ROOT" --timeout 30
```

`refresh_endpoint` allocates the next generation in the existing
`trust/endpoint.json` locator, stops the previously recorded endpoint through the
same verified-PID lifecycle the Host uses, removes the superseded
`<previous>.json` receipt, starts `obscura-endpoint`, bounded-waits for the exact
applied receipt, and only then exposes `endpoint_state: "refreshed"` with the
endpoint PID and generation. The endpoint binary and `obscura-media` are resolved
beside the bound Host binary.

The endpoint argv is frozen (§7.4):

```text
--listen <host:port>
--host-dir <path>
--socket-dir <path>
--server-cert <trust>/server.der
--server-key  <trust>/server-key.der
--media-bin <path>
--ffmpeg <path>
--applied-receipt-path <runtime-root>/endpoint-applied/<generation>.json
--endpoint-generation <N>
```

`--client-ca`, `--authorized-clients`, and `--token` do not exist. The endpoint
does not compare the token; it forwards the header to the Host, which is the only
comparator.

The applied receipt has exactly four fields:

```json
{"schema_version": 1, "endpoint_generation": 2, "endpoint_pid": 12345,
 "bound_address": "192.168.0.3:51883"}
```

There is no `authorization_revision`, no `registry_sha256`, and no
`projection_sha256`. The consumer parses those exact bytes and verifies the
generation, the live PID, and the bound address. A missing, partial, stale,
corrupt, wrong-PID, or wrong-bind file stays an explicit `ENDPOINT_*` failure and
never becomes a synthetic ready receipt.

The started endpoint is recorded in the typed `run/endpoint-process.json`
process record (PID plus exact argv) so the next refresh and `stop` can stop only
the owned process. On any failure the entry exposes `endpoint_state:
"not_refreshed"` with typed partial facts (`last_step`, `cause`, and the
generation/PID/address observed so far); the typed `ENDPOINT_NOT_REFRESHED`,
`ENDPOINT_RECEIPT_MALFORMED`, `ENDPOINT_GENERATION_STALE`,
`ENDPOINT_PID_MISMATCH`, and `ENDPOINT_BIND_MISMATCH` identities are preserved.
Cancelling the bounded wait releases only the waiter; it never stops or cleans up
the endpoint process. A second refresh advances the generation again, stops the
previous endpoint PID, and removes the superseded receipt, so an old generation's
receipt can never satisfy a new generation. `stop` also stops the owned endpoint
and removes its process record and socket directory; a changed or foreign
endpoint PID is reported, never silently signaled or deleted.

The public locator keeps the accepted DNS name or IP literal because that is what
the certificate SAN must carry, but the endpoint producer only accepts a numeric
`SocketAddr` on `--listen`. The numeric bind is resolved and preflighted exactly
once, before any side effect: `endpoint_bind_address` resolves the locator and
`validate_endpoint_bind_address` confirms that one address is locally assignable
before the refresh advances the generation or stops the old endpoint. That one
selected value is passed into the start command, so preflight and start cannot
disagree. The applied-receipt bound-address verifier compares the receipt against
the actual `--listen` value recorded in the typed process record for that
generation; it never rebuilds the running bind from a new DNS query. A locator
that cannot become a local listener fails as `ENDPOINT_BIND_MISMATCH` with no
side effect.

The endpoint binary identity and exact argv are computed before the start. The
interval between a successful `Popen` and a persisted process record is guarded:
if the record write fails, the just-started child is stopped by its known PID and
argv, its own runtime directory is removed, and the entry returns the typed
failure.

## Verification

Two gates are live for this module. Both must be run against a real Host binary;
neither is satisfied by a fixture.

```sh
OBSCURA_HOST_BINARY=/absolute/path/to/obscura-host \
OBSCURA_HOST_PROTOCOL_VERSION=11 \
  scripts/host-service/verify-host-service
```

This drives the real subprocess matrix against the real binary: install, start,
status, restart, stop, duplicate start, version mismatch cleanup, failed-start
recovery, concurrent start, binary drift, foreign-PID protection, legacy config
projection, launchd supervisor ordering, and the persistent flush success/failure
cases. It prints `HOST_SERVICE_REAL_SUBPROCESS_PASS`. Because it drives
`install` -> `start` with no `trust init` anywhere, it is also the gate that
proves the §7.7 lifecycle order. It fails explicitly when
`OBSCURA_HOST_BINARY` is unset and `active/bin/obscura-host` does not exist.

```sh
python3 scripts/host-service/test_host_trust_public.py
```

This drives only the public CLI and the public module functions and asserts
external filesystem side effects. It prints `HOST_TRUST_PUBLIC_CONSUMER_OK`. It
covers: `install` creating the token at `0600` without inventing a locator and
without rotating on reinstall; `trust init` creating the exact four-file set and
being idempotent; the listener certificate's SAN, EKU, and `CA:FALSE` verified
with the real installed OpenSSL; the exact locator and bundle byte shapes and
private modes; `ENDPOINT_PORT_INVALID` before any write; the applied-receipt
waiter and bind-basis refusals; the refresh sequence; and the absence of the
removed ceremony from both the CLI surface and the module API.

The development fixture inside `test_host_service.py` additionally exercises
busy/storage-failed/unknown flush receipts, an owned Host that answers ready once
and then stops answering (`unresponsive_host_stop_contract`: the stop reports the
skipped flush, reaps the owned PID, and removes the state and runtime), and
launchd supervisor ordering without claiming a real Host. It does not load
launchd, install a real user service, touch Android/ADB resources, or claim full
M1 product acceptance.

The AppSDK build and regression bindings invoke the same resolver wrapper. It
uses `OBSCURA_HOST_BINARY` first, then `active/bin/obscura-host`; if neither
exists it fails explicitly instead of running a fake or incomplete test.

The AppSDK module build runs that real subprocess matrix, then emits a consumable
runtime bundle under `generated/modules/host-service/lib/` containing
`host-service` and `host_service.py`. The Obscura binary remains an external
engine artifact owned by Obscura and is not copied into this bundle.

The module build intentionally refuses to run without an explicit real Host
binary and protocol version. A source-only candidate may execute the same build
steps without the verifier: compile the manager sources, create
`generated/modules/host-service/lib`, copy the launcher and `host_service.py`
with preserved permissions, and record both source hashes. This proves only
source syntax and bundle construction, not Host lifecycle behavior.

For formal installed-bundle replay on macOS, commit the complete candidate on
its owner branch, select the validated AppSDK executable on `PATH`, and run:

```sh
OBSCURA_HOST_BINARY=/absolute/path/to/validated/obscura-host \
OBSCURA_HOST_PROTOCOL_VERSION=11 \
  node scripts/host-service/validate-admission.mjs
```

This adapter compiles the module, copies the emitted runtime bundle into a
private install directory, installs a unique private service configuration, and
bootstraps the user's real launchd domain with that bundle. It attaches through
the real Unix entrypoint, waits for the observer socket to close, checks that the
daemon Session survives, then restarts the exact service and waits for a new PID
and Session before reattaching. It boots out only its unique label and stops its
owned runtime; configuration and raw receipts remain under the recorded evidence
path. This private replay installation does not replace the user's default Host
service or establish full M1 acceptance.

## Ownership and non-goals

This module may change `scripts/host-service/**`, this document, and its declared
AppSDK ownership/verification bindings. It must not implement browser protocol
semantics, duplicate Host state, edit Obscura engine/Host source, own
`apps/macos` UI lifecycle, or infer profile/session state from logs, metadata,
request payloads, or client attach/detach events.

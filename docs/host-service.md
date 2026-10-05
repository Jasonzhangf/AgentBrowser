# Mac Host service entrypoint

Status: AgentBrowser-side local lifecycle entrypoint for the Obscura Host.
Owner: `scripts/host-service/`. This slice does not own the browser Session,
the Obscura engine, the direct endpoint, or the Mac UI.

## Runtime contract

The service starts the real Obscura Host binary with one fresh socket directory:

```text
obscura-host --socket-dir <fresh-private-directory> [--profile-root <canonical-absolute-directory>]
```

The current Host CLI also accepts the explicit local-test flag
`--allow-private-network`. The service passes it only when enabled in the typed
config. The explicit Host11 profile root is passed only when the service was
installed with `--profile-root`; the service never substitutes a default shared
root or modifies an existing profile directory. It does not start `obscura
serve`, create a second Session owner, or replace `obscura-endpoint`. The
endpoint is a separate transport process that connects to an already-running
Host.

Reinstalling without `--profile-root` reuses the canonical root already stored
in a v2 config. It never silently drops that root or modifies the existing
profile directory; switching to a different explicit root requires passing the
new root to `install`.

The Host itself owns `Session`, `Page`, attachment admission, operation identity,
viewport state, and `host.sock`/`frames.sock`. A client closing its Unix socket
only detaches that attachment. The Host process and its Page continue until the
service is explicitly stopped or the Host exits. Reconnecting to the same
daemon sees the same Session identity.

The Host ready response is checked against the expected protocol version bound
at install time. The expected value is a positive `u32` supplied by the
deployment owner as `--protocol-version`; it is stored in typed config and is
the only source used by ready validation and status projection. A parsed ready
response with another version fails immediately and the owned child/runtime are
cleaned through the normal startup failure path. The service does not infer the
expected version from the first ready response, logs, environment, or payloads,
and it does not retry another version.

Startup records the actual `session_id`, binary path, binary SHA-256, service
label, config ID, config hash, and configured protocol version in service state.
`status` reports the configured version as typed config projection; it never
reconstructs ownership or health from log text.

The Host ready response is also the first message on the local Unix maintenance
connection. For a persistent service, the manager sends the Host-local
`flush_profiles` request before stop or restart and requires the matching typed
`ProfileFlush` result with `flushed: true`. A busy, storage-failed, timed-out,
or otherwise unknown result fails explicitly; the manager does not signal the
Host, remove state, start a replacement, or report `saved`.

`status` validates a ready response through the same configured-version rule as
startup and rejects it when the ready session does not match the state. A dead
recorded PID is reported as `stale`; a mismatching ready response is reported
as terminal `ready_rejected`, both with a non-zero status exit. Neither terminal
state is interpreted as successful startup progress.

## Profile boundary

`profile_id` identifies the service configuration. The install argument
`--profile-root` is optional. With no explicit root, config version 2 projects
`profile_mode: in_memory` and `profile_root: null`; the Host runs one in-memory
profile and a restart creates a new Session identity. With an explicit root,
config and state project `profile_mode: persistent` and the canonical
`profile_root`; the service passes that exact root to the Host on both
subprocess and launchd starts.

The Host owns the profile catalog, profile selection, profile storage and the
meaning of a successful flush. The service owns only the process/config and
consumes the typed Host maintenance result before lifecycle termination. It
does not read profile files, infer saved state from logs or cookies, or treat
ordinary UI profile selection as a service configuration change or restart.

Config version 1 is accepted only as the legacy in-memory shape and is
normalized to an explicit null root for downstream projection. A v2 config must
contain `profile_root`; a persistent mode without a root, a root with in-memory
mode, a non-canonical root, or a root containing characters that cannot be
matched in the recorded argv fails explicitly. Credentials, bearer tokens, and
client certificates are not accepted as service config and are not written to
service logs or the launchd plist.

## Commands

The executable launcher is `scripts/host-service/host-service`.

Install binds one executable and writes private config, runtime/log roots, and
an unloaded launchd plist. The runtime root should use a short absolute path
because macOS limits Unix-domain socket path length. It must not contain
whitespace, quotes, or backslashes because lifecycle ownership compares the
Host's exact argv. The default is a private per-user directory under
`/private/tmp`.

```sh
scripts/host-service/host-service install \
  --root "$HOME/Library/Application Support/AgentBrowser/host-service" \
  --binary /absolute/path/to/obscura-host \
  --profile-root /absolute/private/profile-root \
  --protocol-version 11

scripts/host-service/host-service start --root "$HOME/Library/Application Support/AgentBrowser/host-service"
scripts/host-service/host-service status --root "$HOME/Library/Application Support/AgentBrowser/host-service"
scripts/host-service/host-service stop --root "$HOME/Library/Application Support/AgentBrowser/host-service"
scripts/host-service/host-service restart --root "$HOME/Library/Application Support/AgentBrowser/host-service"
```

`install` validates the executable's actual `--help` entrypoint, validates the
expected protocol version as a positive `u32`, records the binary SHA-256, and
refuses to start a changed binary until reinstalled. Duplicate starts fail with
the recorded PID. Lifecycle mutations share one lock, and `stop` revalidates the
verified PID immediately before both termination signals. The command line must
still contain the configured binary and exact socket directory; it never uses
process-name or process-group killing. Stale service state is cleaned only after
that PID is dead and the runtime directory matches the service-owned `host-*`
path shape.

Before any ownership check, signal, or runtime cleanup, `state.json` must match
the complete typed schema, including canonical binary/runtime/socket identities.
Malformed state fails explicitly and is never used to signal a PID or remove a
runtime directory.

For persistent service, `stop` sends the local `flush_profiles` request first.
Only a matching `ProfileFlush { flushed: true, profiles: <u32> }` permits the
existing verified-PID termination path. `restart` performs the same
stop-and-confirm sequence and then starts a fresh Host with the same explicit
root; it never reuses the old Session or epoch. A rejected or unknown
maintenance result leaves the running Host and its recorded state in place.

Startup records a typed `subprocess_starting` state before waiting for the Host
ready response, then replaces it with the Session-bearing state. Failed startup
uses the current child handle for terminate/wait; if that cleanup cannot be
verified, the starting record and runtime are retained for an explicit later
stop instead of abandoning an untracked Host.

Service and runtime directories are created private. Existing directories must
already be `0700`; the manager refuses a shared directory and never chmods it
as a side effect. The launchd plist directory is only checked for being a
directory, while the plist itself is always `0600`.

`status` distinguishes `stopped`, `running`, `starting`, `stale`,
`foreign_pid`, and `identity_mismatch`. A running result includes the current
PID, Session ID, protocol version, config identity, binary identity, and socket
permissions. It does not report a daemon as persistent merely because a socket
file or old log exists.

## launchd boundary

## Local trust authority

The service owns the Mac-local trust root. It is the only writer of the device
registry and of the derived authorized-client projection. The design contract
is `docs/runs/m1-first-connection-e2e-design-r2-20261003/implementation-contracts.md`
with its r9 correction note; the consumption boundary is
`docs/runs/m1-first-connection-profile-grant-design-r1-20261004/interface-handoff.md`.
This module implements only the Host-side trust primitives and the freeze API
for the future Mac `HostProvisioningBridge` and profile/endpoint consumers.

`trust/` under the service root holds the trust material:

```text
trust/
  ca.der                   0644  public CA certificate, Host-service-owned
  ca-key.der               0600  CA private key, never exported
  server.der               0644  endpoint server certificate with the requested SAN
  server-key.der           0600  server private key, never exported
  endpoint.json            0600  endpoint host/port recorded at init
  registry.json            0600  authorization truth: identity, scopes, initial grant
  authorized-clients.json  0600  derived projection of active clients only
  devices/<device-id>.json 0600  per-device client certificate and key material
```

`registry.json` uses `schema_version: 2`. Each device carries `device_id`,
`device_label`, `client_certificate_sha256`, the ordered `scopes` set, an
explicit `initial_profile_grant` with `selection` and `authorization_revision`,
`scope_revision`, `status`, lifetime, and creation/revocation timestamps. The
four legal scopes are `observe_human_control`, `profile_read`, `profile_create`,
and `profile_select`; an explicit empty set is legal and omitting the scope
argument is refused.
`initial_profile_grant.selection` is either `default_profile` or
`{"profile_id": "..."}`. No other shape is legal.

`authorization_revision` is a single monotonic counter for the whole registry.
Every mutable write (enroll, revoke, scope or selection change) increments it.
In the same atomic write, every still-active device's
`initial_profile_grant.authorization_revision` is rebound to the new counter,
while its selection, scopes, certificate identity, and `scope_revision` stay
unchanged. Only the device that the mutation targets advances its own
`scope_revision`. A rebind is derived freshness, not a permission grant, so
enrolling B never makes A's grant unreachable. Revoked devices stay revoked and
are excluded from the projection.

`authorized-clients.json` is a read-only derived projection. It contains
`schema_version: 2`, the current `authorization_revision`, the exact-byte
`registry_sha256`, and one entry per active device. The projection content never
carries a `projection_sha256` self-hash. The external `projection_sha256` is the
SHA-256 of the complete published UTF-8 file bytes, computed by the producer
after atomic publication and reported only through typed status or refresh
receipts. Install creates an empty revision-0 projection, so a deny-all endpoint
can start before the first device is enrolled.

Every public authorization mutation (`enroll_device` and `revoke_device`, and
the `trust provision`/`trust revoke` CLI wrappers that call them) holds the one
shared `run/.start.lock` across the complete registry read, mutation, registry
commit, and projection publication. The CLI wrappers do not take the lock
themselves, so a public module caller and a CLI caller serialize on the same
boundary and cannot overwrite each other's committed revision or revoke. A
`PARTIAL_REGISTRY_COMMIT` receipt still reports the committed revision after the
registry commit succeeds and projection publication fails.

`trust_status` is the single public status calculation behind both
`trust status` and `trust_summary`. It reads the registry file once, decodes and
validates those same bytes, and compares the published projection bytes to the
exact projection derived from the committed registry, so a mismatched client
set is detected, not just a mismatched header. `projection_state` reports the
publication fact (`missing`, `current`, or `stale`). `authorization_state`
reports endpoint-applied authorization and stays `not_refreshed` with
`endpoint_pid: null` until the endpoint owner supplies a current matching typed
receipt; the declared `deny_all` and `refreshed` values are never inferred from
file publication, a PID file, a health probe, or a log.

```sh
scripts/host-service/host-service trust init \
  --root "$HOME/Library/Application Support/AgentBrowser/host-service" \
  --endpoint-host 100.x.y.z --endpoint-port 8443
scripts/host-service/host-service trust status --root "$ROOT"
scripts/host-service/host-service trust provision --root "$ROOT" \
  --label "Android phone" \
  --scope observe_human_control --scope profile_read --scope profile_select \
  --initial-profile profile_id:<uuid>
scripts/host-service/host-service trust revoke --root "$ROOT" --device-id "<uuid>"
scripts/host-service/host-service trust export --root "$ROOT" \
  --device-id "<uuid>" --output "<path.abconnection>"
```

`trust init` generates the CA, the server certificate with the requested SAN,
the revision-0 registry, and the empty projection exactly once. A reinitialize
is refused with `TRUST_ALREADY_INITIALIZED`; it never overwrites identity or
material. `trust provision` reads the CA, generates one client key/certificate,
writes the per-device record, then commits the registry and projection in the
declared order. The one-time transfer code is a 16-character opaque value that
never contains `0`, `O`, `1`, `I`, or `L`; it is returned only through the
sensitive in-process API and is never written to files, argv, logs, the bundle,
or the registry.

`trust export` refuses with `PROFILE_CATALOG_UNAVAILABLE`,
`ENDPOINT_REFRESH_REQUIRED`, `PROJECTION_MATERIAL_CONFLICT`, or
`BUNDLE_NOT_READY` until a real Host catalog membership receipt and a real
current endpoint applied receipt exist. It also verifies that the published
projection is the exact current registry projection and that the receipts name
the enrolled device, profile, revision, and material. It does not create
substitute receipts to make the export succeed. The CLI export path emits only
non-sensitive fields; the transfer code stays in process. The typed errors are
`REGISTRY_SCOPE_REQUIRED`, `MISSING_INITIAL_PROFILE_GRANT`,
`AUTHORIZATION_REVISION_CONFLICT`, `REGISTRY_WRITE_FAILED`,
`PROJECTION_WRITE_FAILED`, and the `PARTIAL_REGISTRY_COMMIT` receipt, which
carries the actual committed revision and digests.

The connection bundle URL authority is built by `endpoint_ws_url`. It brackets
an accepted IPv6 literal (`wss://[fd7a:115c:a1e0::1]:8443/`) so the host and
port stay parseable, while the certificate SAN keeps the unbracketed address
(`fd7a:115c:a1e0::1`). DNS names and IPv4 literals are unchanged.

The projection module is the whole trust-authority API surface. The future Mac
`HostProvisioningBridge` maps its typed methods (`enrollDevice`, `revokeDevice`,
`refreshAuthorizedClients`, `exportBundle`) onto these functions. The Browser
wire and the Host catalog membership stay outside this module.

```sh
python3 scripts/host-service/test_host_trust_public.py
```

That public consumer drives only the CLI and the public module functions, then
verifies the CA, server SAN/EKU, and client certificate with the real installed
`openssl`, checks private modes, initialize/reinitialize identity preservation,
the A/B enroll and revoke revision matrix, invalid scope/selection refusal, the
exact external projection digest and absence of a self-hash, the declared empty
and nonempty vectors, an own-filesystem projection write failure that exposes
committed/partial truth, and a real PKCS#12 with exactly the three allowed
aliases. It also launches concurrent public module enrolls and CLI revocations
behind an external start barrier and asserts every successful receipt stays
committed, every revocation survives, the revision advances once per success,
every active grant is current, and no orphan device record is left. It checks
CLI/module status agreement for the published-but-not-applied and corrupted
projection cases, a real generated IPv6 certificate SAN and its bracketed URL,
and public `export_bundle` refusals for malformed/mismatched membership and
endpoint receipts, wrong registry/projection material, and stale receipts. It
prints private material only as public hashes.

Authenticated successful export and Android consumption remain `NOT_RUN` here:
they need the real Host catalog membership and endpoint applied receipts from
their owning modules. The public consumer verifies only malformed-input refusal
and never fabricates a positive receipt or treats a fixture as a usable bundle.

## Local admin authority and applied endpoint receipt

The service does not write the Host catalog or membership state. The Host
daemon owns `catalog.json` and exposes one local control resource:
`<socket_dir>/host-admin.sock` (`host-admin.sock`, one UTF-8 JSON object plus LF
per request/response, `id` u64, at most 64 KiB). This module provides the typed
client functions that future native provisioning and command boundaries use:

```python
admin_catalog_info(socket_path)
admin_catalog_create(socket_path, display_name, make_default, expected_catalog_revision)
admin_catalog_select(socket_path, profile_id, expected_catalog_revision)
admin_membership_set(socket_path, intent, profile_id, owner_device_id, device_id,
                     members, expected_catalog_revision)
```

`admin_membership_set` requires `intent` to be exactly `grant` or `remove`; it
never infers an intent. The client preserves the exact request/attempt identity,
rejects malformed, oversized, mismatched-id, wrong-schema, and unknown-field
responses with `ADMIN_FRAME_MALFORMED`, and propagates a Host denial with its
exact typed code. Transport errors, clean EOF, and partial EOF remain
`CATALOG_UNAVAILABLE` or `ADMIN_FRAME_MALFORMED`; they are never catalog success.

`wait_endpoint_applied(root, *, generation, timeout_seconds)` consumes exactly
one declared generation file
(`<runtime-root>/endpoint-applied/<generation>.json`). The v1 receipt has
`schema_version`, `endpoint_generation`, `endpoint_pid`, `bound_address`,
`authorization_revision`, `registry_sha256`, and `projection_sha256`, and no
`status` field. The method parses those exact bytes and verifies generation,
live PID, bound address, committed registry revision, and both registered
registry/projection digests before returning the service-only
`status: "refreshed"` projection. Missing, partial, stale, corrupt, wrong-PID,
bind, or material-mismatch files remain explicit `ENDPOINT_*` failures.

`command_trust_export` now obtains the real membership proof from the Host admin
owner and the verified endpoint result from the declared generation file before
calling `export_bundle`. It never accepts a guessed, removed, target-omitting,
or endpoint-file receipt. A missing live producer stays unavailable and refused
at the existing `PROFILE_CATALOG_UNAVAILABLE`, `ENDPOINT_REFRESH_REQUIRED`,
`ENDPOINT_NOT_REFRESHED`, or `PROJECTION_MATERIAL_CONFLICT` boundary.

The public consumer adds one-shot local admin transport harnesses and
`wait_endpoint_applied` fixtures for malformed/mismatched/stale/EOF refusal.
Those harnesses never yield a positive applied receipt, a successful export, or
blackbox acceptance.

## launchd boundary

`install` writes `<launchd-root>/<service-label>.plist` with `RunAtLoad` and
`KeepAlive` with failed successful exits restarted by launchd. It reports
`launchd_loaded: false`. This worker does not load a plist into the user's real
launchd domain. The plist's program is the same service manager in `launchd`
mode; that mode allocates a fresh runtime socket directory, validates the
configured Host ready version against the real socket, and only then records
the running launchd-owned Host identity. The manager waits for that child while
it remains the service owner. SIGINT/SIGTERM for a persistent service first
request the same typed Host flush in the main thread; only a confirmed result
terminates the owned child and removes its owned socket runtime and state.

A launchd protocol rejection uses `terminate`/`wait` on the already spawned
child, removes its owned socket runtime, and writes a terminal
`ready_rejected` status with the configured error. The evidence state retains
the launchd manager PID, but `stop` refuses cleanup if that PID is live. This
state never causes a guessed process signal or runtime deletion. The main
integration owner must perform real `LaunchAgents` installation and
`launchctl bootstrap` serially during product acceptance, then collect separate
install/restart and deployed-entrypoint evidence.

While a live state is marked `launchd`, `stop` and `restart` refuse to send a
subprocess signal or start an unmanaged replacement. The integration owner must
first use the matching `launchctl bootout`; once the recorded PID is dead, the
manager may clean the stale runtime/state and start again.

## Verification

Build an actual render-enabled Host binary in an owner-specific Obscura target,
then run the real subprocess matrix:

```sh
OBSCURA_TARGET=/tmp/obscura-host-service-target
CARGO_INCREMENTAL=0 CARGO_BUILD_JOBS=2 CARGO_TARGET_DIR="$OBSCURA_TARGET" \
  cargo build --release -p obscura-host --features render

OBSCURA_HOST_BINARY="$OBSCURA_TARGET/release/obscura-host" \
OBSCURA_HOST_PROTOCOL_VERSION=11 \
  scripts/host-service/verify-host-service
```

The test uses the actual binary and checks: missing executable, invalid profile
config, missing/invalid protocol version, configured ready/session identity,
version mismatch cleanup, duplicate start, client exit without Host exit,
normal stop/status, restart producing a new Session identity, and the
persistent stop/restart maintenance success/failure cases. The development
fixture additionally exercises busy/storage-failed/unknown flush receipts,
foreign-PID protection, legacy config projection, explicit-root argv matching,
and launchd supervisor ordering without claiming a real Host. It does not load
launchd, install a real user service, touch Android/ADB resources, or claim
full M1 product acceptance.

The AppSDK build and regression bindings invoke the same resolver wrapper. It
uses `OBSCURA_HOST_BINARY` first, then `active/bin/obscura-host`; if neither
exists it fails explicitly instead of running a fake or incomplete test.

The AppSDK module build runs that real subprocess matrix, then emits a
consumable runtime bundle under `generated/modules/host-service/lib/` containing
`host-service` and `host_service.py`. The Obscura binary remains an external
engine artifact owned by Obscura and is not copied into this bundle.

The module build intentionally refuses to run without an explicit real Host
binary and protocol version. A source-only candidate may execute the same
build steps without the verifier: compile the manager sources, create
`generated/modules/host-service/lib`, copy the launcher and `host_service.py`
with preserved permissions, and record both source hashes. This proves only
source syntax and bundle construction, not Host lifecycle behavior.

For formal installed-bundle admission on macOS, commit the complete candidate
on its owner branch, select the validated AppSDK executable on `PATH`, and run:

```sh
OBSCURA_HOST_BINARY=/absolute/path/to/validated/obscura-host \
OBSCURA_HOST_PROTOCOL_VERSION=11 \
  node scripts/host-service/validate-admission.mjs
```

This adapter compiles the module, copies and hashes the emitted runtime bundle,
installs a unique private service configuration, and bootstraps the user's real
launchd domain with that bundle. It attaches through the real Unix entrypoint,
waits for the observer socket to close, checks that the daemon Session survives,
then restarts the exact service and waits for a new PID and Session before
reattaching. It boots out only its unique label and stops its owned runtime;
configuration and raw receipts remain under the recorded evidence paths.
This private acceptance installation does not replace the user's default Host
service or establish full M1 acceptance. Existing admission records are preserved:
the adapter refuses to overwrite a candidate/validation graph.

The later real launchd window must add the missing cases without using
placeholders: install a compiled bundle whose explicit `--protocol-version` is
legal but does not match the Host binary, bootstrap the unique label, assert
non-zero `ready_rejected` plus owned process/socket/state cleanup; then repeat
with the matching binary/version and assert ready, observe attach, confirmed
profile flush, restart with a new Session/epoch, stop, and unique-label
bootout. The controller must provide the validated Host binary path and hash,
the positive protocol version 11, a legal mismatching version, an explicit
private profile root containing real store data, the launchd root and private
UID.

## Ownership and non-goals

This module may change `scripts/host-service/**`, this document, and its
declared AppSDK ownership/verification bindings. It must not implement browser
protocol semantics, duplicate Host state, edit Obscura engine/Host source,
own `apps/macos` UI lifecycle, or infer profile/session state from logs,
metadata, request payloads, or client attach/detach events.

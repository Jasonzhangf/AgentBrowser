# Mac Host service entrypoint

Status: AgentBrowser-side local lifecycle entrypoint for the Obscura Host.
Owner: `scripts/host-service/`. This slice does not own the browser Session,
the Obscura engine, the direct endpoint, or the Mac UI.

## Runtime contract

The service starts the real Obscura Host binary with one fresh socket directory:

```text
obscura-host --socket-dir <fresh-private-directory>
```

The current Host CLI also accepts the explicit local-test flag
`--allow-private-network`. The service passes it only when enabled in the typed
config. It does not start `obscura serve`, create a second Session owner, or
replace `obscura-endpoint`. The endpoint is a separate transport process that
connects to an already-running Host.

The Host itself owns `Session`, `Page`, attachment admission, operation identity,
viewport state, and `host.sock`/`frames.sock`. A client closing its Unix socket
only detaches that attachment. The Host process and its Page continue until the
service is explicitly stopped or the Host exits. Reconnecting to the same
daemon sees the same Session identity.

The Host ready response is checked at startup. This slice requires protocol
version `4` and records the actual `session_id`, binary path, binary SHA-256,
service label, config ID, and config hash in service state. `status` reports
that typed state; it never reconstructs ownership or health from log text.

## Profile boundary

`profile_id` identifies the service configuration. `profile_mode` is explicitly
`in_memory`. The current Obscura Host constructs its browser context without a
storage directory, so this entrypoint does not claim disk-profile, cookie,
localStorage, DOM, form, timer, or JavaScript persistence across a daemon
restart. A restart creates a new Host Session identity. Same-daemon reconnect
and process-restart persistence are separate claims.

The config schema rejects unknown keys and any profile mode other than
`in_memory`; credentials, bearer tokens, and client certificates are not
accepted as service config. They are not written to service logs or the launchd
plist.

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
  --binary /absolute/path/to/obscura-host

scripts/host-service/host-service start --root "$HOME/Library/Application Support/AgentBrowser/host-service"
scripts/host-service/host-service status --root "$HOME/Library/Application Support/AgentBrowser/host-service"
scripts/host-service/host-service stop --root "$HOME/Library/Application Support/AgentBrowser/host-service"
scripts/host-service/host-service restart --root "$HOME/Library/Application Support/AgentBrowser/host-service"
```

`install` validates the executable's actual `--help` entrypoint, records its
SHA-256, and refuses to start a changed binary until reinstalled. Duplicate
starts fail with the recorded PID. Lifecycle mutations share one lock, and
`stop` revalidates the verified PID immediately before both termination
signals. The command line must still contain the configured binary and exact
socket directory; it never uses process-name or process-group killing. Stale
service state is cleaned only after that PID is dead and the runtime directory
matches the service-owned `host-*` path shape.

Before any ownership check, signal, or runtime cleanup, `state.json` must match
the complete typed schema, including canonical binary/runtime/socket identities.
Malformed state fails explicitly and is never used to signal a PID or remove a
runtime directory.

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

`install` writes `<launchd-root>/<service-label>.plist` with `RunAtLoad` and
`KeepAlive`, but reports `launchd_loaded: false`. This worker does not load a
plist into the user's real launchd domain. The plist's program is the same
service manager in `launchd` mode; that mode allocates a fresh runtime socket
directory and then `exec`s the configured Host binary. The main integration
owner must perform real `LaunchAgents` installation and `launchctl bootstrap`
serially during product acceptance, then collect separate install/restart and
deployed-entrypoint evidence.

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
  scripts/host-service/verify-host-service
```

The test uses the actual binary and checks: missing executable, invalid profile
config, private file modes, protocol-v4 ready/session identity, duplicate
start, client exit without Host exit, normal stop/status, and restart producing
a new Session identity. It does not load launchd, install a real user service,
touch Android/ADB resources, or claim full M1 product acceptance.

The AppSDK build and regression bindings invoke the same resolver wrapper. It
uses `OBSCURA_HOST_BINARY` first, then `active/bin/obscura-host`; if neither
exists it fails explicitly instead of running a fake or incomplete test.

The AppSDK module build runs that real subprocess matrix, then emits a
consumable runtime bundle under `generated/modules/host-service/lib/` containing
`host-service` and `host_service.py`. The Obscura binary remains an external
engine artifact owned by Obscura and is not copied into this bundle.

## Ownership and non-goals

This module may change `scripts/host-service/**`, this document, and its
declared AppSDK ownership/verification bindings. It must not implement browser
protocol semantics, duplicate Host state, edit Obscura engine/Host source,
own `apps/macos` UI lifecycle, or infer profile/session state from logs,
metadata, request payloads, or client attach/detach events.

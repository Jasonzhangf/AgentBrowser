# AB connection Host11/WebRTC10 migration notes

Run: `m1-connection-host11-migration-r1-20261003`
Author: fresh GCM implementation author
Branch: `codex/m1-connection-host11-migration-r1-20261003`

## Bindings

| Item | Value |
| --- | --- |
| migration-base source SHA | `978ec570846d8e02b794f8760f9c338a5223f3c1` |
| migration-base tree | `350899559d03ffa8fc02c3b397e6f3bd5fb8dc81` |
| base `origin/main` | `bc9d67ad037be2c7119bee692f9ad362417ffd05` |
| frozen protocol root | `/Volumes/Intel/playground/obscura/m1-profile-navigation-impl-r1-20261003/protocol/browser` |
| protocol `src/lib.rs` SHA256 | `2344ad88b52e34af1e8d6ccbd6de998770bbeca080e6ec57cd0e047aeb6edab9` |
| protocol versions | Host11 / WebRTC10 |
| AB cargo target lease | `/Volumes/Intel/playground/agentbrowser/m1-relay-host-version-r1-20261003/target` |
| build environment | `CARGO_INCREMENTAL=0`, `CARGO_BUILD_JOBS=2` |

Runtime Host/endpoint/WebRTC/Relay E2E is not granted in this task. The
shared Obscura binaries are mutable and must not be read or run here.

## Node log

### 2026-10-03 context binding

- Read the task contract, global/project `AGENTS.md`,
  `coding-principals`, the AppSDK project governance Skill, the existing W6
  source contract/notes, the frozen Host11 protocol, and current
  `packages/client-connection` callers.
- Confirmed a clean frozen candidate at `978ec570` / tree `35089955`.
  Existing generic and workspace callers are Android bridge, macOS bridge,
  Relay host, and admission, but this migration's write scope is the
  connection module and its tests.
- Evidence: this file, task contract, frozen protocol source. Next: red
  compile boundary.

### 2026-10-03 red compile boundary

- Command:
  `CARGO_TARGET_DIR=/Volumes/Intel/playground/agentbrowser/m1-relay-host-version-r1-20261003/target CARGO_INCREMENTAL=0 CARGO_BUILD_JOBS=2 python3 scripts/connection.py check --protocol-root /Volumes/Intel/playground/obscura/m1-profile-navigation-impl-r1-20261003/protocol/browser`
- Result: raw exit `101`.
  `error[E0063]: missing fields presentation_revision and tab_id in
  initializer of PresentedFrameIdentity` at
  `packages/client-connection/src/transport.rs:792`.
- Evidence: `red-build.log`, `red-build.exit`. Next: implement the minimal
  Host11 field/API migration and public parsing tests.

### 2026-10-03 Host11 implementation and no-Host verification

- `DisplayedFrame` and `PresentedFrame` now carry the Host-owned `tab_id` and
  `presentation_revision`; `PresentedFrameIdentity` sends them unchanged.
  `MediaSequence` fences tab/presentation regressions, while the existing
  session/document/viewport continuity remains.
- Added `Connection::back`, `forward`, `reload`, `tab_create`, `tab_select`,
  `tab_close`; all use the same status-derived operation identity and control
  epoch path as `navigate`. Added read-only `Connection::profile_state` for the
  authoritative navigation/tab/profile projection.
- Profile catalog/create/select remain typed Host11 protocol results and are
  not exposed as remote commands; the Host endpoint's current
  `REMOTE_COMMAND_FORBIDDEN` scope is preserved. `FlushProfiles` remains
  Host-local maintenance and is not exposed by `Connection`.
- Focused no-Host command (rerun against the final source):
  `cargo nextest run --release -p agentbrowser-connection --lib --test framing`
  bound to the frozen protocol patch; raw exit `0`, `17 tests run: 17 passed`.
  Evidence: `no-host-test.log`, `no-host-test.exit`.
- Workspace consumer check (rerun against the final source):
  `cargo check --workspace --release` bound to the same protocol patch; raw
  exit `0`. This compiles `android-bridge`, macOS connection bridge, and
  Relay host consumers that only needed the explicit frame fields.
  Evidence: `workspace-check.log`, `workspace-check.exit`.
- Official `check` (rerun against the final source): raw exit `0`.
  Evidence: `check.log`, `check.exit`.
- Official build (rerun against the final source):
  `python3 scripts/connection.py build --protocol-root <frozen root>`; raw exit
  `0`. Evidence: `build.log`, `build.exit`.

Artifact SHA256:

| Artifact | SHA256 |
| --- | --- |
| `libagentbrowser_connection.rlib` | `c203ed5b8483bd6edd84e8ab222c821ca29e2ba36e1654e3394913a9a9effb39` |
| `connection-acceptance` | `68b4dc4f426ad1b709067716ccbea92f5c829ab6eef5856cffee83f2f8c11429` |
| `webrtc-acceptance` | `63041ac5a6899559d0b0aa841f36f72a4879517da0921fc8d128279135a13c65` |
| `relay-acceptance` | `28b80d568821f5eb7556f6a3ca322ba452a3643a328cd9cbf3461323ac12862d` |
| `relay-connection-acceptance` | `6423bfa4939e9a43a303ea640abe9d9d62fa3b78609d1be1106b640618689be8` |

Artifacts were copied by the official build to
`generated/modules/client-connection/lib/` (git-ignored development output)
from the AB cargo target leased at
`/Volumes/Intel/playground/agentbrowser/m1-relay-host-version-r1-20261003/target`.

Evidence SHA256:

| Evidence | SHA256 |
| --- | --- |
| `red-build.log` | `d4f8a3280c3d7c6c6a9233de76977940fec5fe5ce7740984b95ec98d9a8d3eeb` |
| `check.log` | `90f2d0b160e7a7ed7ba84e649c493c4e599666b8396378e4abf299d3882210c0` |
| `no-host-test.log` | `bad5667b2b18bd0f4e4511449143a9bcbb186f130ed4fd4e8e3f4cabfb5912bd` |
| `workspace-check.log` | `f5610f4872428687ebc1c58f057affba68ab432064bb04536a654aa46456c1be` |
| `build.log` | `403998d7813def59ff54ef8942a37a928b6a42f3aea6f4660bbd1cdb0d4ebac8` |

Caller migration:

- `packages/android-bridge/src/lib.rs`: frame-to-`DisplayedFrame` initializer
  now copies `source.tab_id` and `source.presentation_revision`.
- `apps/macos/connection-bridge/src/main.rs`: acknowledged-frame
  `DisplayedFrame` initializer likewise copies the Host fields.
- `packages/client-connection/tests/{endpoint,webrtc,relay_connection}.rs`:
  test consumers construct the expanded frame identity from `FrameInfo`.
- `apps/relay-host` and Relay protocol code are unchanged; the Relay-host
  consumer compiles against the migrated connection crate and still awaits a
  separate real paired runtime grant.

State: `SOURCE_BUILT` / `E2E_WAIT`. No real Host, endpoint, WebRTC, Relay,
device, install, restart, merge, push, review, or runtime replay was performed;
the shared Obscura binaries were not read or run. The leased target and
worktree are retained for the controller's paired runtime acceptance.

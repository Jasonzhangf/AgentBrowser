# m1-service-endpoint-refresh-r3 - worker notes

Worker: fresh standalone GCM author (not controller, not reviewer). Scope:
close exactly the three P1 findings and one P2 finding from review
`m1-service-endpoint-refresh-r1-20261005-r2` on candidate
`7d5e46ba28eb7ae3f299d1a5fdf0bc93bbe0a54d`.

## A1 admission (verified from this environment)

- Worktree: `/Volumes/Intel/playground/agentbrowser/m1-service-endpoint-refresh-r1-20261005`
- `git rev-parse HEAD` = `7d5e46ba28eb7ae3f299d1a5fdf0bc93bbe0a54d` (matches the
  declared base).
- `git rev-parse HEAD^{tree}` = `ecd05e48b8eec3dff775e5aa611d58028cbfce9a`
  (matches the declared tree).
- Branch = `codex/m1-service-endpoint-refresh-r1-20261005` (matches).
- `git status --porcelain` showed one untracked file only:
  `docs/runs/m1-service-endpoint-refresh-r1-20261005/r2/raw/real_refresh_evidence/device.abconnection`
  (a predecessor-round artifact, left untouched). No tracked file was modified.
- `core.hooksPath` = `.githooks`; no commit/reset/rebase/checkout is performed by
  this node.
- Real binaries (protocol version 11):
  - `obscura-host`     sha256 `6335a174287c917f7947f080a6087f7dcb8f613de361baf11db325cb59b23dab`
  - `obscura-endpoint` sha256 `4c281713e6dae8918cbdafd255f527b2e3c02bc01bd7c42b232f0c0eee0f9318`
  - `obscura-media`    sha256 `2ee0166fa4b45b634bce9dc6d51ea132da5b3e7d902e00fd3980e4cfeea9af80`
  - directory `/Volumes/Intel/playground/obscura/m1-engine-reconcile-20261002/target/release`

## Node conclusions (one per finding)

- R2-P1-a - one declared bind basis. `refresh_endpoint` now resolves and
  preflights the numeric bind once (`validate_endpoint_bind_address`) and passes
  that selected value into the start command. `endpoint_listen_address` and
  `endpoint_command` take the already-resolved `(host, port)` instead of
  re-resolving the locator. The applied-receipt verifier compares the receipt's
  `bound_address` against the actual `--listen` value in the typed process
  record (`_declared_endpoint_listen`), not a fresh DNS query. Public DNS URL
  and SAN semantics are unchanged. Pair: `raw/p1a_{before,after}`.
- R2-P1-b - acceptance boundary. The `verify_applied` step now catches
  `(ServiceError, OSError)` and routes through `_endpoint_refresh_failure`, so a
  real `OSError` from the registry/projection reads becomes typed
  `ENDPOINT_NOT_REFRESHED` with `authorization_state: not_refreshed`,
  `last_step: verify_applied`, and the already-changed generation and recorded
  endpoint PID. The verifier's own read sites were also converted to typed
  errors. Pair: `raw/p1b_{before,after}`.
- R2-P1-c - real persistence failure. A private test root makes the record's own
  parent directory reject new entries (`chflags UF_IMMUTABLE`), so the
  unmodified product writer fails on a real `mkstemp`/`replace` inside
  `run/endpoint-process.json`. No internal call is replaced. The started PID
  exits, the listener is released, and no process record or endpoint runtime
  remains. Pair: `raw/p1c_{before,after}`.
- R2-P2 - short-runtime reclaim. `test_refresh_missing_binary_typed_failure`
  now manages the `r2mb-*` runtime with an outer `try/finally` that
  `shutil.rmtree`s it on both the success and the failure exit, keeping the
  short Unix-socket path. Pair: `raw/p2_{before,after}`.

## Evidence index (see raw/commands.md for exact commands)

- Finding pairs: `raw/p1a_{before,after}.*`, `raw/p1b_{before,after}.*`,
  `raw/p1c_{before,after}.*`, `raw/p2_{before,after}.*` (after arms exit 0,
  before arms exit 1 with the named assertion failure).
- Public consumer: `raw/accept_public_consumer.*` (exit 0)
- Dev fixture matrix: `raw/accept_dev_fixture_matrix.*` (exit 0)
- Real subprocess matrix: `raw/accept_verify_host_service.*` (exit 0)
- npm verify:local: `raw/accept_npm_verify_local.*` (exit 0)
- git diff --check: `raw/git_diff_check.*` (exit 0)
- Real refresh harness: `raw/accept_real_refresh_e2e.*` (exit 0, 30/30 ok)
- Real refresh artifacts: `raw/real_refresh_evidence/`

## NOT_EXECUTED (owned by the controller / later nodes)

- installation, daemon restart, OTA, merge, push, remote receipt
- independent architecture review (this node only supplies candidate evidence)
- the downstream service real admin E2E fix node, Mac, Android, first-user delivery

## Resource note

Scratch copies under `/private/tmp/m1r3-*` and the finding harness bases
(`/private/tmp/m1r3-p1*/` and `/private/tmp/m1r3-p2*/`) belong to this node; the
controller may remove them. No shared Obscura binary, other worktree, or another
owner's process was touched. The r3 harness's P2 case deletes any runtime it
observes leak so the leak cannot accumulate across runs. Importing the service
module writes `scripts/host-service/__pycache__/`; this node removed its own copy
so the working tree holds only the three edited files plus the new `r3/`
directory. That path is untracked and not covered by `.gitignore` (out of this
node's write scope), so a re-run of the suites will recreate it; the controller
should add only the candidate paths when committing.

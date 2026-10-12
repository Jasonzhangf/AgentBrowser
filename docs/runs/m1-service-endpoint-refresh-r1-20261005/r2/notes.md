# m1-service-endpoint-refresh-r2 - worker notes

Worker: fresh standalone GCM author (not controller, not reviewer). Scope:
close exactly the five P1 findings from review
`m1-service-endpoint-refresh-r1-20261005-r1` on candidate
`8cd4315459cd47b12bfdb091a94543f5c3e35dbb`.

## A1 admission (verified from this environment)

- Worktree: `/Volumes/Intel/playground/agentbrowser/m1-service-endpoint-refresh-r1-20261005`
- `git rev-parse HEAD` = `8cd4315459cd47b12bfdb091a94543f5c3e35dbb` (matches)
- Branch = `codex/m1-service-endpoint-refresh-r1-20261005` (matches)
- `git status --porcelain` was clean at admission
- `node_modules` present, so `npm run verify:local` runs
- Source tree at admission: `cb8606e65b0502911397bb917f0f91ab7eab110a`
- Real binaries (protocol version 11):
  - `obscura-host`     sha256 `6335a174287c917f7947f080a6087f7dcb8f613de361baf11db325cb59b23dab`
  - `obscura-endpoint` sha256 `4c281713e6dae8918cbdafd255f527b2e3c02bc01bd7c42b232f0c0eee0f9318`
  - `obscura-media`    sha256 `2ee0166fa4b45b634bce9dc6d51ea132da5b3e7d902e00fd3980e4cfeea9af80`
  - directory `/Volumes/Intel/playground/obscura/m1-engine-reconcile-20261002/target/release`

## Node conclusions (one per finding)

- F1 - export re-grants removed membership: `_export_membership_receipt` now
  consumes the committed member set and refuses with
  `PROFILE_ACCESS_WRITE_FAILED` when the enrolled target is missing or removed.
  It never inserts the target. Only an explicit authorized grant entry point
  submits a set containing the target.
- F2 - DNS locator projected into `--listen`: `endpoint_bind_address` resolves
  the public locator to one numeric binding basis; `validate_endpoint_bind_address`
  confirms it is locally assignable before the generation advances or the old
  endpoint stops; the start command and the receipt verifier share that basis.
  The DNS WSS URL and SAN semantics are unchanged.
- F3 - unclosed start/persist boundary: binary identity and argv are computed
  before `Popen`; a failure between start and a persisted process record stops
  the known child, removes its own runtime, and returns a typed failure. After
  the record exists the waiter cancellation contract holds.
- F4 - missing typed partial facts: every refresh step boundary now wraps
  `ServiceError`/filesystem/process failures with `_endpoint_refresh_failure`,
  preserving the original typed code and adding `authorization_state:
  not_refreshed`, `last_step`, and the already-changed generation/PID.
- F5 - cancel case performed no cancel: `real_refresh_e2e.py` CASE 9 now starts
  a real public waiter holding the lifecycle lock, hides the receipt, sends
  `SIGINT`, and asserts waiter exit, same endpoint PID alive, record/runtime
  survival, and lock release.

## Evidence index (see raw/commands.md for exact commands)

- Finding pairs: `raw/f{1..5}_{before,after}.log` + `.exit` (all exit 0)
- Public consumer: `raw/public_consumer.*` (exit 0)
- Dev fixture matrix: `raw/dev_fixture_matrix.*` (exit 0)
- Real subprocess matrix: `raw/real_subprocess_matrix.*` (exit 0)
- npm verify:local: `raw/npm_verify_local.*` (exit 0)
- git diff --check: `raw/git_diff_check.*` (exit 0)
- Real refresh harness: `raw/real_refresh_e2e.*` (exit 0, 30/30 cases ok)
- Real refresh artifacts: `raw/real_refresh_evidence/`
- Predecessor green logs preserved at `raw/iteration-1/`
- Kept red: `raw/git_diff_check_badpath.*` (wrong `-C` path, exit 129)

## NOT_EXECUTED (owned by the controller / later nodes)

- installation, daemon restart, OTA, merge, push, remote receipt
- independent architecture review (this node only supplies candidate evidence)
- the downstream service real admin E2E fix node, Mac, Android, first-user delivery

## Resource note

Scratch copies under `/private/tmp/m1r2-*` and
`/private/tmp/m1r2-before-service-1791329249` are this node's own; the
controller may leave or remove them. No shared Obscura binary, other worktree,
or another owner's process was touched.

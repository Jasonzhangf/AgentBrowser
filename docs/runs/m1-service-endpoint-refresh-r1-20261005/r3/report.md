# m1-service-endpoint-refresh-r3 - report

## Identity

- Node: `m1-service-endpoint-refresh-r3-20261005`, standalone GCM author
- Base candidate: `7d5e46ba28eb7ae3f299d1a5fdf0bc93bbe0a54d`
  (tree `ecd05e48b8eec3dff775e5aa611d58028cbfce9a`)
- Branch: `codex/m1-service-endpoint-refresh-r1-20261005`
- Review closed: `m1-service-endpoint-refresh-r1-20261005-r2`
  (FAIL, three P1, one P2, zero P0)

## Delta (per-file sha256, working tree)

| Path | sha256 |
| --- | --- |
| `scripts/host-service/host_service.py` | `69e5b86d4074c0c359cfcdc6309b34d96baaf305a4fe1c214610fff609310745` |
| `scripts/host-service/test_host_trust_public.py` | `927b4e8e1ed28c9064688a67a25ccbe071a33c1757a59934ee0043410f528332` |
| `docs/host-service.md` | `529cdf3e206f4af07e9a9aa4647627489b66f568d3630077e362597bae293d7e` |
| `docs/runs/m1-service-endpoint-refresh-r1-20261005/r3/findings_harness.py` | `84c3d53693010c93069147109c3c00c14ea016f83e805ee14e0585149cee2f38` |
| `docs/runs/m1-service-endpoint-refresh-r1-20261005/r3/run.sh` | `d89fa4879664072ef2bd7332d741b705e3576a78d565fde443f86e27693949e0` |

Before-candidates used by the pairs:

| Candidate | Path | sha256 |
| --- | --- | --- |
| r1 (for P1-c) | `/private/tmp/m1r3-before-r1-service/host_service.py` | `43d1e41a7b110cb23ace5a02426d1ccf379b099f1190e3049db2760b21e1633e` |
| r2 (for P1-a/P1-b/P2) | `/private/tmp/m1r3-before-r2-service/host_service.py` | `c678ab15f929e5c73c29123b34f92edac13592341a00412ac3a493348f8ce342` |

Real binaries (protocol version 11):

| Binary | sha256 |
| --- | --- |
| `obscura-host` | `6335a174287c917f7947f080a6087f7dcb8f613de361baf11db325cb59b23dab` |
| `obscura-endpoint` | `4c281713e6dae8918cbdafd255f527b2e3c02bc01bd7c42b232f0c0eee0f9318` |
| `obscura-media` | `2ee0166fa4b45b634bce9dc6d51ea132da5b3e7d902e00fd3980e4cfeea9af80` |

## Observed behavior per finding (real public entry, before/after)

### R2-P1-a - one declared bind basis

- Fix: `refresh_endpoint` resolves and preflights the numeric bind once and
  passes that selected value into `endpoint_command`; the applied-receipt
  verifier compares `bound_address` against the actual `--listen` value in the
  typed process record instead of re-resolving the locator.
- Pair: `raw/p1a_before.*` (exit 1) / `raw/p1a_after.*` (exit 0). The harness
  runs a real refresh with a DNS locator `rot.test` whose injected resolver
  answers `127.0.0.1, 127.0.0.1, 127.0.0.2` on successive calls.
- Before (r2): the endpoint binds the assignable `127.0.0.1` and writes a
  correct receipt, but the third resolution returns `127.0.0.2`; the correct
  receipt is rejected as `ENDPOINT_BIND_MISMATCH` (`last_step: verify_applied`)
  and `trust status` reports `not_refreshed` (`rc 3`).
- After (candidate): `rc 0`, `authorization_state: refreshed`, `bound_address`
  equals the preflighted `127.0.0.1:<port>`, the generation-1 receipt exists,
  and `trust status` reports `refreshed` with the same endpoint PID. The DNS
  locator is preserved (`endpoint_host: rot.test`, `endpoint_host_kind: dns`).

### R2-P1-b - typed acceptance failure on a real filesystem read error

- Fix: the acceptance step catches `(ServiceError, OSError)` and routes through
  `_endpoint_refresh_failure`, preserving the code/chain and adding
  `authorization_state`, `last_step`, generation and endpoint PID.
- Pair: `raw/p1b_before.*` (exit 1) / `raw/p1b_after.*` (exit 0). A watchdog
  flips `trust/registry.json` to `chmod 000` exactly after the process record is
  published, so the acceptance read fails for real while the new endpoint is
  already started and recorded.
- Before (r2): the `OSError` (the raw `PermissionError`) escapes as an untyped
  traceback (`rc 1`, no JSON error); the caller gets no typed facts.
- After (candidate): `rc 3` with typed
  `ENDPOINT_NOT_REFRESHED` `last_step: verify_applied`,
  `authorization_state: not_refreshed`, `endpoint_generation: 1`, and the real
  recorded `endpoint_pid`. The lifecycle lock re-acquires (released), and the
  registered endpoint is still alive and recorded.

### R2-P1-c - real process-record write failure

- Fix: the real product writer is exercised; no internal call is replaced.
- Pair: `raw/p1c_before.*` (exit 1) / `raw/p1c_after.*` (exit 0). A private test
  root makes `run/` reject new entries with `chflags UF_IMMUTABLE`, so
  `write_json` fails on a real `mkstemp`/`replace` for
  `run/endpoint-process.json`.
- Before (r1, before the r2 reclaim guard): the raw `PermissionError` escapes,
  one orphan `obscura-endpoint` process remains, the endpoint runtime directory
  remains, and the port is still bound.
- After (candidate): typed `ENDPOINT_NOT_REFRESHED`
  (`last_step: persist_endpoint_record`, `cause: endpoint_record_write_failed`,
  generation 1, recorded PID), the started PID has exited, the listener is
  released (`127.0.0.1:<port>` rebindable), and no process record, endpoint
  runtime directory, or orphan process remains.

### R2-P2 - reclaim the temporary runtime

- Fix: `test_refresh_missing_binary_typed_failure` wraps the `r2mb-*` short
  runtime in `try/finally` and removes it on both exits.
- Pair: `raw/p2_before.*` (exit 1) / `raw/p2_after.*` (exit 0). The harness runs
  that one consumer case against a private root and lists new `r2mb-*`
  directories under `/private/tmp`.
- Before (r2): one `r2mb-*` directory is left behind.
- After (candidate): no new `r2mb-*` directory is left behind.

## Verification (all exit 0 unless noted)

| Command | Log | Exit |
| --- | --- | --- |
| `test_host_trust_public.py` | `raw/accept_public_consumer.*` | 0 |
| `test_host_service.py` | `raw/accept_dev_fixture_matrix.*` | 0 |
| `verify-host-service` (real subprocess) | `raw/accept_verify_host_service.*` | 0 |
| `real_refresh_e2e.py` (30/30 cases) | `raw/accept_real_refresh_e2e.*` | 0 |
| `npm run verify:local` | `raw/accept_npm_verify_local.*` | 0 |
| `git diff --check` | `raw/git_diff_check.*` | 0 |
| finding pairs after | `raw/p1a_after.*`, `raw/p1b_after.*`, `raw/p1c_after.*`, `raw/p2_after.*` | 0 |
| finding pairs before (expected red) | `raw/p1a_before.*`, `raw/p1b_before.*`, `raw/p1c_before.*`, `raw/p2_before.*` | 1 |

## Remaining NOT_EXECUTED

Installation, daemon restart, OTA, merge, push, remote receipt, and the
independent architecture review are outside this node and belong to the
controller. The downstream service real admin E2E fix node, Mac, Android, and
first-user delivery are also out of scope.

## Controller next action

1. Fetch latest `origin/main`; if it moved, rebase the candidate and rerun the
   affected checks.
2. Run the independent architecture review on this candidate; it must cover the
   three P1 and one P2 finding and confirm no accepted behavior regressed.
3. On PASS, commit the delta, merge to `main`, push, and verify the remote
   receipt. Do not install/restart/OTA from this node's evidence.

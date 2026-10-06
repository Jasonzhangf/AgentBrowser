# m1-service-endpoint-refresh-r2 - report

## Identity

- Node: `m1-service-endpoint-refresh-r2-20261005`, standalone GCM author
- Base candidate: `8cd4315459cd47b12bfdb091a94543f5c3e35dbb`
- Source tree: `cb8606e65b0502911397bb917f0f91ab7eab110a`
- Branch: `codex/m1-service-endpoint-refresh-r1-20261005`
- Review closed: `m1-service-endpoint-refresh-r1-20261005-r1`
  (FAIL, five P1, no P0)

## Delta (per-file sha256, working tree)

| Path | sha256 |
| --- | --- |
| `scripts/host-service/host_service.py` | `c678ab15f929e5c73c29123b34f92edac13592341a00412ac3a493348f8ce342` |
| `scripts/host-service/test_host_trust_public.py` | `05f0c2d076c830482218ec33f8f9da218b5e2ba6daf78862b4c218f592539239` |
| `scripts/host-service/verify-host-service` | `c82246bada9a12ba8166151e15a0cff4f9128e7a4883e51b40c3bfa5bf7a6be5` (unchanged) |
| `docs/host-service.md` | `b4b626af3d998ea61ba6fa38ccd625862b120aaf5e6bd9b1df9e97b3938d18a3` |
| `docs/runs/m1-service-endpoint-refresh-r1-20261005/real_refresh_e2e.py` | `88e8b52e81e47b2f60062a5574cd0f43f47cbc97c4ef74304320057f3f9a8a8c` |
| `docs/runs/m1-service-endpoint-refresh-r1-20261005/r2/findings_harness.py` | `1d37e8699c335893f6d223f8bd3a3da188778ba2bee7ccc8906e0ab9fa896d95` |
| `docs/runs/m1-service-endpoint-refresh-r1-20261005/r2/run.sh` | `d89fa4879664072ef2bd7332d741b705e3576a78d565fde443f86e27693949e0` |

Real binaries (protocol version 11):

| Binary | sha256 |
| --- | --- |
| `obscura-host` | `6335a174287c917f7947f080a6087f7dcb8f613de361baf11db325cb59b23dab` |
| `obscura-endpoint` | `4c281713e6dae8918cbdafd255f527b2e3c02bc01bd7c42b232f0c0eee0f9318` |
| `obscura-media` | `2ee0166fa4b45b634bce9dc6d51ea132da5b3e7d902e00fd3980e4cfeea9af80` |

## Observed behavior per finding

### F1 - export must not re-grant a removed membership

- Before (r1): after a public `admin.membership.set(intent=remove)`, `trust
  export` returned rc 0, wrote a bundle, and changed the catalog from
  `{revision 3, members [local]}` to `{revision 4, members [<device>, local]}`.
  A target-omitting grant produced the same re-grant
  (`raw/f1_before.log`).
- After (candidate): the same removal/omission leaves `trust export` at
  `PROFILE_ACCESS_WRITE_FAILED` rc 3 with the catalog members and revision
  unchanged (`raw/f1_after.log`). `case5b.removed_cli_refused` in the real
  harness also drives the public CLI and asserts the catalog is unchanged.

### F2 - DNS locator must reach the real endpoint

- Before: a DNS `endpoint_host=localhost` refresh projected `--listen
  localhost:<port>`; the real endpoint exited 2 with `invalid socket address
  syntax`, and the generation had already advanced to 1
  (`raw/f2_before.log`).
- After: the same DNS locator refresh completes with `authorization_state:
  refreshed` and a numeric `bound_address`; the WSS URL keeps
  `wss://localhost:<port>/`. A rejected IP (`203.0.113.7`) fails as
  `ENDPOINT_BIND_MISMATCH` with `last_step: resolve_bind_address` before the
  generation advances and while the old endpoint is still alive
  (`raw/f2_after.log`).

### F3 - close the start/persist boundary

- Before: an injected `write_json` failure on the endpoint process record left
  one orphan `obscura-endpoint` process and no record
  (`raw/f3_before.log`).
- After: the same failure returns typed `ENDPOINT_NOT_REFRESHED` with
  `last_step: persist_endpoint_record`, the started PID is dead, and no orphan
  process remains (`raw/f3_after.log`).

### F4 - typed partial facts on failure

- Before: a missing sibling `obscura-endpoint` produced untyped text after the
  generation had advanced to 1 (`raw/f4_before.log`).
- After: the same case returns `ENDPOINT_NOT_REFRESHED` with facts
  `authorization_state: not_refreshed`, `last_step: start_endpoint`, and
  `endpoint_generation: 1` (`raw/f4_after.log`).

### F5 - real cancel, not a timeout

- Before: CASE 9 only timed out a wait; no cancel was observed
  (`raw/f5_before.log`).
- After: CASE 9 starts a real public waiter holding the lifecycle lock, hides
  the receipt, sends `SIGINT`, and observes waiter exit (`returncode -2`), the
  same endpoint PID still alive, the process record and runtime surviving, and
  the lock released (`raw/f5_after.log`, `raw/real_refresh_e2e.log`
  `case9.cancel_keeps_endpoint`).

## Verification (all exit 0 unless noted)

| Command | Log | Exit |
| --- | --- | --- |
| `test_host_trust_public.py` | `raw/public_consumer.*` | 0 |
| dev fixture matrix | `raw/dev_fixture_matrix.*` | 0 |
| `verify-host-service` (real subprocess) | `raw/real_subprocess_matrix.*` | 0 |
| `npm run verify:local` | `raw/npm_verify_local.*` | 0 |
| `git diff --check` | `raw/git_diff_check.*` | 0 |
| real refresh harness (30/30 cases) | `raw/real_refresh_e2e.*` | 0 |
| finding pairs f1-f5 | `raw/f{1..5}_{before,after}.*` | 0 |
| kept red: `git diff --check` wrong `-C` path | `raw/git_diff_check_badpath.*` | 129 |

## Remaining NOT_EXECUTED

Installation, daemon restart, OTA, merge, push, remote receipt, and the
independent architecture review are outside this node and belong to the
controller. The downstream service real admin E2E fix node, Mac, Android, and
first-user delivery are also out of scope.

## Controller next action

1. Fetch latest `origin/main`; if it moved, rebase the candidate and rerun the
   affected checks.
2. Run the independent architecture review on this candidate; it must cover the
   five findings and confirm no previously accepted behavior regressed.
3. On PASS, commit the delta, merge to `main`, push, and verify the remote
   receipt. Do not install/restart/OTA from this node's evidence.

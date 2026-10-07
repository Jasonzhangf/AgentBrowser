# r3 raw command log

All commands ran from the repository root of the worktree
`/Volumes/Intel/playground/agentbrowser/m1-service-endpoint-refresh-r1-20261005`
with the worktree-level helper `r3/run.sh <logbase> <command...>`, so each
`*.log` has a matching `*.exit`.

Environment used by every real-binary command:

```
OBSCURA_HOST_BINARY=/Volumes/Intel/playground/obscura/m1-engine-reconcile-20261002/target/release/obscura-host
OBSCURA_ENDPOINT_BINARY=/Volumes/Intel/playground/obscura/m1-engine-reconcile-20261002/target/release/obscura-endpoint
OBSCURA_MEDIA_BINARY=/Volumes/Intel/playground/obscura/m1-engine-reconcile-20261002/target/release/obscura-media
OBSCURA_HOST_PROTOCOL_VERSION=11
```

`R3=docs/runs/m1-service-endpoint-refresh-r1-20261005/r3`

## Candidates under test

- `AFTER` = in-tree `scripts/host-service` (the candidate).
- `BEFORE_R1` = `/private/tmp/m1r3-before-r1-service`, a scratch copy of
  `git show 8cd4315459cd47b12bfdb091a94543f5c3e35dbb:scripts/host-service/...`
  (`host_service.py` only, plus the `host-service` launcher). Used for the
  P1-c before pair, because the r1 candidate has no start/persist reclaim guard.
- `BEFORE_R2` = `/private/tmp/m1r3-before-r2-service`, a scratch copy of
  `git show 7d5e46ba28eb7ae3f299d1a5fdf0bc93bbe0a54d:scripts/host-service/...`
  (`host_service.py`, `host-service`, and `test_host_trust_public.py`). Used for
  the P1-a, P1-b and P2 before pairs.

## Finding before/after pairs (the closing evidence)

Every pair drives the real `host-service` CLI or the real `findings_harness.py`
against real Obscura binaries. Each `before` run asserts the fixed
postcondition and must fail because the pre-fix candidate still has the defect;
each `after` run must pass.

```
zsh $R3/run.sh $R3/raw/p1a_before python3 $R3/findings_harness.py \
  --service-dir $BEFORE_R2 --case p1a --expect before --base-dir /private/tmp/m1r3-p1a-before
zsh $R3/run.sh $R3/raw/p1a_after  python3 $R3/findings_harness.py \
  --service-dir scripts/host-service --case p1a --expect after  --base-dir /private/tmp/m1r3-p1a-after

zsh $R3/run.sh $R3/raw/p1b_before python3 $R3/findings_harness.py \
  --service-dir $BEFORE_R2 --case p1b --expect before --base-dir /private/tmp/m1r3-p1b-before
zsh $R3/run.sh $R3/raw/p1b_after  python3 $R3/findings_harness.py \
  --service-dir scripts/host-service --case p1b --expect after  --base-dir /private/tmp/m1r3-p1b-after

zsh $R3/run.sh $R3/raw/p1c_before python3 $R3/findings_harness.py \
  --service-dir $BEFORE_R1 --case p1c --expect before --base-dir /private/tmp/m1r3-p1c-before
zsh $R3/run.sh $R3/raw/p1c_after  python3 $R3/findings_harness.py \
  --service-dir scripts/host-service --case p1c --expect after  --base-dir /private/tmp/m1r3-p1c-after

zsh $R3/run.sh $R3/raw/p2_before python3 $R3/findings_harness.py \
  --service-dir $BEFORE_R2 --case p2 --expect before --base-dir /private/tmp/m1r3-p2-before
zsh $R3/run.sh $R3/raw/p2_after  python3 $R3/findings_harness.py \
  --service-dir scripts/host-service --case p2 --expect after  --base-dir /private/tmp/m1r3-p2-after
```

Each command also sets `OBSCURA_HOST_BINARY` and
`OBSCURA_HOST_PROTOCOL_VERSION=11` from the environment block above.

## Acceptance suites (pre-existing)

```
zsh $R3/run.sh $R3/raw/accept_public_consumer       python3 scripts/host-service/test_host_trust_public.py
zsh $R3/run.sh $R3/raw/accept_dev_fixture_matrix     python3 scripts/host-service/test_host_service.py
zsh $R3/run.sh $R3/raw/accept_verify_host_service    scripts/host-service/verify-host-service
zsh $R3/run.sh $R3/raw/accept_real_refresh_e2e       python3 docs/runs/m1-service-endpoint-refresh-r1-20261005/real_refresh_e2e.py
zsh $R3/run.sh $R3/raw/accept_npm_verify_local       npm run verify:local
zsh $R3/run.sh $R3/raw/git_diff_check                git diff --check
```

`accept_verify_host_service` and `accept_real_refresh_e2e` also export
`OBSCURA_ENDPOINT_BINARY`/`OBSCURA_MEDIA_BINARY` and, for the E2E,
`REFRESH_EVIDENCE_DIR=$PWD/$R3/raw/real_refresh_evidence`.

## Superseded / corrected runs

- An early smoke run of P1-c used `--base-dir /private/tmp/m1r3-smoke-p1c`
  (after) and `/private/tmp/m1r3-smoke-p1c-before` (before); the same smoke
  bases exist for P1-a, P1-b and P2. Those bases held the harness's
  development iteration and are not the closing evidence; the closing pairs are
  the `p1{a,b,c}_{before,after}` and `p2_{before,after}` logs above.
- No command in this round was run with a wrong working directory, so there is
  no kept-red argument error like the r2 round's `git diff --check` mistake.
  The red exits in this round (`p1a_before`, `p1b_before`, `p1c_before`,
  `p2_before`) are expected: they are the before arms of the pairs and fail
  because the pre-fix candidates still exhibit the defect.

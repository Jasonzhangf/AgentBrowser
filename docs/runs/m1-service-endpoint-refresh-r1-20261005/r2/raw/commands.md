# r2 raw command log

All commands ran from
`docs/runs/m1-service-endpoint-refresh-r1-20261005/r2` with
`run.sh <logbase> <command...>`, so each `*.log` has a matching `*.exit`.

Environment used by every real-binary command:

```
OBSCURA_HOST_BINARY=/Volumes/Intel/playground/obscura/m1-engine-reconcile-20261002/target/release/obscura-host
OBSCURA_ENDPOINT_BINARY=/Volumes/Intel/playground/obscura/m1-engine-reconcile-20261002/target/release/obscura-endpoint
OBSCURA_MEDIA_BINARY=/Volumes/Intel/playground/obscura/m1-engine-reconcile-20261002/target/release/obscura-media
OBSCURA_HOST_PROTOCOL_VERSION=11
```

`BEFORE` is a scratch copy of the r1 code at commit
`8cd4315459cd47b12bfdb091a94543f5c3e35dbb` (`git show HEAD:...`), created at
`/private/tmp/m1r2-before-service-1791329249` with only `host-service` and
`host_service.py`. `AFTER` is the in-tree candidate
`scripts/host-service`.

## Finding pairs (before = r1, after = candidate)

```
./run.sh raw/f1_before env OBSCURA_HOST_BINARY=... OBSCURA_HOST_PROTOCOL_VERSION=11 \
  python3 findings_harness.py --service-dir /private/tmp/m1r2-before-service-1791329249 \
  --finding f1 --expect before --base-dir /private/tmp/m1r2-f1b
./run.sh raw/f1_after  env OBSCURA_HOST_BINARY=... OBSCURA_HOST_PROTOCOL_VERSION=11 \
  python3 findings_harness.py --service-dir <repo>/scripts/host-service \
  --finding f1 --expect after  --base-dir /private/tmp/m1r2-f1a

./run.sh raw/f2_before ... --finding f2 --expect before --base-dir /private/tmp/m1r2-f2-before
./run.sh raw/f2_after  ... --finding f2 --expect after  --base-dir /private/tmp/m1r2-f2a
./run.sh raw/f3_before ... --finding f3 --expect before --base-dir /private/tmp/m1r2-f3-before
./run.sh raw/f3_after  ... --finding f3 --expect after  --base-dir /private/tmp/m1r2-f3a
./run.sh raw/f4_before ... --finding f4 --expect before --base-dir /private/tmp/m1r2-f4-before
./run.sh raw/f4_after  ... --finding f4 --expect after  --base-dir /private/tmp/m1r2-f4a
./run.sh raw/f5_before ... --finding f5 --expect before --base-dir /private/tmp/m1r2-f5-before
./run.sh raw/f5_after  ... --finding f5 --expect after  --base-dir /private/tmp/m1r2-f5a
```

Each `before` run asserts the r1 behavior, each `after` run asserts the fixed
behavior; both are green because the harness checks the expected side of the
pair.

## Acceptance suites

```
./run.sh raw/public_consumer       python3 <repo>/scripts/host-service/test_host_trust_public.py
./run.sh raw/dev_fixture_matrix    python3 <repo>/scripts/host-service/test_host_service.py
./run.sh raw/real_subprocess_matrix env OBSCURA_HOST_BINARY=... OBSCURA_HOST_PROTOCOL_VERSION=11 \
  <repo>/scripts/host-service/verify-host-service
./run.sh raw/npm_verify_local      npm run verify:local
./run.sh raw/git_diff_check        git diff --check
./run.sh raw/real_refresh_e2e      env OBSCURA_HOST_BINARY=... OBSCURA_ENDPOINT_BINARY=... \
  OBSCURA_MEDIA_BINARY=... OBSCURA_HOST_PROTOCOL_VERSION=11 \
  REFRESH_EVIDENCE_DIR=$PWD/raw/real_refresh_evidence python3 ../real_refresh_e2e.py
```

`raw/git_diff_check_badpath.log`/`.exit` is a kept red result: the first
`git diff --check` used one `..` too many for `-C` and exited 129. The corrected
`git diff --check` run is `raw/git_diff_check.*` with exit 0.

`raw/iteration-1/` keeps the predecessor's earlier green finding logs before
this node re-ran the pairs against a freshly rebuilt `BEFORE` copy.

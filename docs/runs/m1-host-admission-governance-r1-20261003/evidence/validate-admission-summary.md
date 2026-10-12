# Formal candidate-bound admission (host-service)

Candidate: `b48ebf8ad31a04b8c599894a2d2cd368f15ce9fc`
Tree: `b3299e756fb897ae57f303942d9d6da234e69e38`
Base: `bc9d67ad037be2c7119bee692f9ad362417ffd05` (origin/main)
Host binary (read-only): `/Volumes/Intel/playground/obscura/m1-engine-reconcile-20261002/target/release/obscura-host`
Host binary sha256: `e4d3aa1d07c353ce7169d87d2f658f3cc269cb9a67219c6592dd1565cc20bfa7`
Protocol: `OBSCURA_HOST_PROTOCOL_VERSION=10`

## Command

```sh
OBSCURA_HOST_BINARY=/Volumes/Intel/playground/obscura/m1-engine-reconcile-20261002/target/release/obscura-host \
OBSCURA_HOST_PROTOCOL_VERSION=10 node scripts/host-service/validate-admission.mjs
```

Exit 0. stdout (also validate-admission.stdout.log):

```json
{"candidate":"b48ebf8ad31a04b8c599894a2d2cd368f15ce9fc","artifactHash":"sha256:c70b9eef84b4bd0a67cc6ec2c6aae203976549602b3e8ed02a2ac155172fa13d","directory":"evidence/host-service-admission-b48ebf8ad31a-1791048748813","admission":"pass"}
```

## Installed consumer evidence

- module artifact_hash `sha256:c70b9eef84b4bd0a67cc6ec2c6aae203976549602b3e8ed02a2ac155172fa13d`
- compiled artifacts: `host-service` `sha256:03fca07b71d92d027089218132250f3e8b54386a1522cbd3a141c3cc07b5f671`,
  `host_service.py` `sha256:8f83c424201e79251729fd531871ee605214061244096788d06df0f15604a6a3`
- install: label `com.agentbrowser.admission.3a94ab49faa148f2b2e413cfce285c97`,
  root `/private/tmp/abhs-admit-3a94ab49faa148f2b2e413cfce285c97`,
  runtime `/private/tmp/abhr-JeUmv7`, `launchd_loaded: false`, `protocol_version: 10`
- first running instance: pid `49425`, session `97ce5878-241e-40d2-8025-f5f7109c07d0`
- kickstart restart: pid `52442`, session `746b96a3-b163-45a9-bd1c-6f0ad244aaef`
- observe attach receipts: ready+result for both instances (blackbox.json)
- cleanup: `state: stopped`, pid `52442`
- `appsdk verify --review-admission --module host-service` -> exit 0,
  `{"ok":true,"gate":"review_admission","module_id":"host-service"}`

## Record IDs (candidate-bound, gitignored)

- fix candidate: `candidate-b48ebf8ad31a`
- evidence: `whitebox-b48ebf8ad31a`, `install-b48ebf8ad31a`,
  `restart-b48ebf8ad31a`, `blackbox-b48ebf8ad31a`
- pre-review validation: `validation-b48ebf8ad31a`

## Resource cleanup

- launchd label `com.agentbrowser.admission.3a94ab49faa148f2b2e413cfce285c97`: absent
  (`launchctl print` -> Could not find service)
- pids `49425` / `52442`: absent
- `/private/tmp/abhs-admit-3a94ab49...` and `/private/tmp/abhr-JeUmv7`: removed

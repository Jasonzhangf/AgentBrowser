# Host Service Host10 R1 Delivery

## Scope

Replace the host-service protocol-version constant with one explicit install
input, drive config/state/ready/status checks from that typed configuration, and
cover missing, malformed, and mismatched values through the real subprocess
consumer.

## Status

TASK-SCOPE FUNCTIONAL DELIVERY COMPLETE; APPSDK ADMISSION BLOCKED BY BASE

## Evidence

- Source owner: `host-service` only.
- Host10 ABI source: Obscura W2 `ff50bd99e959189f697f69f7472d7a82e66e2eff`,
  tree `48a9486e5735d7bc99a6761700caf88e4c36d367`; checkout drift is the
  later W2 delivery-docs commit and is recorded in `notes.md`.
- Fixed Host binary SHA256:
  `e4d3aa1d07c353ce7169d87d2f658f3cc269cb9a67219c6592dd1565cc20bfa7`.
- Development regression:
  `OBSCURA_HOST_BINARY=... OBSCURA_HOST_PROTOCOL_VERSION=10 scripts/host-service/verify-host-service`
  passed with `HOST_SERVICE_REAL_SUBPROCESS_PASS`.
- Build:
  `OBSCURA_HOST_BINARY=... OBSCURA_HOST_PROTOCOL_VERSION=10 scripts/host-service/build-host-service`
  passed; compiled `host-service` hash
  `03fca07b71d92d027089218132250f3e8b54386a1522cbd3a141c3cc07b5f671`,
  compiled `host_service.py` hash
  `8f83c424201e79251729fd531871ee605214061244096788d06df0f15604a6a3`.
- Public failures: missing env/install argument and non-u32 input exit 2;
  Host10-vs-expected-9 exits 2 immediately and cleans owned state/runtime.
- Candidate commit:
  `aed8e98ce377b8ec7fedacd69b472aa7aa634c42`, tree
  `62279d86701e17488514e03efa721bc98e54aa6b`.
- Compiled installed lifecycle passed with the fixed Host10 binary:
  install, start, real observe attach, client detach, status, restart with a new
  Session, stop, and stopped status all exited 0. Evidence:
  `evidence-installed/`.
- launchd passed with a unique task label and private plist root: bootstrap,
  first print/status, kickstart, replacement PID/Session, bootout, stale
  detection, owned cleanup, and final absence all matched. Evidence:
  `evidence-launchd/`.
- `validate-admission.mjs` is blocked before its install phase:
  `appsdk compile-module --module host-service` returns
  `NON_CANONICAL_RECORD_CONTRACT_SET`. The untouched base
  `bc9d67ad037be2c7119bee692f9ad362417ffd05` reproduces the same failure. The
  project record-contract list is missing the canonical
  `contracts/records/collab-live-closure-record.schema.json` entry and has
  `record-graph.contract.json` in the wrong order. Existing admission records
  were not deleted, altered, or bypassed.
- No review, merge, push, global service installation, or product integration
  was started.

## Consumer Handoff

The install consumer must now pass the expected Host ABI version explicitly:

```sh
scripts/host-service/host-service install \
  --root <private-root> \
  --binary <validated-obscura-host> \
  --protocol-version <paired-host-u32>
```

Verification and admission consumers pass the same value through
`OBSCURA_HOST_PROTOCOL_VERSION`. The service keeps `profile_mode: in_memory`;
disk persistence remains outside this task.

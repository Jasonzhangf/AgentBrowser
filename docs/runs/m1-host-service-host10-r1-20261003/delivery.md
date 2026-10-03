# Host Service Host10 R1 Delivery

## Scope

Replace the host-service protocol-version constant with one explicit install
input, drive config/state/ready/status checks from that typed configuration, and
cover missing, malformed, and mismatched values through the real subprocess
consumer.

## Status

AUTHOR VERIFICATION COMPLETE; INSTALLED ADMISSION PENDING

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
- Installed launchd lifecycle/admission is run after the clean candidate commit;
  records are emitted under the task worktree's ignored admission/evidence
  paths.

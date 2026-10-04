# m1-host-trust-authority-r2 notes

Author: this run's Host trust authority author. Scope:
`scripts/host-service/**`, `docs/host-service.md`,
`docs/runs/m1-host-trust-authority-r2-20261004/**`.

## Design inputs

+ First-connection r9 final root
  `/Volumes/Intel/playground/agentbrowser/m1-first-connection-admission-r9-20261004`
  at `9f1522062d169d6feb3d891102463ca74f4902a9`; implementation contracts,
  BB plan, G-1 interface handoff, and r9 source trace were read.
+ OB profile terminal root
  `/Volumes/Intel/playground/obscura/m1-profile-eof-contract-r7-20261004` at
  `687db20bb4e08211d1144996befbbf1557d5641e` is consumed only as the frozen
  terminal design, not as a product writer.
+ r1 terminal was a provider decode failure, not a proven crypto defect; its
  19-line patch was not imported.

## Decisions

+ The trust root is a separate `trust init` operation because the Host
  lifecycle `install` requires a real `obscura-host` binary which this run does
  not have. Re-init is refused with `TRUST_ALREADY_INITIALIZED` and preserves
  bytes.
+ `registry.json` and `authorized-clients.json` use compact canonical JSON so
  `registry_sha256` and the external `projection_sha256` are exact published
  byte digests.
+ PKCS#12 assembly uses the installed OpenSSL-backed `cryptography` library
  because the OpenSSL CLI cannot attach distinct friendly names to the extra
  certificate bags; verification in the public consumer uses the real OpenSSL
  CLI binary (`/opt/homebrew/bin/openssl` 3.6.3 seen by the tool).
+ Export implements the ordered dependency refusal
  (`BUNDLE_NOT_READY`, `PROFILE_CATALOG_UNAVAILABLE`,
  `ENDPOINT_REFRESH_REQUIRED`) and does not create substitute receipts. The
  success bundle writer exists as the full typed path but is unreachable until
  the OB Host catalog and endpoint owners supply real receipts.

## Runs

| 时间 / 节点 | 结论 / 状态 | 证据路径 | 输入版本 | 下一步 |
| --- | --- | --- | --- | --- |
| 2026-10-04 / design | filled contract from r9 final design and OB terminal | parent read-only docs | AB `dacbb84` tree `b29cd8d` | implement |
| 2026-10-04 / implement | trust CLI added; compile ok | `raw/py-compile.txt` | candidate source | validate |
| 2026-10-04 / public consumer | `HOST_TRUST_PUBLIC_CONSUMER_OK` exit 0 | `raw/public-consumer.{stdout,stderr,exit}` | source candidate | evidence |
| 2026-10-04 / lifecycle regression | existing dev fixture suite PASS exit 0 | `raw/existing-dev-fixture.{stdout,stderr,exit}` | same source | retain WAIT for real Host |

## Non-goals and dependencies

+ Host catalog membership and endpoint applied receipts are OB owners; export
  refuses at `PROFILE_CATALOG_UNAVAILABLE` until those real receipts exist.
+ Full Mac -> Android product E2E and BB01 remain `WAIT_DEPENDENCY`.
+ Real Host lifecycle subprocess acceptance still requires an `obscura-host`
  binary and protocol version; not run here.

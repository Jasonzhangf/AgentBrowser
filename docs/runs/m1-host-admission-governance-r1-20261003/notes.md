# Host service admission governance R1

Owner: host-admission-governance worker (no Collab/goal/AGY registration).

## Inputs

- Worktree: `/Volumes/Intel/playground/agentbrowser/m1-host-admission-governance-r1-20261003`
- Branch: `codex/m1-host-admission-governance-r1-20261003`
- Start HEAD: `6e4e087cd8a24b160205edff92e863383f49b318` (service `aed8e98` + docs `9774f6b` over `bc9d67a`).
- Installed AppSDK: `/Users/fanzhang/.cargo/bin/appsdk`, `0.1.0010`, sha256 `d7a67ef2...`.
- Canonical SDK source: `/Users/fanzhang/Documents/github/appsdk` @ `61e8c534`, release `0.1.0010`.
- Host binary (read-only): `/Volumes/Intel/playground/obscura/m1-engine-reconcile-20261002/target/release/obscura-host`.

## Gate sequence observed (same entrypoint)

1. `NON_CANONICAL_RECORD_CONTRACT_SET` (base) -> evidence/red-compile.log
2. After registering `collab-live-closure-record` + canonical transition manifest:
   `DECLARED_RECORD_CONTRACT_MISMATCH:contracts/records/evidence-record.schema.json/allOf/1/then/properties/artifact_hash`
   -> evidence/green-compile.log
3. After refreshing the five drifted declared record contracts to the canonical source:
   `PROJECT_SDK_VERSION_PIN_MISMATCH:0.1.6:required_binary=appsdk-0.1.6`
   -> evidence/pin-mismatch-compile.log

Root cause: the project `.appsdk` control plane is pinned to SDK `0.1.6` while the
installed compiler is `0.1.0010`. `appsdk compile-module` compares the declared
root `contracts/records/**` against the installed binary's embedded canonical
set, then rejects the stale pin. The canonical record set and the pin must move
to `0.1.0010` together via the official migration entry.

## Decision

Use the official `appsdk pin-lock --binary` migration (no `appsdk init`, no
Collab init, no reset/discard). It owns the canonical record-contract install,
the governance-map migration record, the SDK lock, the bundle resource install,
and the ignored `.appsdk/sdk.bin` witness.

## Migration result

- `appsdk pin-lock . --binary /Users/fanzhang/.cargo/bin/appsdk` -> exit 0,
  `pinned ...` (evidence/pin-lock.log).
- `.appsdk/project.json` sdk.version `0.1.6` -> `0.1.0010`; `record_contracts`
  now the canonical 20-entry set with `collab-live-closure-record` before
  `merge-record` and `record-graph.contract.json` last.
- `.appsdk/sdk.lock` version `0.1.0010`, `digest`/`compiler_digest` = installed
  binary sha256 `d7a67ef2...`, `bundle_digest` `fdccb88a...`.
- `.appsdk/sdk-resources.json` version `0.1.0010`, 94 bundle resources.
- New migration witness `.appsdk/migrations/0.1.0009-to-0.1.0010/record.json`
  preserves the project's custom governance maps via `canonical_source_digest`
  bindings; `.appsdk/maps/**` is unchanged.
- `.appsdk/sdk.bin` witness materialized (gitignored), byte-identical to the
  installed binary.
- Full change classification: evidence/migration-diff.txt.

## Green gates

- `appsdk compile-module --module host-service` -> exit 0
  (evidence/compile-after-migration.log; artifact_hash
  `sha256:c70b9eef84b4bd0a67cc6ec2c6aae203976549602b3e8ed02a2ac155172fa13d`).
- `appsdk verify` -> exit 0, development_ready true
  (evidence/verify-after-migration.log).
- `npm run verify:local` -> exit 0 (evidence/verify-local.log).

## Formal candidate-bound admission

Candidate `b48ebf8ad31a04b8c599894a2d2cd368f15ce9fc`, tree
`b3299e756fb897ae57f303942d9d6da234e69e38`.

`OBSCURA_HOST_BINARY=.../obscura-host OBSCURA_HOST_PROTOCOL_VERSION=10 node
scripts/host-service/validate-admission.mjs` -> exit 0, `admission: pass`.
Installed bundle ran launchd bootstrap, real observe attach, kickstart restart
with a new PID/session, and stopped cleanup. `appsdk verify --review-admission
--module host-service` -> exit 0. Full bindings:
evidence/validate-admission-summary.md and evidence/validate-admission.stdout.log.

Owned resources cleared: launchd label absent, recorded PIDs absent, private
root/runtime removed. No reader dependency remains on the shared target lease.

No review, merge, push, or global service action was performed.

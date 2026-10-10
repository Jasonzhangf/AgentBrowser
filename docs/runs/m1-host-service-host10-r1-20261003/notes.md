# Host Service Host10 R1 Notes

## 2026-10-03 / baseline

- Status: implementation worktree confirmed clean before edits.
- Source: `bc9d67ad037be2c7119bee692f9ad362417ffd05`, tree
  `4ee512388b4a6687a7a620ca7b0a6309af1b6993`, branch
  `codex/m1-host-service-host10-r1-20261003`.
- Host binary:
  `/Volumes/Intel/playground/obscura/m1-engine-reconcile-20261002/target/release/obscura-host`,
  SHA256 `e4d3aa1d07c353ce7169d87d2f658f3cc269cb9a67219c6592dd1565cc20bfa7`.
- Protocol source contract: W2 commit
  `ff50bd99e959189f697f69f7472d7a82e66e2eff`, tree
  `48a9486e5735d7bc99a6761700caf88e4c36d367`.
- Protocol checkout drift: the read-only worktree currently has delivery-docs
  commit `9515e8125b449e1250406ab662bab5c78069a11d` /
  tree `de869056056492278cd7fe984e1f0aeebfd149a8`; its parent is the contract
  commit. Diff from the contract commit is only W2 delivery/notes Markdown, so
  protocol code is read from `ff50bd99...:protocol/browser/src/lib.rs`.
- Expected Host ABI from `ff50bd99...`: `HOST_PROTOCOL_VERSION = 10`,
  ready response is `{"type":"ready","version":<u32>,"session_id":"..."}`.
- Owner and allowed implementation paths: `scripts/host-service/**`,
  `docs/host-service.md`, and this run directory. Product, Obscura, client,
  profile-persistence, and other authors' files are out of scope.
- Next: reproduce the current manager's Host10 version rejection and cleanup.

## 2026-10-03 / observe-reproduction

- Status: reproduced.
- Evidence: `evidence/observe-install.stdout`, `observe-start.stderr`,
  `observe-status.stdout`, `observe-result.txt`, `observe-environment.txt`.
- Environment: task-owned private root/runtime/label under `/private/tmp`; fixed
  Host10 binary above.
- Result: install exited 0 and wrote `protocol_version: 4`; start exited 2 with
  `Host did not become ready within 3.0s: Host protocol version is not 4`;
  status exited 0 and reported `stopped`.
- Cleanup: `state.json` was absent, the allocated `host-*` socket directory was
  absent, the owned runtime root remained as an empty directory, and no matching
  Host process remained. Task root/runtime/launchd paths were removed after the
  observation.
- First divergence: `host_service.py` owns both a hard-coded expected version
  and a ready loop that retries semantic version rejection until timeout.
- DAG: existing `m1-delivery@1` graph validates with 7 nodes/6 edges; this change
  stays inside its `install_runtime` node and does not alter topology.
- Next: add the explicit install input and one configured expected-version truth,
  then add missing/bad/mismatch public regressions.

## 2026-10-03 / implementation

- Status: implemented in the host-service owner only.
- Source changes:
  - `scripts/host-service/host_service.py`: removed the expected-version
    constant; added strict positive-u32 parsing; required install
    `--protocol-version`; config validation accepts only a typed positive u32;
    state stores the same configured projection; ready/status use config as the
    only expected-version source; parsed ready rejection is terminal and no
    longer retried until timeout.
  - `scripts/host-service/test_host_service.py`: added missing, malformed,
    Python-bool, and real Host mismatch regressions; all installs now receive
    the explicit expected version.
  - `scripts/host-service/verify-host-service`: requires
    `OBSCURA_HOST_PROTOCOL_VERSION` and passes it to the real subprocess suite.
  - `scripts/host-service/validate-admission.mjs`: requires and asserts the
    same explicit version through compiled-bundle launchd admission.
  - `docs/host-service.md`: documents the install input, u32 boundary,
    config-driven ready/status behavior, and new verification environment.
- DAG: reused the reviewed `m1-delivery@1` graph's `install_runtime` node;
  `dagpipe graph validate` passed with 7 nodes/6 edges and no topology change.
- Next: run public failure probes, generated bundle lifecycle, and installed
  admission after committing the candidate.

## 2026-10-03 / development-verification

- Status: pass.
- Smoke: task-owned install/start/status/stop with the fixed Host10 binary
  exited `0/0/0/0`; status reported `protocol_version: 10`, the real PID and
  Session, then cleanup left no task root/runtime/launchd directory.
  Evidence: `evidence/smoke-*`.
- Full regression:
  `OBSCURA_HOST_BINARY=... OBSCURA_HOST_PROTOCOL_VERSION=10 scripts/host-service/verify-host-service`
  exited 0 with `HOST_SERVICE_REAL_SUBPROCESS_PASS`.
  Evidence: `evidence/verify.stdout`, `evidence/verify.stderr`,
  `evidence/verify-result.txt`.
- Build:
  `OBSCURA_HOST_BINARY=... OBSCURA_HOST_PROTOCOL_VERSION=10 scripts/host-service/build-host-service`
  exited 0 and emitted `generated/modules/host-service/lib/`.
  Evidence: `evidence/build-result.txt`, `evidence/build.stdout`,
  `evidence/build.stderr`.
- Compiled bundle hashes:
  `host-service` `03fca07b71d92d027089218132250f3e8b54386a1522cbd3a141c3cc07b5f671`;
  `host_service.py` `8f83c424201e79251729fd531871ee605214061244096788d06df0f15604a6a3`.
- Next: record targeted public failures, commit the candidate, then run the
  compiled installed lifecycle and admission on that exact commit.

## 2026-10-03 / public-failure-probes

- Status: pass.
- Missing verification input: `verify-host-service` exited 2 with
  `expected Host protocol version required: set OBSCURA_HOST_PROTOCOL_VERSION`.
- Missing install input: `install` exited 2 and reported the required
  `--protocol-version`.
- Bad install input: `--protocol-version 0` exited 2 with
  `protocol version must be a positive u32`.
- Real mismatch: config expected 9, Host10 ready returned 10; start exited 2
  in under one second with `Host protocol version is 10, expected 9`.
  `state.json` was absent, the runtime root had no socket child, and no owned
  child was left behind. Evidence: `evidence/failure-probes-result.txt` and
  `evidence/fail-*`.
- Next: commit the exact candidate and run compiled installed admission.

## 2026-10-03 / candidate-commit

- Status: committed.
- Candidate: `aed8e98ce377b8ec7fedacd69b472aa7aa634c42`, tree
  `62279d86701e17488514e03efa721bc98e54aa6b`, parent/base
  `bc9d67ad037be2c7119bee692f9ad362417ffd05`.
- First commit attempt was blocked by the project hook because `node_modules`
  was absent in the fresh worktree; `npm ci` restored lockfile dependencies.
  The retry passed the normal hook's `verify:local`: typecheck, 8 tests, and UI
  build all passed. No hook bypass was used.
- Next: run installed admission/E2E on exact candidate.

## 2026-10-03 / admission-gate

- Status: blocked before compiled bundle install.
- Command:
  `OBSCURA_HOST_BINARY=... OBSCURA_HOST_PROTOCOL_VERSION=10 node scripts/host-service/validate-admission.mjs`
  failed at `appsdk compile-module --module host-service`, which returned
  `NON_CANONICAL_RECORD_CONTRACT_SET`.
- Evidence: `evidence/host-service-admission-aed8e98ce377-1791045027403/compile.log`.
- Isolation: the untouched fresh base worktree
  `/Volumes/Intel/playground/agentbrowser/m1-browser-chrome-r4-20261003` at
  `bc9d67ad...` (tree `4ee512388...`) returns the same gate with the same
  command. This is a base governance blocker, not introduced by this candidate.
- Contract cause: the project's `.appsdk/project.json` record-contract list is
  stale relative to the pinned/current compiler's canonical set. The canonical
  list inserts `contracts/records/collab-live-closure-record.schema.json` before
  `contracts/records/merge-record.schema.json`, whereas the project list omits
  that file. The matching upgrade commit
  `09222706bb1035190e795c7715322fd7b95cc78a` changed `.appsdk` only; applying it
  is outside this host-service contract's allowed paths.
- Consequence: admission stopped before the compiled-bundle launchd install, so
  it did not create the task-owned fix-candidate/validation records. No shared
  record was deleted, overwritten, mocked, or bypassed.
- Next: complete the independent compiled installed lifecycle and do not claim
  AppSDK admission PASS.

## 2026-10-03 / compiled-installed-lifecycle

- Status: pass.
- Candidate identity: `aed8e98ce377b8ec7fedacd69b472aa7aa634c42`, tree
  `62279d86701e17488514e03efa721bc98e54aa6b`.
- Installed fixture: `docs/runs/m1-host-service-host10-r1-20261003/installed/`,
  copied from `generated/modules/host-service/lib/`. Hash comparison matched the
  build output: `host-service`
  `03fca07b71d92d027089218132250f3e8b54386a1522cbd3a141c3cc07b5f671`;
  `host_service.py`
  `8f83c424201e79251729fd531871ee605214061244096788d06df0f15604a6a3`.
- Real commands with the fixed Host10 binary: install 0, start 0, status 0,
  actual Unix-socket observe attach 0, status-after-detach 0, restart 0,
  status 0, stop 0, status-stopped 0.
- Behavior: first PID 29275/session
  `d3a88243-41a4-4aa3-a7fa-d997db632e33`; readiness and attach both reported
  Host wire version 10; observe attach returned typed `result` with
  `attachment_id` 3; detach kept the same running PID/session; restart created
  PID 30172/session `73db6635-11b6-47f0-80ec-aea1e62eafaa`; stop reported
  stopped and status stayed stopped.
- Evidence: `evidence-installed/`.

## 2026-10-03 / launchd-installed-lifecycle

- Status: pass.
- Candidate compiled manager was installed with unique label
  `com.agentbrowser.launchd.h10.<pid>` under a task-owned plist root.
- Real commands: install 0, `launchctl bootstrap` 0, first `launchctl print` 0,
  status running, `launchctl kickstart -k` 0, second `launchctl print` 0,
  status running with a new PID/session, `launchctl bootout` 0, status stale 1,
  manager stop 0 to clean owned state/runtime, final `launchctl print` exit 113
  with "Could not find service".
- Behavior: Host10 wire version 10; first PID 39591/session
  `e05527e9-e09e-4d0c-8770-8f74105cee77`; kickstart created PID 40240/session
  `407884ae-161d-40e3-861d-3ff29c3696fa`.
- Evidence: `evidence-launchd/`.
- Next: finalize delivery report; do not start independent review, merge, push,
  global installation, or product integration.

# Host service profile11 run notes

## 2026-10-03 17:45 PDT / baseline-observe

- Conclusion: baseline HEAD `f762451e2e9db8b298fa18eaeee920d3e28e3a4d`
  tree `76af6837869a0c68040f69c368bc0cc07695346e` is clean in the assigned
  worktree and already contains the inherited r2 service candidate.
- Evidence: `git rev-parse HEAD`, `git rev-parse HEAD^{tree}`, and
  `git status --porcelain` all read from the assigned worktree. The old
  service config/state hard-code `in_memory`; Host argv is only
  `--socket-dir [--allow-private-network]`; stop/restart signal the recorded
  PID without a Host maintenance receipt.
- Input/environment: controller design
  `9c1f5f71886c0302d8852600f650e9472b79a55d:docs/m1/browsing-profile-contract.md`;
  OB source `c0cc80ae24cbfed50be186c23e8d75dc29b6ee85` / receipt
  `5ac23fd0e0f970643fe4faeb4a427893efe8f242`; protocol
  `2344ad88b52e34af1e8d6ccbd6de998770bbeca080e6ec57cd0e047aeb6edab9`.
- First divergence: service has no persistent `profile_root` projection and no
  local `flush_profiles` request/typed `ProfileFlush` consumer before stop.
- Next: add Host11 service config/argv/maintenance wiring and fixture coverage;
  do not start a real Host or install launchd.

## 2026-10-03 21:45 PDT / red-observe

- Conclusion: the inherited service rejects the new install argument and has no
  maintenance call, so the required behavior is absent at the entrypoint.
- Evidence: against the inherited `host-service` source,
  `host_service.py install ... --profile-root <dir>` exits `2` with argparse
  `unrecognized arguments`; source search found no `flush_profiles` request,
  `ProfileFlush` consumer, or `profile_root` projection in the service.
- Input/environment: baseline HEAD
  `f762451e2e9db8b298fa18eaeee920d3e28e3a4d`, Python 3.14 fixture run in the
  assigned worktree; no real Host or launchd used.
- Next: implement the v2 config/state projection, explicit argv/plist root,
  and typed flush-before-stop/restart path with local fixtures.

## 2026-10-03 22:00 PDT / green-source-fixture

- Conclusion: the Host11 service candidate is source-complete for the local
  fixture boundary and remains inside the declared service scope.
- Evidence, exact commands and exits:
  - `python3 -m py_compile scripts/host-service/host_service.py
    scripts/host-service/test_host_service.py` -> `PY_COMPILE_EXIT=0`.
  - `python3 scripts/host-service/test_host_service.py` ->
    `HOST_SERVICE_DEV_FIXTURE_PASS`, `DEV_FIXTURE_EXIT=0` (about 44 s).
  - `scripts/host-service/build-host-service-source` -> `SOURCE_BUILD_EXIT=0`;
    launcher SHA-256
    `03fca07b71d92d027089218132250f3e8b54386a1522cbd3a141c3cc07b5f671`,
    `host_service.py` SHA-256
    `a3c9ac1e9547222489bf5a15216b1950307f690f33b17d3a12b8eae7f8c82c0b`.
    `generated/modules/host-service/lib` contains the same two hashes and stays
    ignored for successor consumption.
  - `scripts/host-service/build-host-service --help` -> exit `2`,
    `real obscura-host required: set OBSCURA_HOST_BINARY or provide
    active/bin/obscura-host`; `verify-host-service --help` -> the same exit and
    refusal. This confirms no real Host verification was started.
  - `git diff --check` -> `DIFF_CHECK_EXIT=0`.
- Covered fixture behavior: install/config v2 projection and explicit
  `--profile-root` argv; legacy v1 in-memory normalization; v2 missing/empty
  root rejection; persistent stop/restart success, `MAINTENANCE_BUSY`,
  `PROFILE_STORAGE_FAILED`, and unknown/no-receipt handling; launchd
  supervisor flush-before-child-reap ordering; wrong/matching protocol
  version handling already inherited; foreign-PID refusal; reinstall without
  `--profile-root` preserves the existing canonical root and sentinel data;
  restart keeps the same root and creates a new Session/epoch.
- Input/environment: worker worktree, branch
  `codex/m1-host-service-profile11-r1-20261004`; only self-owned `cc` fixture
  launchers, Unix sockets, and `/private/tmp` service roots were used. No OB
  binary/target, real Host, launchd/bootstrap, device, or shared service was
  touched.
- Next: keep the candidate at `SOURCE_BUILT/E2E_WAIT`; the controller must
  supply the validated Host11 binary/hash, legal wrong version, private profile
  root/store data, launchd root/UID, and formal verify/admission window.

## 2026-10-03 22:05 PDT / cleanup-and-scope

- Conclusion: this task's local fixture resources are removed; unrelated
  worker evidence and the ignored successor build output were preserved.
- Evidence: `find /private/tmp -maxdepth 1 -type d \( -name 'abhp-*' -o -name
  'abhl*' \) -exec rm -rf {} +` followed by a count check -> `leftover_count=0`.
  `scripts/host-service/__pycache__` was removed and verified absent.
  `abhs-launchd-*`, `abhs-mp*`, and `abhs-rj-*` artifacts from other workers
  remain untouched. No fixture PIDs were alive before cleanup.
- Scope: `scripts/host-service/host_service.py`,
  `scripts/host-service/test_host_service.py`, `docs/host-service.md`, and this
  notes directory only. `scripts/host-service/host-service`,
  `build-host-service`, `build-host-service-source`, `verify-host-service`, and
  `validate-admission.mjs` are unchanged; `validate-admission.mjs` remains
  deferred to the formal launchd window.
- Input/environment: assigned worktree at
  `f762451e2e9db8b298fa18eaeee920d3e28e3a4d` plus the worker changes; no
  merge, push, install, review, or real runtime action was performed.
- Formal E2E status: `NOT_EXECUTED`. Real Host11 binary/hash, launchd
  bootstrap/bootout, real store flush/replay, matching/wrong version admission,
  and cleanup evidence require the controller-provided fixed binary and
  private runtime window.
- Next: commit only the scoped source/docs/notes on the worker branch with
  normal hooks and report `SOURCE_BUILT/E2E_WAIT` to the controller.

## 2026-10-03 22:12 PDT / source-built-handoff

- Conclusion: the scoped worker candidate is committed on the worker branch and
  ready for controller pickup at `SOURCE_BUILT/E2E_WAIT`.
- Evidence: normal pre-commit hook passed after locked dependency install
  (`npm ci`, then `npm run verify:local`): typecheck clean, 8/8 TAP tests pass,
  UI build succeeded. The worker commit is
  `feat(host-service): wire persistent profile maintenance` on
  `codex/m1-host-service-profile11-r1-20261004`; the exact commit/tree are in
  the worker handoff because embedding the commit SHA here would change it.
  `git status --porcelain` is clean. Ignored `node_modules` and
  `generated/modules/host-service/lib` remain as local build inputs for the
  successor.
- Input/environment: worker branch
  `codex/m1-host-service-profile11-r1-20261004` on base
  `f762451e2e9db8b298fa18eaeee920d3e28e3a4d`; no fetch, merge, push, review,
  install, launchd bootstrap, real Host, or device action.
- Formal E2E status: `NOT_EXECUTED`; the controller must provide the fixed
  Host11 binary/hash, legal wrong version, private profile root with real store
  data, launchd root/UID, and formal verify/admission window before this
  candidate can leave `E2E_WAIT`.

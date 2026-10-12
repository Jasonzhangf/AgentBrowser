# Host launchd rejection r2 source notes

## 2026-10-03 / baseline

- Status: inherited source-only stage. HEAD
  `75e5355b5e45f90b5f1fac191c3b06921d08a497`; tree
  `5d18b013b37d43d2bb819ddd6bfd9901e42c3a16`.
- Source of truth read: controller contract blob
  `cc4c4693486ffea45ced4f33e824feb5294f919f:docs/runs/m1-20261002/host-launchd-rejection-r1.contract.md`
  and independent review
  `/Volumes/Intel/playground/agentbrowser/m1-host-admission-governance-r1-20261003/.agent-collab/review/m1-host-service-governance-implementation-20261003-r1/review.final.md`.
- Applied partial patch identity before edits:
  `4590066e95be899220c13aebf3daf577dc4e6edcfa9599f0a723c1fa4fa3a59d`;
  this matched the existing r2 and r1 worktrees byte-for-byte and was not reapplied.
- Boundary: no OB binary/target reader, Host/endpoint start, install, launchd
  bootstrap/kickstart, device, review, merge, push, Collab, goal, or child.
- Next: close source/debug/test/build evidence and commit only this worktree.

## 2026-10-03 / source-debug

- Conclusion: launchd now validates ready against the typed configured version,
  terminates and waits for its owned child on terminal rejection, removes the
  owned socket runtime, and records `launchd_rejected`. `status` projects
  terminal `ready_rejected` with exit 1 instead of swallowing it as `starting`.
- Conclusion: the terminal state retains the manager identity needed for
  foreign-PID protection, so `stop` never signals a guessed process or removes
  a runtime without owned-state validation.
- Conclusion: `KeepAlive` uses successful-exit semantics so a managed Host is
  restarted after a crash; a terminal ready rejection exits non-zero and is not
  restarted as a confirmed protocol failure.
- Conclusion: launchd manager exits non-zero on terminal ready rejection after
  stopping/waiting for its owned child and removing only the owned socket
  runtime; the terminal `launchd_rejected` state is preserved for exact
  status/stop inspection. A launchd restart of that failure path is outside
  this source-only stage and must be observed in the real launchd window.
- Evidence: source diff in this worktree; the test-only fixture is local.
- Next: run syntax, development fixture, graph validation, and source bundle.

## 2026-10-03 / development-verification

- Command:
  `python3 -m py_compile scripts/host-service/host_service.py scripts/host-service/test_host_service.py`
  -> exit 0.
- Command: `python3 scripts/host-service/test_host_service.py` -> exit 0,
  `HOST_SERVICE_DEV_FIXTURE_PASS`. The fixture uses only an executable
  `--help` stub, a Unix socket, and a private `/private/tmp/abhs-rj-*` root.
  It asserts legal-but-mismatched `expectedVersion` rejection, non-zero
  `status` state, terminal state, empty owned runtime, explicit cleanup, and
  state compatibility without the new `ready_error` field. It does not load
  launchd, install a service, or claim real Host E2E.
- Command: `dagpipe graph validate docs/dagpipe/graphs/m1-delivery.graph.json`
  -> exit 0, `valid DAG: m1-delivery@1 (7 nodes, 6 edges, 7 waves)`.
- Graph identity:
  `text` path `docs/dagpipe/graphs/m1-delivery.graph.json`, SHA-256
  `896e2c39105ca5bfb9c62bd770a2755e913035f72b793f3cde58cb960b45c2bb`;
  byte comparison with the controller/repair-model source exited 0.
- Source bundle command executed by
  `scripts/host-service/build-host-service-source`: exit 0. It compiles both
  Python files and copies the launcher/manager into the ignored
  `generated/modules/host-service/lib/`. Observed SHA-256:
  launcher `03fca07b71d92d027089218132250f3e8b54386a1522cbd3a141c3cc07b5f671`,
  manager `c4cb304c0b0f6ee09647aed6790c98062c9aa6f135c6b220ce25bc6497c09201`.
- Not verified: real Host binary, installed manager, launchd bootstrap,
  ready mismatch against Obscura, observe attach, private restart/stop,
  foreign-PID live rejection, `verify-host-service`, admission gate, review,
  merge, push, or product acceptance.
- Next action: controller must bind the validated Host path/hash, positive
  protocol version, legal mismatching version, private launchd root, and UID;
  then run the prepared real launchd matrix in a successor stage.

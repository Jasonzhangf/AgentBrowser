# m1-service-endpoint-refresh-r3 - handoff

## State

`AUTHOR_COMPLETE`. All three P1 findings and the one P2 finding from
`m1-service-endpoint-refresh-r1-20261005-r2` are closed in the working tree on
candidate `7d5e46ba28eb7ae3f299d1a5fdf0bc93bbe0a54d`. No commit, merge, push,
install, or restart was performed; the working tree is left uncommitted for the
controller.

## What changed

- `scripts/host-service/host_service.py`
  - `refresh_endpoint` resolves and preflights the numeric bind once and passes
    that selected `(host, port)` into the start command.
  - `endpoint_listen_address(host, port)` and `endpoint_command(..., listen_address, ...)`
    take the already-resolved bind and never re-resolve the locator.
  - `_declared_endpoint_listen(record)` reads the actual `--listen` value from the
    typed process record; `_verify_endpoint_applied` compares the receipt
    against that recorded fact instead of a fresh DNS query.
  - `_verify_endpoint_applied` converts the registry/projection `read_bytes`
    `OSError` into a typed `ENDPOINT_NOT_REFRESHED`.
  - The `verify_applied` step catches `(ServiceError, OSError)` and routes
    through `_endpoint_refresh_failure`, adding `authorization_state`,
    `last_step`, generation and endpoint PID.
- `scripts/host-service/test_host_trust_public.py`
  - adds `write_endpoint_process_record` and declares the numeric bind in the
    `test_wait_endpoint_applied_public` fixture (required because the verifier
    now reads the process record).
  - `test_endpoint_bind_basis_public` calls
    `endpoint_listen_address(numeric_host, numeric_port)` and
    `_bound_address_matches(receipt, listen)` with the declared value.
  - `test_refresh_missing_binary_typed_failure` manages the `r2mb-*` short
    runtime with `try/finally` and removes it on both exits.
- `docs/host-service.md` documents the single declared bind basis, the
  process-record receipt comparison, and the typed acceptance read failure.
- `docs/runs/m1-service-endpoint-refresh-r1-20261005/r3/`
  - `findings_harness.py`, `run.sh`, and `raw/` before/after pairs for P1-a,
    P1-b, P1-c and P2.

## Evidence

See `report.md` and `notes.md`. Every real command ran through
`r3/run.sh <logbase> <command...>` with a matching `.exit`. The after arms of
all four pairs exit 0; the before arms exit 1 with the named assertion failure
for the real defect. All five pre-existing acceptance suites plus
`git diff --check` exit 0.

## What the controller must do next

1. Fetch the latest `origin/main` and rebase/combine if it moved.
2. Run the independent architecture review on this candidate against the three
   P1 and one P2 findings and the no-regression requirement.
3. On PASS, commit the delta, merge to `main`, push, and verify the remote
   receipt. Installation/restart/OTA are not part of this node.

## Residual risk

- The P1-a regression relies on an injected `socket.getaddrinfo` (via
  `PYTHONPATH` `sitecustomize`) to make a legal multi-address DNS locator answer
  in a changing order; the real endpoint, receipt and verifier all still run.
- The P1-b regression requires the watchdog to flip the registry between the
  step-1 read and the acceptance read. It uses the process-record publication as
  the trigger; if that read never happens the case fails closed rather than
  passing silently.
- The P1-c regression uses `chflags UF_IMMUTABLE` on the record's parent
  directory; this is the same real filesystem-layer failure a full or
  permission-denied volume would produce, and the real writer path runs
  unmodified.

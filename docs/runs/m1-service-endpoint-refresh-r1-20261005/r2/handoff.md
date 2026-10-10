# m1-service-endpoint-refresh-r2 - handoff

## State

`AUTHOR_COMPLETE`. All five P1 findings from
`m1-service-endpoint-refresh-r1-20261005-r1` are closed in the working tree on
candidate `8cd4315459cd47b12bfdb091a94543f5c3e35dbb`. No commit, merge, push,
install, or restart was performed; those belong to the controller.

## What changed

- `scripts/host-service/host_service.py`
  - `_export_membership_receipt` uses the committed member set and refuses a
    missing/removed target with `PROFILE_ACCESS_WRITE_FAILED`; it never adds the
    target back.
  - `endpoint_bind_address` + `validate_endpoint_bind_address` resolve the
    public DNS/IP locator to one numeric binding basis and preflight it before
    any side effect; `endpoint_listen_address` and `_bound_address_matches`
    share that basis.
  - `refresh_endpoint` precomputes binary identity/argv before `Popen`, guards
    the start-to-record interval with `_reclaim_failed_endpoint_child`, and
    wraps every step failure with `_endpoint_refresh_failure` for typed
    `not_refreshed` partial facts.
- `scripts/host-service/test_host_trust_public.py`
  - adds `test_endpoint_bind_basis_public` and
    `test_refresh_missing_binary_typed_failure`.
- `docs/host-service.md`
  - documents the numeric bind basis, the start/persist guard, and the
    committed-membership export rule.
- `docs/runs/m1-service-endpoint-refresh-r1-20261005/real_refresh_e2e.py`
  - adds the explicit authorized grant before export, a public CLI
    remove-then-export refusal, and a real waiter cancellation in CASE 9.
- `docs/runs/m1-service-endpoint-refresh-r1-20261005/r2/`
  - `findings_harness.py`, `run.sh`, and `raw/` before/after pairs for F1-F5.

## Evidence

See `report.md` and `notes.md`. Every real command ran through
`run.sh <logbase> <command...>` with a matching `.exit`; all green commands exit
0. The real refresh harness is 30/30 cases ok. `raw/git_diff_check_badpath.*`
keeps one red result (a wrong `-C` path, exit 129) that was corrected in
`raw/git_diff_check.*`.

## What the controller must do next

1. Fetch the latest `origin/main` and rebase/combine if it moved.
2. Run the independent architecture review on this candidate against the five
   findings and the no-regression requirement.
3. On PASS, commit the delta, merge to `main`, push, and verify the remote
   receipt. Installation/restart/OTA are not part of this node.

## Residual risk

- The DNS bind basis takes `getaddrinfo(...)[0]`; on this host that is `::1`
  for `localhost`. A host whose DNS resolves to a non-local address is refused
  by the preflight rather than silently rebound.
- The finding harness injects one `write_json` failure for F3; the real refresh
  harness exercises the unmodified persistence path.

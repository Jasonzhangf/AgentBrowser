# Host trust authority r3 source trace (append-only handoff)

Status: `TRUST_REPAIR_AUTHOR_VERIFIED` / `PAIRED_EXPORT_E2E_WAIT`.

This is the author-side record for the four r2 owning-boundary review repairs.
It does not rewrite the r2 raw evidence. Section 5 is an append-only correction
of the r2 `source-trace.md` overclaim; the original r2 file is left untouched.
The parent owns paired product E2E, architecture review, integration, and the
launcher HOME.

## 1. Candidate identity

| Item | Value |
| --- | --- |
| worktree | `/Volumes/Intel/playground/agentbrowser/m1-host-trust-authority-r3-20261004` |
| branch | `codex/m1-host-trust-authority-r3-20261004` |
| start (origin/main) | `bc9d67ad037be2c7119bee692f9ad362417ffd05` |
| start (r2 final) | `d6fac360c2528eb74f859bad634a171705e1aec1` / tree `a45da5cf1a7f27cd7d85d448b5f8172e9c0173f4` |
| python | 3.14.6; cryptography 46.0.2 (installed) |
| openssl | `/opt/homebrew/bin/openssl` 3.6.3 |
| node / npm | v22.22.2 / 10.9.7 |

## 2. Owned source changes (allowlist)

| Path | Role |
| --- | --- |
| `scripts/host-service/host_service.py` | one public mutation lock; one status owner; IPv6 URL producer |
| `scripts/host-service/test_host_trust_public.py` | public red/green regressions for all four issues |
| `docs/host-service.md` | contract text for lock, status, URL, export denials |

No AB app, package, protocol, OB, registry producer, SDK, hook, AGENTS, package
config, or design graph was changed. No second lock, store, registry, or
transactional framework was added. Source hashes are in `raw/checks.txt`.

## 3. The four repairs

### 3.1 Public mutation lock (review P1 line 1995)

`enroll_device` and `revoke_device` now take the one shared
`run/.start.lock` (via `acquire_start_lock(root, blocking=True)`) across the
complete registry read, mutation, registry commit, and projection publication.
The old bodies moved to private `_enroll_device_locked` / `_revoke_device_locked`
helpers. The `trust provision` / `trust revoke` CLI wrappers now call the same
public functions and no longer take the lock, so there is no double locking.
Blocking acquisition serializes concurrent callers instead of failing them, so
every successful receipt survives. `PARTIAL_REGISTRY_COMMIT` still returns the
committed revision, active grants are still rebound, and revoked devices stay
excluded.

RED (pre-repair source): `raw/red-public-consumer.stderr` shows the new public
consumer failing with `successful module enroll is not committed` after six
concurrent module enrolls and three concurrent CLI revokes lost updates.

GREEN: the same consumer passes; `test_public_mixed_mutation_no_lost_success`
launches six module enrolls and three CLI revokes behind an external flag-file
barrier, then asserts every successful enroll is committed and active, every
successful revocation survives, the revision equals `before + successes`, every
active grant is current, the projection equals the derived registry projection,
and device records do not outnumber committed devices.

### 3.2 One status owner (review P1 line 2347)

`trust_status(root)` is the single public calculation. `command_trust_status`
emits it and `trust_summary` is a thin subset of it, so the CLI and module agree
for init/enroll/revoke, published-but-not-applied, and corrupted/stale
projection. It reads the registry file once, decodes and validates those same
bytes (`parse_trust_registry`), and compares the published projection bytes to
the exact projection derived from the committed registry, so a mismatched
client set is caught, not just a mismatched header.

`projection_state` is the publication fact (`missing`/`current`/`stale`).
`authorization_state` reports endpoint-applied authorization and stays
`not_refreshed` with `endpoint_pid: null`; the declared `deny_all`/`refreshed`
values are reserved for the endpoint owner's typed receipt and are never
inferred from a file, PID, health probe, or log.

RED (pre-repair source): `raw/red-focused-status-ipv6.txt` shows
`trust_summary` returning `authorization_state: "refreshed"` with
`endpoint_pid: null` after one enroll with no endpoint application.

GREEN: `test_status_owner_and_export_denials` asserts CLI/module agreement for
`not_refreshed` and `current`, and asserts a corrupted projection reports
`stale`/`not_refreshed` with no invented PID.

### 3.3 IPv6 URL authority (review P1 line 2288)

New public `endpoint_ws_url(host, host_kind, port)` is used only by
`export_bundle`. It brackets an IPv6 literal with `ipaddress` from the standard
library while the certificate SAN keeps the unbracketed address. DNS and IPv4
behavior are unchanged.

RED (pre-repair source): `raw/red-focused-status-ipv6.txt` shows
`wss://fd7a:115c:a1e0::1:8443/` failing to parse
(`Port could not be cast to integer value as '115c:a1e0::1:8443'`).

GREEN: `test_ipv6_url_san` initializes a real IPv6 endpoint, verifies the
generated server certificate with `openssl`, normalizes the real SAN IP to
`fd7a:115c:a1e0::1`, and asserts the produced URL parses to scheme `wss`, host
`fd7a:115c:a1e0::1`, port `8443`, and no userinfo. IPv4 and DNS stay unbracketed.

### 3.4 Export denial evidence (review P1 `source-trace.md:112`)

`test_status_owner_and_export_denials` now calls the public
`export_bundle`/`trust export` boundary with deliberately invalid inputs and
asserts typed refusal plus no output file, no revision advance, no device count
change, and unchanged registry/projection/device-record bytes. Covered:
malformed membership, membership `device_id` mismatch, membership `profile_id`
mismatch, missing catalog receipt, missing endpoint receipt, endpoint PID,
revision, registry digest, and projection digest mismatches, corrupted
published projection, a registry whose active grant is not current, and a stale
endpoint receipt after a newer registry mutation. The CLI export path is
covered as `PROFILE_CATALOG_UNAVAILABLE` with no output.

## 4. Author commands and results

| Command | Result | Evidence |
| --- | --- | --- |
| `python3 scripts/host-service/test_host_trust_public.py` | exit 0, `HOST_TRUST_PUBLIC_CONSUMER_OK` | `raw/green-public-consumer.*` |
| `python3 scripts/host-service/test_host_service.py` | exit 0, `HOST_SERVICE_DEV_FIXTURE_PASS` (dev fixture, not live Host acceptance) | `raw/green-dev-fixture.*` |
| `python3 -m py_compile scripts/host-service/host_service.py scripts/host-service/test_host_trust_public.py` | exit 0 | `raw/checks.txt` |
| `npm ci --offline` | exit 0 (17 packages, no package changes) | `raw/npm-ci.*` |
| `npm run verify:local` | exit 0, 8 tests, UI build | `raw/verify-local.*` |
| `git diff --check -- scripts/host-service docs/host-service.md docs/runs/m1-host-trust-authority-r3-20261004` | exit 0 | `raw/checks.txt` |

## 5. Append-only correction of the r2 source-trace overclaim

The r2 `docs/runs/m1-host-trust-authority-r2-20261004/source-trace.md` line 112
claimed: "export also rejects stale projection or mismatched typed receipts."
That was not executed. `raw/red-source-trace-gap.txt` records that the r2 public
consumer contains zero `export_bundle` and zero `membership_receipt` references.
The r2 consumer only reached `PROFILE_CATALOG_UNAVAILABLE` before any receipt or
projection check. The corrected scope for that historical run is: premature
export refusal at `PROFILE_CATALOG_UNAVAILABLE` was verified; stale/mismatched
receipt and stale-projection rejection were NOT covered by the r2 consumer.
The r3 consumer in section 3.4 is the first executed public coverage of those
denials. The original r2 raw record is retained unchanged.

## 6. Dependencies left explicit (WAIT)

| Gap | Owner | Current behavior |
| --- | --- | --- |
| Host catalog membership receipt | OB Host catalog | export refuses `PROFILE_CATALOG_UNAVAILABLE` |
| endpoint applied authorization receipt | OB endpoint / Profile owner | export refuses `ENDPOINT_REFRESH_REQUIRED` / `PROJECTION_MATERIAL_CONFLICT`; status stays `not_refreshed` |
| authenticated successful bundle export + Android consumption | paired catalog/endpoint product | `NOT_RUN`; direct PKCS#12 primitive verification is not product export |
| real Host lifecycle subprocess acceptance | deployment owner | requires a real `obscura-host` + protocol version; out of scope |

No substitute receipts, no fake endpoint PID, no fallback, and no
fixture-as-usable-bundle were used.

## 7. Resource accounting

- RED scratch under `/tmp/ab-r3-red` was created for pre-repair evidence and
  removed after the raw outputs were copied into `raw/`.
- `npm run verify:local` generated UI output under
  `apps/android/app/build/generated/probeAssets/ui`; it was removed.
- Ignored `node_modules/` from `npm ci --offline` remains for the parent hooks.
- No user key, auth/profile data, device, network, shared target, daemon, or
  release resource was touched. The launcher HOME stays parent-owned.
- This worktree is retained for parent intake and is not deleted here.

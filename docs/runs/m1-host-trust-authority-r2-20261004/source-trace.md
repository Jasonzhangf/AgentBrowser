# Host trust authority r2 source trace

Status: `TRUST_MODULE_AUTHOR_VERIFIED` /
`CATALOG_ENDPOINT_PRODUCT_E2E_WAIT`. This is the author-side evidence record
for the Host-service trust primitives and the frozen provisioning API. The
parent owns the paired runtime, BB01, milestone and independent review.

## 1. Candidate identity

| Item | Value |
| --- | --- |
| worktree | `/Volumes/Intel/playground/agentbrowser/m1-host-trust-authority-r2-20261004` |
| branch | `codex/m1-host-trust-authority-r2-20261004` |
| accepted source start | `dacbb8436981674927a741e561e2cbf58688a496` / tree `b29cd8d4e70699219fa19e8f798b3b501ee83afd` |
| author commit | `23c6eb320680d2106fd984e0f75097b2cd59c0e0` / tree `f40ec47888be3cc7435e609fe206c797e0a363eb` |
| AB origin/main (parent fetch) | `bc9d67ad037be2c7119bee692f9ad362417ffd05` |
| OB terminal (design, read-only) | `687db20bb4e08211d1144996befbbf1557d5641e` / tree `905cce37a9038f667c040018d5217cd755e68862` |
| OB profile r1 design docs | `/Volumes/Intel/playground/obscura/m1-profile-eof-contract-r7-20261004` |
| openssl | `/opt/homebrew/bin/openssl` `3.6.3` |
| python | `3.14.6`; `cryptography` `46.0.2` (installed) |

## 2. Product source changes (allowlist)

| Path | Role | SHA-256 |
| --- | --- | --- |
| `scripts/host-service/host_service.py` | trust primitives + CLI + freeze API | (see `raw/source-hashes.txt`) |
| `scripts/host-service/test_host_trust_public.py` | public CLI/module consumer | (see `raw/source-hashes.txt`) |
| `docs/host-service.md` | local trust authority contract | (see `raw/source-hashes.txt`) |

The single existing service remains the sole authority, config and registry
owner. No second service, registry, or shadow client truth was created.

## 3. Public freeze API

CLI (`scripts/host-service/host-service`):

```text
trust init      --root ROOT --endpoint-host HOST [--endpoint-port PORT] [--validity-days DAYS]
trust status    --root ROOT
trust provision --root ROOT --label LABEL --scope S ... --initial-profile default_profile|profile_id:<uuid> [--validity-days DAYS]
trust revoke    --root ROOT --device-id ID
trust export    --root ROOT --device-id ID --output PATH
```

Public module functions (for the future Mac `HostProvisioningBridge`):

```text
trust_init_locked(args, root) -> int
trust_summary(root) -> {authorization_revision, authorization_state, registry_sha256, projection_sha256, endpoint_pid}
enroll_device(root, *, label, scopes, selection, validity_days) -> enroll receipt
revoke_device(root, device_id) -> revoke receipt
refresh_projection(root, registry, registry_bytes) -> (projection_bytes, registry_sha256)
build_device_transfer_p12(root, device_id, transfer_code) -> p12 bytes
export_bundle(root, device_id, output_path, *, membership_receipt, endpoint_receipt) -> export receipt
projection_content(registry_sha256, authorization_revision, clients) -> projection bytes
generate_transfer_code() -> str
```

Sensitive in-process values are kept separate from safe JSON projection:
`build_device_transfer_p12` and `export_bundle` return the one-time transfer
code only in process; the CLI export path strips it before printing. No private
key or transfer code is written to stdout, argv, the bundle, the registry, or
logs.

Typed errors: `TRUST_NOT_INITIALIZED`, `TRUST_ALREADY_INITIALIZED`,
`REGISTRY_SCOPE_REQUIRED`, `MISSING_INITIAL_PROFILE_GRANT`,
`ENDPOINT_HOST_INVALID`, `REGISTRY_WRITE_FAILED`, `PROJECTION_WRITE_FAILED`,
`PARTIAL_REGISTRY_COMMIT`, `DEVICE_NOT_FOUND`, `BUNDLE_NOT_READY`,
`PROFILE_CATALOG_UNAVAILABLE`, `ENDPOINT_REFRESH_REQUIRED`,
`PROJECTION_MATERIAL_CONFLICT`, `BUNDLE_WRITE_FAILED`, `TRANSFER_CODE_INVALID`,
`CERT_VALIDITY_INVALID`, `DEVICE_LABEL_INVALID`.

## 4. Declared projection vectors

`authorized-clients.json` carries no `projection_sha256` self-hash. Digests
below were recomputed with public `shasum -a 256` and `openssl dgst -sha256`
against the frozen design vectors (see `raw/vector-check.txt`).

| Vector | Bytes | External SHA-256 |
| --- | --- | --- |
| empty revision 0 | 146 | `543c99d61e58f6051e5024a233b1fe084afecad4829f515f279908d0fccdc73a` |
| nonempty revision 7 | 445 | `ab48af7fd6b7360bb8424643981b54033fee67c8cd4bf591bd2252069b7e98c5` |

## 5. Author commands and results

| Command | Result | Evidence |
| --- | --- | --- |
| `python3 -m py_compile scripts/host-service/host_service.py scripts/host-service/test_host_trust_public.py` | exit 0 | `raw/checks.txt` |
| `python3 scripts/host-service/test_host_trust_public.py` | exit 0, `HOST_TRUST_PUBLIC_CONSUMER_OK` | `raw/public-consumer.*` |
| `python3 scripts/host-service/test_host_service.py` | exit 0, `HOST_SERVICE_DEV_FIXTURE_PASS` | `raw/existing-dev-fixture.*` |
| `npm run verify:local` | exit 0 | `raw/verify-local.*` |
| `git diff --check` | exit 0 | `raw/checks.txt` |

The public consumer asserts, from the real CLI/module boundary only:

1. `trust init` creates the CA/server keys and certs, `schema_version=2`
   revision-0 registry, and empty deny-all projection; private modes hold.
2. Real `openssl verify` of CA, server (SAN `IP:100.64.0.1`, serverAuth) and
   client certificates.
3. Reinitialize is refused with `TRUST_ALREADY_INITIALIZED` and preserves bytes.
4. A enroll then B enroll preserves A scopes/selection/cert/`scope_revision`
   while rebinding A's grant freshness to the current global revision.
5. A revoke keeps B active and current, excludes A from the projection.
6. Invalid scope or selection creates no device and does not advance revision.
7. Exact external projection digest and absence of a self-hash field.
8. Declared empty/nonempty vector bytes and digests.
9. A real own-filesystem projection write failure returns
   `PARTIAL_REGISTRY_COMMIT` at exit 4 with the actual committed revision.
10. Premature export returns `PROFILE_CATALOG_UNAVAILABLE` and leaves no output.
11. Real PKCS#12 with exactly `agentbrowser-host-ca`, `agentbrowser-endpoint`,
    `agentbrowser-client`; `openssl` extracts a client cert matching the
    enclosed key and the enrolled DER.

## 6. Dependencies left explicit

| Gap | Owner | Current behavior |
| --- | --- | --- |
| Host catalog membership receipt | OB Host catalog | export refuses `PROFILE_CATALOG_UNAVAILABLE` |
| endpoint applied auth receipt | OB endpoint/Profile r4 owner | export refuses `ENDPOINT_REFRESH_REQUIRED` / `PROJECTION_MATERIAL_CONFLICT` |
| endpoint projection consumption | OB endpoint owner | not implemented here |
| Mac `HostProvisioningBridge` + UI | `apps/macos` owner | API frozen only |
| Android import/connection E2E (BB01) | Android/connection owners | `WAIT_DEPENDENCY` |
| real Host lifecycle subprocess acceptance | deployment owner | requires real `obscura-host` + protocol version |

No substitute receipts, no mock catalog/endpoint, and no anonymous or default
profile path exist. Export cannot reach `usable` until the real receipts exist.

## 7. Resource accounting

- Owned test roots under `/tmp/ab-trust-*`, `/tmp/abp12*` and the scratch
  `p12`/`dbg` files were removed with precise `Path.unlink`/`shutil.rmtree`.
- The smoke test's `apps/android/app/build/generated/probeAssets/ui` output
  (created by `npm run verify:local` build:ui) was removed; ignored
  `node_modules/` from `npm ci` remains for the parent's hooks.
- No user keys, auth/profile data, device, network, or release resource was
  touched. The launcher HOME stays parent-owned.
- This worktree is retained for parent intake and is not deleted here.

# Mac Host user-slice index receipt

Status: **INCOMPLETE / EVIDENCED PARTIAL**. This file is an index only. It does
not add new evidence and it does not claim M1 or `USER_SLICE_ACCEPTED`.

The authoritative Mac/Host receipts are written by their owning runs and live
under `evidence/user-slice/host/*/receipt.md`. Those files are force-added to
Git (see the path list below) so this index is auditable at HEAD.

## Host receipts and the gates they prove

| Receipt | Gates proven |
| --- | --- |
| `evidence/user-slice/host/user-slice-host-20261010T1405/receipt.md` | Mac app build and integrated app/source contract at candidate `d14daff`; first Host run, which preserved an `ENDPOINT_PID_MISMATCH` failure |
| `evidence/user-slice/host/user-slice-host-20261010T1418/receipt.md` | Dedicated service `com.agentbrowser.task6` at protocol 11, lifecycle restart, endpoint PID `94333`, trust export |
| `evidence/user-slice/host/user-slice-host-20261010T1424/receipt.md` | Lifecycle start/status/`trust endpoint-refresh`/`trust export` on the dedicated service root |
| `evidence/user-slice/host/user-slice-host-handoff-20261010T143236/receipt.md` | `trust export --include-transfer-code` native-owner handoff; default CLI stays redacted and the code is not written to the bundle JSON, logs, argv, or the receipt |

## Live Host run for the real-user-path run (task-11)

Run `host-live-20261010T1720` started the dedicated task6 instance through the
lifecycle entrypoint and left it RUNNING for the Android ordinary-UI run.

- Lifecycle start: **PASS**. Raw line: `"pid": 29244, "protocol_version": 11, "service_label": "com.agentbrowser.task6", "socket_dir": "/tmp/abn-task6-runtime/host-1fcd41fb", "state": "running"`.
- Status: **PASS**. `state=running`, `protocol_version=11`, `socket_permissions=0o600`, `endpoint_generation=24`, `endpoint_pid=67508`.
- Endpoint refresh: **PASS**. Raw line: `"authorization_state": "refreshed", "bound_address": "127.0.0.1:51880", "endpoint_generation": 24, "endpoint_pid": 67508`.
- LAN bind: **FAIL**. The declared `trust/endpoint.json` reads `endpoint_host=127.0.0.1` and `endpoint_port=51880`; `trust endpoint-refresh` accepts only `--root` and `--timeout`, and `trust init` refuses reinitialization, so the LAN address `192.168.0.3` cannot be bound through the declared lifecycle path.
- Tailscale bind: **FAIL** for the same reason; live Tailscale IPv4 was `100.66.1.82`.
- Emulator reachability: **PASS** through two routes. Raw lines: `EMU_TCP_OK` for `nc -z -w 3 10.0.2.2 51880`, and `EMU_LOOPBACK_VIA_REVERSE_OK` for `nc -z -w 3 127.0.0.1 51880` after `adb -s emulator-5554 reverse tcp:51880 tcp:51880` (reverse list shows `host-16 tcp:51880 tcp:51880`). The reverse mapping is the route that matches the bundle's declared `127.0.0.1:51880` endpoint.
- Provision: **PASS**. Device `89f994d4-e12b-4a26-b7d9-9ac5aad9918c`, scopes `observe_human_control`, `profile_read`, `profile_create`, `profile_select`, `catalog_membership=unavailable` before the explicit grant.
- Membership: **PASS**. `receipt_type=export_eligible`.
- Export: **PASS**, redacted (no `--include-transfer-code`). Bundle SHA-256 `3a338fae1c37ad0d7e73bdbbfebfbeef3c2b0496bb298e38ff7a6cf317ab60ff`. The bundle itself stays untracked.
- Host left RUNNING for the Android run. The transfer code is absent from every artifact here.

## Related receipts outside this index

- `docs/runs/m1-user-slice-first-connect/receipt.md` owns the Android ordinary
  import/admission and browse gates.
- `docs/runs/m1-user-slice-tailscale/receipt.md` owns the Tailscale scope gates.
- `docs/runs/m1-user-slice-android/receipt.md` owns the installed-APK artifact
  and IME gates.

## Scope notes

- These receipts prove Host lifecycle, endpoint refresh and trust export on the
  dedicated service root. They do not prove a same-session end-to-end browse
  claim by themselves.
- Cross-node Tailscale remains **UNKNOWN** (designated 15T `100.104.163.65`
  offline).
- Chinese composition remains an environment-blocked **UNKNOWN**.

## Force-added evidence paths

```text
evidence/user-slice/host/user-slice-host-20261010T1405/receipt.md
evidence/user-slice/host/user-slice-host-20261010T1418/receipt.md
evidence/user-slice/host/user-slice-host-20261010T1424/receipt.md
evidence/user-slice/host/user-slice-host-handoff-20261010T143236/receipt.md
evidence/user-slice/host-live-20261010T1720/start.json
evidence/user-slice/host-live-20261010T1720/status.json
evidence/user-slice/host-live-20261010T1720/status-final.json
evidence/user-slice/host-live-20261010T1720/endpoint-refresh.json
evidence/user-slice/host-live-20261010T1720/endpoint-refresh-2.json
evidence/user-slice/host-live-20261010T1720/provision.json
evidence/user-slice/host-live-20261010T1720/membership.json
evidence/user-slice/host-live-20261010T1720/export.json
evidence/user-slice/host-live-20261010T1720/bundle-sha256.txt
evidence/user-slice/host-live-20261010T1720/mac-tcp-probe.txt
evidence/user-slice/host-live-20261010T1720/emulator-tcp-probe-final.txt
evidence/user-slice/host-live-20261010T1720/lan-ip.txt
evidence/user-slice/host-live-20261010T1720/tailscale-ip-4.txt
evidence/user-slice/host-live-20261010T1720/adb-devices.txt
evidence/user-slice/host-live-20261010T1720/redacted-gates.txt
```

Only the receipts and the non-secret lifecycle receipts above are force-added.
The remaining Host run artifacts (exported bundles, logs, device records) stay
untracked under `evidence/` because they contain or reference private
connection material.

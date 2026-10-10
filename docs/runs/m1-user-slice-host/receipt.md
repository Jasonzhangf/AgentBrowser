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
```

Only the receipts are force-added here. The remaining Host run artifacts
(`status-before.json`, exported bundles, logs) stay untracked under `evidence/`
because they contain or reference private connection material.

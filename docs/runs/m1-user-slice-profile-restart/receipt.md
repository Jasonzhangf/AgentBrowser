# Task-16 profile persistence across formal restart

Status: **INCOMPLETE / FIRST-GATE FAILURE**.

Scope was restricted to the task6 service root `$HOME/Library/Application Support/AgentBrowser/host-service`, label `com.agentbrowser.task6`. The LAN root, task8 root, shared Hosts, and `weibo` profile were not touched. The task6 Host remained running throughout and after this attempt: Host PID `29244`, endpoint PID `67508`, endpoint generation `24`, protocol `11`.

## Per-gate results

| Gate | Result | Evidence |
| --- | --- | --- |
| Ordinary Mac client launch | PASS | `evidence/user-slice/profile-restart-20261010T1820/app-pid-2.txt`, bridge child observed |
| Real Mac client connection | **FAIL — first failing gate** | `first-failing-gate.txt`, `ax-after-connect.txt`, `app-after-connect.png` |
| Real HTTPS browsing | NOT REACHED | blocked by first gate |
| Durable cookie/localStorage state | NOT REACHED | blocked by first gate |
| `default` profile isolation | NOT REACHED | blocked by first gate |
| Formal lifecycle restart | NOT RUN | blocked by first gate; task6 Host left running |
| Reconnect and state read-back | NOT REACHED | blocked by first gate |

## First failing gate

The ordinary `AgentBrowserMac` GUI launched with a private pairing directory built
from the task6-mac device, and its `AgentBrowserMacBridge` child appeared. The
pairing directory was private and contained only the declared endpoint, CA, client
certificate, and mode-0600 client key. No bundle or transfer code was written to
this evidence.

After opening the visible connection drawer and pressing the visible `连接` action,
the UI stayed:

- `未连接`
- `需要配置连接`
- `状态 idle`
- `Transport unknown`
- `Path unknown`

The connection setup projection stayed required/unconfigured. Therefore the
ordinary Mac client did not connect to the Host through this path. No direct
socket harness was substituted. No HTTPS navigation or profile-state claim is
made.

## Pairing source and retry investigation

The ordinary Mac client reads credentials from the path passed by
`--pairing-dir`, which `apps/macos/Sources/main.swift:2176-2188` passes to the
bridge as `AGENTBROWSER_MAC_PAIRING`. The bridge implementation is explicit:
`apps/macos/connection-bridge/src/main.rs:1464` reads that environment variable;
`1468-1483` separately probes the Host admin socket; `1485-1497` loads exactly
`endpoint.txt`, `ca.der`, `client.der`, and `key.der`; and `1500-1512` validates
those files, including restrictive `key.der` permissions. Connection then calls
`Connector.connect(pairing, None)` at `819-861`.

The private pairing directory was present and valid, so the retry did not fail
from a missing credential file. The ordinary Mac UI still reported `未连接`,
`需要配置连接`, `状态 idle`, `Transport unknown`, and `Path unknown` after the
visible `连接` action. Host status remained running with PID `29244`, endpoint
PID `67508`, generation `24`; no remote `session_id` appeared.

There is no native import step in the bridge dispatch: `apps/macos/connection-bridge/src/main.rs:500-519`
contains `status` and `connect`, while no `import_connection` or
`open_connection_setup` operation exists in that native dispatch. The UI command
names are only consumer-frame commands (`packages/ui-plugins/consumer-frame.ts:540-555`)
and do not establish a real native pairing import. The result is therefore a
missing product capability in the ordinary Mac client connection-setup path,
not a missing pairing directory or transfer-code error.

No HTTPS, durable state, profile-isolation, restart, or reconnect claim is made.

## Resource status

Only the task16 Mac app processes were stopped after capture (owned PIDs `29523`
and `87667`, with their bridge children). The task6 lifecycle Host was not
stopped or restarted. Its live status remained `state=running`, PID `29244`,
endpoint PID `67508`, generation `24`. The LAN lifecycle remained unchanged and
running (PID `2185`, endpoint PID `5534`, generation `1`).

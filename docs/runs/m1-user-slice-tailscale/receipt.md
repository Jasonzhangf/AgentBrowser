# Task-8 Tailscale interface scope receipt

Result scope: **same physical Mac over the Tailscale interface**. Cross-node remains **UNKNOWN**.

## Live network evidence

- Mac Tailscale IPv4: `100.66.1.82` (online).
- Designated 15T `100.104.163.65`: `offline, last seen 8h ago` in the live status capture.
- Emulator: `emulator-5554 device`.
- Raw captures: `evidence/user-slice/tailscale/tailscale-20261010T1615/tailscale-status.txt`, `tailscale-ip-4.txt`, and `adb-devices.txt`.

## Dedicated Task-8 lifecycle

The first Tailscale attempt reused and cycled the task6 instance (restart PID 70574; stop evidence is retained under the first-run evidence directory), then the run moved to the dedicated Task8 instance. The task6 trust root material was not modified. Task8 used service root `$HOME/Library/Application Support/AgentBrowser/host-service-task8`, short private runtime `/tmp/abn-task8-runtime` and profile `/tmp/abn-task8-profile` (both `0700`), service label `com.agentbrowser.task8`, protocol 11, and the same r8 Host binary SHA-256 `d8e956a22809629b50366a51298d94552284ac7519a06e4ab17c000bde62dd21`.

- Lifecycle start: **PASS**. Socket `/tmp/abn-task8-runtime/host-eca88be0`, mode `0600`.
- Endpoint refresh: **PASS**. Raw result: `authorization_state=refreshed`, `bound_address=100.66.1.82:51881`, `endpoint_generation=2`, `endpoint_pid=59549`.
- Catalog membership: **PASS**.
- Bundle export: **PASS**. Bundle SHA-256: `7adcb281cc779554e2d99c9eedb12c17c8f97cfad51d5f0933addc23a74897f9`.
- Mac TCP reachability: **PASS**. Raw probe: `Connection to 100.66.1.82 port 51881 [tcp/*] succeeded!`.
- Android ordinary UI reached the Tailscale endpoint and completed admission plus one browse interaction: **PASS**. The stronger UI evidence is recorded below; no separate emulator-side TCP probe is claimed.

## Ordinary Android UI gates

- Ordinary DocumentsUI import/admission: **PASS**.
- Ordinary connection/configuration: **PASS**.
- Ordinary browse interaction: **PASS**.
- Android instrumentation: `OK (1 test)` for admission and browse runs.
- Browse evidence: `renderedFrames=2`, `navigatedFixturePage=true`, `redBeforeClick=true`, `limeAfterClick=true`, `nativePixelsChanged=true`.
- The real click repainted the Host page control from red to lime.
- Caveat: the service-lifecycle Host exposes no independent DOM read-back channel. The click result is therefore evidenced by Host-applied repaint and native pixel change, not by an independent DOM read.

## Host cleanup

Task8 is verified stopped after the Android run. Task8 service PID `42645` is absent in `evidence/user-slice/tailscale/tailscale-20261010T1615/task8-post-stop-ps.txt`; its state file is absent in `task8-state-check.txt`; and its launchd job is absent in `task8-launchd-check.txt`. The earlier `host-stop.json` and `post-stop-ps.txt` files are explicitly task6-instance evidence (task6 PID `70574`), not Task8 stop proof. Task6 root trust material was not modified, although the first attempt did cycle the task6 service before the dedicated Task8 instance was created.

No cross-node Tailscale result is claimed. No M1 completion claim is made.

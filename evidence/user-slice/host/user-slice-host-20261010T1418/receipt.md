# Mac Host user-slice retry receipt

This is a fresh retry after the prior run's preserved `ENDPOINT_PID_MISMATCH` failure in `user-slice-host-20261010T1405`.

## Host and export

The dedicated service was already configured with protocol 11 and was restarted only through the project lifecycle entrypoint. `status-before.json` records the running dedicated Host:

- service: `com.agentbrowser.task6`
- protocol: `11`
- Host PID: `94286`
- endpoint PID: `94333`
- Host SHA-256: `d8e956a22809629b50366a51298d94552284ac7519a06e4ab17c000bde62dd21`
- runtime: `/tmp/abn-task6-runtime`
- socket dir: `/tmp/abn-task6-runtime/host-1b324658`
- authorization state: `refreshed`
- endpoint generation: `2`

`trust endpoint-refresh` returned `state=refreshed`, bound address `127.0.0.1:51880`, generation `2`, endpoint PID `94333`.

The retry command was:

```text
scripts/host-service/host-service trust export \
  --root "$HOME/Library/Application Support/AgentBrowser/host-service" \
  --device-id e22b76b6-31ac-487b-9953-13e0d78daec8 \
  --output evidence/user-slice/host/user-slice-host-20261010T1418/task6-mac.abconnection
```

Result: **PASS**.

```json
{"authorization_revision":1,"projection_sha256":"2e87226e0f95f346ed7f19a1ab928299f5194fb8bdf1e94c36eb0d07cc824a0b","registry_sha256":"eddaf8ffc4c6eaa4773392b546fa8ae1cc66de18537d9144406d44e4355d1089","state":"exported"}
```

Bundle path: `task6-mac.abconnection`
Bundle SHA-256: `ee42a8c22455e0cd021498a480107ed6b12408349a0226197c095e242b3251fb`
Permissions: `0600`.

The earlier `ENDPOINT_PID_MISMATCH` evidence is preserved unchanged in the previous run directory. The dedicated Host was stopped through the lifecycle entrypoint after this retry. Shared Host processes were not touched.

## Integrated Mac provisioning menu

Integrated app bundle:

- `apps/macos/build/AgentBrowserMac.app`
- AgentBrowserMac SHA-256: `f5bbaae900d95b6fda54838a031569f32514d11df8da5b5047b88d4aa6ceaa6f`
- bundle ZIP SHA-256: `04c3ef0112c446a798e9283e214df601b4952110cf4696e8657daed0a975bbae`

The real integrated app was launched from that bundle. AppleScript System Events exercised the ordinary menu path:

```text
tell application "System Events" to tell process "AgentBrowserMac" to click menu item "配置手机连接…" of menu 1 of menu bar item "连接" of menu bar 1
```

Raw result: exit `0`, with the selected menu item reported as `配置手机连接… of menu 连接`.

Screenshots:

- `app-window.png` SHA-256 `ef48c60551a6bdb4455d30d3c11eb5a9d7b5f4b4121831b4386b4dcb979725b1`
- `provisioning-sheet.png` SHA-256 `8ebbd79ac1abcc1cf16396b80354fbabe2299408eef4d74fca0c392edf0ec8a8`

The provisioning sheet is visible in the retained screenshot. This proves the integrated ordinary provisioning menu path opens successfully against the built app. The export bundle itself was produced by the fixed Host lifecycle/trust path above, and its identity is recorded without exposing contents.

## Gate summary

- Accepted Service + Mac source integration: **PASS**, zero-conflict integration HEAD `d14daff458fad474f529035b49becd14a2ca46e0`.
- Integrated `set_presentation` contract: **PASS** at build/source path; former `UNKNOWN_COMMAND` defect is absent.
- Dedicated protocol 11 Host restart and endpoint refresh: **PASS**.
- Trust export: **PASS**, bundle produced and hashed.
- Ordinary Mac provisioning menu: **PASS**, AppleScript menu action exit 0 and provisioning sheet screenshot retained.
- M1 completion: **not claimed**.

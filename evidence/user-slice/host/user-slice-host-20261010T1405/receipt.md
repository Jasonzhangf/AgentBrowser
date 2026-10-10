# Mac user-slice Host integration receipt

Candidate integration HEAD: `d14daff458fad474f529035b49becd14a2ca46e0`, based on candidate `02330b7`, Service `a5d19bc`, Mac provisioning `1cf4294`.

## Integrated app and source contract

The integrated app was built with:

```text
OBSCURA_PROTOCOL_ROOT=/Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009/protocol/browser PATH="$PWD/node_modules/.bin:$PATH" scripts/build-macos.sh build
```

Bundle: `apps/macos/build/AgentBrowserMac.app`
App executable SHA-256: `f5bbaae900d95b6fda54838a031569f32514d11df8da5b5047b88d4aa6ceaa6f`
Bundle ZIP: `generated/modules/macos-shell/lib/AgentBrowserMac.app.zip`
Bundle ZIP SHA-256: `04c3ef0112c446a798e9283e214df601b4952110cf4696e8657daed0a975bbae`

The integrated `main.swift` contains the `set_presentation` handler at lines 2381, 2436 and 2449. This agrees with the UI operation and removes the prior `UNKNOWN_COMMAND` source defect.

## Fixed Host service

Commands used:

```text
scripts/host-service/host-service install --root "$HOME/Library/Application Support/AgentBrowser/host-service" --binary /Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009/target/release/obscura-host --protocol-version 11 --runtime-root /tmp/abn-task6-runtime --profile-root /tmp/abn-task6-profile --service-label com.agentbrowser.task6
scripts/host-service/host-service start --root "$HOME/Library/Application Support/AgentBrowser/host-service"
scripts/host-service/host-service status --root "$HOME/Library/Application Support/AgentBrowser/host-service"
```

Status: **PASS**. Recorded status JSON reports `state=running`, protocol version `11`, Host PID `35717`, Host SHA-256 `d8e956a22809629b50366a51298d94552284ac7519a06e4ab17c000bde62dd21`, runtime root `/tmp/abn-task6-runtime`, socket dir `/tmp/abn-task6-runtime/host-173affc8`, and persistent profile root `/private/tmp/abn-task6-profile`.

Existing shared Host processes were inspected and left untouched.

## Trust flow

`trust init` completed: state `initialized`; CA, server, registry and projection identities are in `trust-init.json`.

The first provision attempt failed with raw `MISSING_INITIAL_PROFILE_GRANT` because `default` is not the explicit selector grammar. The second attempt with `profile_id:default` and unsupported scopes failed with raw `REGISTRY_SCOPE_REQUIRED`. These were corrected without source changes.

Final provision command:

```text
scripts/host-service/host-service trust provision --root "$HOME/Library/Application Support/AgentBrowser/host-service" --label task6-mac --scope profile_read --scope observe_human_control --initial-profile profile_id:default
```

Result: **PASS**. Device ID `e22b76b6-31ac-487b-9953-13e0d78daec8`; scopes `observe_human_control`, `profile_read`; certificate SHA-256 `b5e9048d6f027229cae2b03a495b192412fd2c23db21d9cad9f0e92a794a0eef`; authorization revision `1`.

`trust endpoint-refresh` reached `state=refreshed` with bound address `127.0.0.1:51880` and endpoint generation `1`. Admin catalog list returned revision `1`, default profile `default`, owner `local`, members `[local]`. Membership grant then returned an `export_eligible` receipt with catalog revision `2` and members `[local, e22b76b6-31ac-487b-9953-13e0d78daec8]`.

## Export and ordinary UI result

Pairing export remains **FAIL / BLOCKED**. The final raw error is:

```text
{"error":{"code":"ENDPOINT_PID_MISMATCH","message":"endpoint applied receipt PID is not a live process"}}
```

The endpoint refresh started an endpoint PID that was no longer live by export time. No `.abconnection` bundle was produced. No bundle contents, credentials, keys, or private material were printed.

The integrated app was launched from:

```text
evidence/user-slice/host/user-slice-host-20261010T1405/...
```

The independent screenshot `app-drawer.png` was captured after launch. However, this particular AX probe returned `NO_BUTTON` because the integrated app's web content was not exposed to the AX button tree in the captured state. Therefore the ordinary export menu action is **UNKNOWN**, not claimed as pass. `app-before.png` and `app-drawer.png` are retained for audit.

## Gates

- Integration merge: **PASS**. Candidate, Service and Mac provisioning commits merged without conflicts; integrated app built successfully.
- `set_presentation` contract: **PASS at integrated source/build level**. The prior `UNKNOWN_COMMAND` defect is absent in the integrated binary source path. Live drawer action: **UNKNOWN** because AX could not find the web button in this run.
- Protocol 11 fixed Host install/start/status: **PASS**.
- Trust init/provision/catalog/membership: **PASS** after correcting CLI input grammar; all outputs are retained without sensitive contents.
- Trust export: **FAIL**, first failing gate `ENDPOINT_PID_MISMATCH`; no bundle identity exists.
- Ordinary export menu result: **UNKNOWN**, blocked by missing export bundle and unavailable AX button in this run.

Next executable action: restart the dedicated Host service through the lifecycle entrypoint, rerun endpoint refresh, then retry export and the installed-app AX menu path in a fresh evidence directory. No M1 completion is claimed.

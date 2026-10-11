# Task-14 LAN user-path receipt

Result scope: **real LAN user path on a fresh protocol-11 Host root**. The task6
and task8 roots were not touched.

## Live addresses (read at run time)

- `$ ipconfig getifaddr en0` → `192.168.0.3`
- `$ tailscale ip -4` → `100.66.1.82`
- `$ adb devices` → `emulator-5554 device`

The LAN address is the endpoint host for this run. The Tailscale address is
recorded for comparison only.

## Fresh LAN root

- Service root: `$HOME/Library/Application Support/AgentBrowser/host-service-lan`
- Service label: `com.agentbrowser.lan`
- Runtime root: `/tmp/abn-lan-runtime` (short path, mode `0700`)
- Profile root: `/tmp/abn-lan-profile` (mode `0700`)
- Protocol version: `11`
- Host binary SHA-256: `d8e956a22809629b50366a51298d94552284ac7519a06e4ab17c000bde62dd21`

## Per-gate results

| Gate | Result | Raw line |
| --- | --- | --- |
| `trust init` with live LAN host | PASS | `"endpoint_host": "192.168.0.3", "endpoint_port": 51882, "state": "initialized"` |
| Lifecycle start | PASS | `"pid": 2185, "protocol_version": 11, "socket_dir": "/tmp/abn-lan-runtime/host-bd4369b6", "state": "running"` |
| Status | PASS | `state=running`, `protocol_version=11`, `socket_dir=/tmp/abn-lan-runtime/host-bd4369b6` |
| Provision | PASS | device `c02d396c-5178-42d8-9ed5-03f7e83f25c7` |
| Membership grant | PASS | `receipt_type=export_eligible` |
| Endpoint refresh | PASS | `"authorization_state": "refreshed", "bound_address": "192.168.0.3:51882", "endpoint_generation": 1, "endpoint_pid": 5534` |
| Bound address is the LAN address | PASS | `bound_address=192.168.0.3:51882`, not `127.0.0.1` |
| Endpoint process | PASS | `pid=5534 listen=192.168.0.3:51882 generation=1` |
| Mac LAN TCP probe | PASS | `Connection to 192.168.0.3 port 51882 [tcp/*] succeeded!` |
| Emulator LAN probe without `adb reverse` | PASS | `EMU_LAN_DIRECT_OK`; reverse list contained only `host-16 tcp:51880 tcp:51880`, so no mapping existed for `51882` |
| Redacted export | PASS | SHA-256 `1f1e2de4053cc2d2d7ebf07596b2a05bbb5726b258f146b0ae2e71c67521656c`, no `--include-transfer-code` |
| Host left running | PASS | Host PID `2185`, endpoint PID `5534`, `state=running`, `endpoint_generation=1` after the Android run |
| Ordinary Android UI admission over LAN | PASS | `instrument-testOrdinaryRealPathAdmission.txt` → `OK (1 test)` |
| Ordinary real HTTPS browse over LAN | PASS | `pageUrl=https://www.selenium.dev/selenium/web/web-form.html`, `finalScheme=https`, `renderedFramesBefore=3`, `renderedFramesAfter=4`, `displayedTicket=27` |
| Video + touch over LAN | PASS | reported by the Android replay owner for this run |
| System IME gate | FAIL | `mInputShown=false` with `mCurId=LatinIME`, `mServedView=RemoteImeView`, `mServedInputConnection=RemoteImeView$Connection` |

Android evidence for these gates lives in the Android worktree at
`m1-android-navigation-fix-r1-20261009/evidence/user-slice/ime-focus-20261010T1740/final-pass/`.

First failing gate: **system IME**. The raw assertion is:

```text
junit.framework.AssertionFailedError: The raised system IME must report mInputShown=true: {"mInputShown":"false","mIsInputViewShown":"true","mShowRequested":null,"mServedInputConnection":"RemoteInputConnectionImpl{connection=com.agentbrowser.probe.RemoteImeView$Connection@8ab9694","mServedView":"com.agentbrowser.probe.RemoteImeView{630c3fc VFED..... .F...... 0,0-1,1 aid=1073741824}","mCurMethodId":"com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME",...}
```

The LAN binding gate that failed on the task6 root
(`bound_address=127.0.0.1:51880`) **PASSES** here with
`bound_address=192.168.0.3:51882`.

The LAN instance stays RUNNING unchanged while the Lead records this failed
gate. It is stopped only on the Lead's stop instruction, through the lifecycle
entrypoint.

No transfer code, private key, or bundle content appears in this receipt or in
the committed evidence. The exported bundle stays untracked.

# M1 Mac native client handoff

更新时间：2026-09-05

## 范围与基线

- worker tree：`/Volumes/extension/code/AgentBrowser/playground/m1-luna-mac`
- branch：`codex/m1-luna-mac`
- base：`5d1d2884f56873e3ebfc4aaf1600ec76ba5eacf0`
- remote main observed：`eed395cf49d5c7a514d91c6efc3eba4aba7671d5`
- Browser ABI read-only checkout：`7e7b6cc547f60f5215b514415556267196ed4afb`
- scope：`apps/macos/`、Mac build scripts/docs、Mac module/map 接线及 Cargo 最小 workspace 接线
- forbidden：Android、`packages/client-connection`、shared UI/plugin/domain、relay、Obscura、共享 Host/设备

本切片不宣称 M1 完成，不宣称 Android+Mac 同 Session、Relay/选路、系统安装、发布或 main 集成。

## 实现

- AppKit `AgentBrowserMac` 加载现有 Cordis UI bundle；UI 只通过同步 typed JSON `ProbeNative.request` 访问 native bridge。
- `AgentBrowserMacBridge` 为独立 child process，复用既有 Rust `agentbrowser-connection`；控制响应与 H.264 Annex B 帧使用不同的有界 pipe framing。
- H.264 bytes 不进入 JS、Cordis、metadata 或业务 payload；AppKit 使用 VideoToolbox 解码并绘制 `CVPixelBuffer`，显示后才发送 ticketed `ack_frame`。
- 输入检查 displayed frame 的 generation/session/document/viewport identity，并要求 Host control mode 与 displayed ack；stale、未显示、无控制权和未知字段显式拒绝。
- pairing 只从 `AGENTBROWSER_MAC_PAIRING` 指向的目录读取；未把证书/私钥写入快照或 UI。

## 源码与静态验证

通过：

```text
CARGO_BUILD_JOBS=2 cargo test -p agentbrowser-macos-bridge \
  --config 'patch.crates-io.obscura-host-protocol.path="/Volumes/extension/code/AgentBrowser/playground/obscura-fork/playground/m1-form/protocol/browser"'
3 passed; 0 failed

npm test
4 passed; 0 failed

xcrun swiftc -typecheck apps/macos/Sources/main.swift \
  -framework AppKit -framework WebKit -framework VideoToolbox \
  -framework CoreMedia -framework CoreVideo -framework CoreImage -framework QuartzCore
exit 0; warning only: WKWebViewConfiguration.preferences.javaScriptEnabled deprecated
```

Rust tests cover closed local command fields、non-finite input rejection、nullable optional snapshot omission (`error: null` remains required)。

## 构建与产物

通过：

```text
OBSCURA_PROTOCOL_ROOT=/Volumes/extension/code/AgentBrowser/playground/obscura-fork/playground/m1-form/protocol/browser \
CARGO_BUILD_JOBS=2 scripts/build-macos.sh build
```

产物：`apps/macos/build/AgentBrowserMac.app`

bundle 内容：

```text
Contents/MacOS/AgentBrowserMac
Contents/MacOS/AgentBrowserMacBridge
Contents/Resources/ui/index.html
Contents/Resources/ui/app.js
Contents/Resources/ui/app.css
```

当前候选产物 SHA-256：

```text
AgentBrowserMac       8921eeb4dd4254ecb273466264b3abbd93cc16925e82205ee386156d2a9b4836
AgentBrowserMacBridge 76edababab9d95950b0ab3ef55fa3e3802c13dbf431f3223600435b18680761d
ui/index.html         dbbdcc4c68afb20e836a15b1762fef47142f147a5a4e957c8ad69f300c13259e
ui/app.js             ae0f319875863b269c6d387fe00c5769a1424de1dbc1f29f9858c8d1d2a1fef4
```

未执行系统安装；`scripts/build-macos.sh run` 直接启动 bundle，符合本模块 `deployment_operations: []`。

## 独立 loopback 入口证据

fixture 仅为独立验证资源，不是共享 Host、手机或最终双端验收：

```text
fixture dir: /tmp/an-a0d2882b6c2d476ebd447d05c69d6e08
endpoint: wss://127.0.0.1:55130
fixture exec session: 81729
```

使用已验证 Obscura fixture binary 和临时 pairing 文件；未记录 credential 内容。

已完成的 CUA 真实流程：

1. AppKit app 启动，AX tree 显示现有 Cordis UI。
2. `连接 Host` 后进入 `观察模式 · Agent 可继续操作`。
3. 收到 loopback H.264 Annex B frame；VideoToolbox format/decode/display 回调成功，随后连续发送 `ack_frame`。
4. 观察截图显示真实 fixture Host 页面，画面上下方向正确；红色 Host button 位于顶部、蓝色区域位于底部。
5. `接管页面` 后进入 `接管中 · 可点击页面和输入文字`。
6. native surface 坐标点击 `(80,70)` 后截图中 Host button 由红变绿，证明点击经过 native H.264 surface 到达 Host。
7. native surface 坐标点击远程输入框约 `(90,145)`；右侧 native typed input 设置 `native-mac-input`，发送后截图显示 Host 输入框出现相同文字。
8. `返回观察` 后 AX 显示 `观察模式 · Agent 可继续操作`，输入/发送控件 disabled。
9. `断开` 后 AX 显示等待连接文案和 `连接 Host`，视频 surface 回到等待状态。

第 1--9 步最初在带临时 native 诊断输出的构建上完成；随后仅删除 Swift 诊断日志并重建当前候选。日志删除不改变连接、解码、显示确认、控制或输入语义。日志删除后的当前候选已再次实际启动、连接同一 loopback fixture、显示真实 H.264 frame，并断开回到等待连接；上述操作证据按未改变相关语义复用，二者 artifact identity 分开记录。

未验证：scroll loopback、Android+Mac 同 Session、中文 IME、横竖屏同步、Relay/UDP/选路、断网重连、系统安装身份及正式双端验收。

资源清理：最终 candidate app 通过显式 PID 终止；独立 fixture 通过自身 `quit` stdin 退出，exit code 0。teardown 期间 fixture 打印 peer EOF / `Host media ended without Closed`，但无残留 `device_fixture`、55130 endpoint 或 Mac app/bridge 进程；共享 Host 未操作。

## AppSDK / Collab 状态

- 已按要求尝试官方 `appsdk init .`；Collab 返回 `collab peer bootstrap pending: no live tmux pane`，未伪造 identity/claim。
- `appsdk verify .` 返回 `INVALID_SDK_MIGRATION_RECORD`。
- 对干净 base `5d1d288` 的只读归档副本执行同一 `appsdk verify .`，仍返回 `INVALID_SDK_MIGRATION_RECORD`；该失败属于基线迁移记录，不在 Mac owner scope 内手改、删除或伪造。
- Mac module 已绑定 `.appsdk/maps/module-registry.json` 与 `.appsdk/project.json` 的 `macos-shell` / `macos-native-h264` 条目；治理资源修改仍需主线程结合基线迁移问题审计。

## Review / Git

- AGY review：PASS；controller task `m1-luna-mac-bffdb90`，commit `bffdb90`，base `5d1d288`，`findings=[]`，`outcomeReason=controller_no_blocking_findings`。
- candidate implementation commit：`bffdb90`。
- 未 merge、未 push、未改 main、未清理旧 worktree。

## 主线程下一步

1. 审计并决定如何在不掩盖 base `INVALID_SDK_MIGRATION_RECORD` 的前提下接纳本切片的 AppSDK 接线。
2. 主线程负责 merge、main verification、共享 Host/设备资源调度及后续双端验收。

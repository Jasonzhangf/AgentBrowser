# AgentBrowser 用户切片连接指南

本文说明当前的普通连接步骤。范围只覆盖已交付的用户切片。完整 M1 仍未交付。

## 已交付的产物

| 产物 | 位置 | 身份 |
| --- | --- | --- |
| Android APK | `/Volumes/Intel/playground/agentbrowser/m1-android-navigation-fix-r1-20261009/apps/android/app/build/outputs/apk/debug/app-debug.apk`（本集成 worktree 不含 APK） | 包名 `com.agentbrowser.probe`，versionCode `2`，versionName `0.2.0-probe`，SHA-256 `8eceb55e404791484451499aac38cce70a2d6fcdd2aa2d1ed72f872a7d63cb45` |
| Mac 应用 | `apps/macos/build/AgentBrowserMac.app` | 可执行文件 SHA-256 `053adf8ad875a384605181dac38217dc30c2bb4e371af6d7448cae57209cd00c` |
| Mac ZIP | `generated/modules/macos-shell/lib/AgentBrowserMac.app.zip` | 由 `scripts/build-macos.sh` 生成 |
| Host 服务 | `scripts/host-service/host-service` | 协议版本 `11`，由本项目生命周期入口启动 |

## 第一步：安装 Android 应用

```sh
adb install -r apps/android/app/build/outputs/apk/debug/app-debug.apk
```

升级安装保持签名不变，因此可以覆盖旧版本。

## 第二步：启动 Host 并导出配对包

只用项目生命周期入口，不要手工启动 Host。

```sh
ROOT="$HOME/Library/Application Support/AgentBrowser/host-service"
scripts/host-service/host-service start --root "$ROOT"
scripts/host-service/host-service status --root "$ROOT"
scripts/host-service/host-service trust init --root "$ROOT" \
  --endpoint-host <endpoint-host> --endpoint-port 8443
scripts/host-service/host-service trust provision --root "$ROOT" \
  --label "Android phone" \
  --scope observe_human_control --scope profile_read \
  --initial-profile profile_id:<profile-uuid>
scripts/host-service/host-service admin catalog list --root "$ROOT"
scripts/host-service/host-service admin membership set --root "$ROOT" \
  --intent grant --owner-device-id local --profile-id <profile-id> \
  --device-id <device-uuid> --member local --member <device-uuid>
scripts/host-service/host-service trust endpoint-refresh --root "$ROOT" --timeout 30
scripts/host-service/host-service trust export --root "$ROOT" \
  --device-id <device-uuid> --output <private-dir>/pairing.abconnection
```

要点：

- `trust export` 的输出目录必须是私有目录（`0700`）。写到 `/tmp` 这类共享目录会被拒绝，错误为 `directory must already be private (expected 0700)`。
- `trust export` 需要当前目录成员关系和已刷新的端点回执。缺少时会报 `PROFILE_ACCESS_WRITE_FAILED` 或 `ENDPOINT_REFRESH_REQUIRED`。
- 一次性传输码只在进程内返回，不写入文件、argv、日志或配对包。

## 第三步：在 Mac 上生成并读取一次性传输码

1. 打开 `AgentBrowserMac.app`。
2. 选择菜单 `连接` → `配置手机连接…`。
3. 在配置面板中填写设备名称与权限，选择 `登记并导出…`。
4. 在保存面板中选择配对包位置并确认。
5. 应用会弹出一张原生对话框，标题为 `一次性传输码`，并提示 `请在手机上输入此传输码。关闭此窗口后不会再显示。`
6. 需要复制时选择 `复制传输码`。

传输码只在这次导出中有效。关闭对话框后不会再次显示。

## 第四步：在 Android 上导入配对包

1. 打开应用。
2. 打开抽屉，选择 `配置连接`。
3. 选择 `导入`。
4. 系统打开 Android DocumentsUI。选择刚导出的 `.abconnection` 文件。
5. 应用显示受保护配对包对话框，包含 `Transfer code`、`CANCEL`、`IMPORT`。
6. 在 `Transfer code` 中输入 Mac 上显示的一次性传输码。
7. 选择 `IMPORT`。

成功后应用进入已配置状态。失败时应用显示类型化错误，例如 `BUNDLE_TRANSFER_CODE_INVALID`。

## 第五步：连接并浏览

1. 在 `配置连接` 面板中确认已配置。
2. 在页面地址输入 Host 端点地址，然后提交。
3. 应用进入浏览模式。远端画面显示在原生视频区，WebView 负责页面交互。

## 已知限制

这些限制已在证据中记录，不能当作通过。

- 中文输入法：当前验收模拟器没有可用的中文输入法子类型或中文输入法包。真实中文组合输入未验证。
- 系统输入法焦点：`dumpsys input_method` 显示 `mInputShown=false`、`mServedInputConnection=null`。系统输入法抬升未验证。
- 跨节点 Tailscale：指定的 Android 节点 `15T`（`100.104.163.65`）离线。跨节点 Tailscale 未验证。
- 传输码交付路径：Mac 侧会显示一次性传输码。复制入口已实现，但在捕获中未观察到可操作状态（`copy_action_available=implemented_not_visible`）。模拟器不共享主机剪贴板，因此未验证复制到 Android 的粘贴路径。本次验收中一次性传输码由操作者在同一 shell 内直接传给仪器测试，代替人工粘贴。
- Host 范围：配对与准入在服务生命周期 Host 上验证；本地浏览在 fixture 启动的同一 r8 Host 上验证。
- 独立 DOM 回读：服务生命周期 Host 不提供独立 DOM 回读通道。Tailscale 浏览中的点击由 Host 应用的画面重绘证明，不由独立 DOM 读取证明。
- 稳定性：`testRealNetworkControlAndFrames` 在本环境中出现过两次与断言无关的失败后通过。浏览门禁当前不稳定。

## 已通过的浏览范围

在 fixture 启动的 r8 Host 上，远端视频、真实点击、拖动滚动和观察/接管均已通过。

- 远端视频：`frameAck.renderedFrames=3`，`displayed=true`。
- 真实点击：独立 Host DOM 记录 `clicked=1`。
- 拖动滚动：Host 记录 `scrollTrace=[0,250,0]`，`maxScroll=250`，`finalScrollY=0`，`restoredToZero=true`。拖动把文档从 `0` 移到 `250`，反向拖动回到 `0`。两次独立运行复现。
- 观察/接管：通过。

## 已通过的 Tailscale 范围

已通过的 Tailscale 范围是“同一台物理 Mac 经 Tailscale 接口”。

- Mac Tailscale 地址：`100.66.1.82`（运行时从 `tailscale status` 读取）。
- 端点绑定：`100.66.1.82:51881`。
- 普通 Android UI 准入：通过。
- 普通 Android UI 浏览：通过。`renderedFrames=2`，`navigatedFixturePage=true`，`redBeforeClick=true`，`limeAfterClick=true`，`nativePixelsChanged=true`。
- 跨节点 Tailscale 仍未验证，因为 `15T` 离线（最后在线约 8 小时前）。

证据在 `evidence/user-slice/tailscale/`，回执在 `docs/runs/m1-user-slice-tailscale/receipt.md`。

## 失败时看哪里

- Host 生命周期：`scripts/host-service/host-service status --root "$ROOT"`。
- 端点刷新：`scripts/host-service/host-service trust status --root "$ROOT"`。
- Android 导入错误：应用面板显示的类型化错误文本。
- Android 日志：`adb logcat -d | grep -iE 'agentbrowser|BUNDLE_|CONFIG_'`。

## 完整 M1 状态

完整 M1 未交付。仍未完成的验收包括：BB01–BB14 全量、真实 HTTPS 登录与配置文件重启、弱网与画质行为、本地/LAN/Tailscale/Relay 矩阵、核心回归、最新 main 端到端、独立评审、双仓库远端交付、版本包与 OTA、基线与项目记忆、资源清理。

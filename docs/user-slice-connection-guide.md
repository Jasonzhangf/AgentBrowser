# AgentBrowser 用户切片连接指南

本文说明当前的普通连接步骤。当前有两条客户端主线：Mac 本地连接/接管路径和
Android 导入/浏览路径。范围只覆盖已交付的用户切片，完整 M1 仍未交付。

## Mac 主线当前状态

2026-10-21 编排者在独占 worktree 上用隔离 fixture Host 和构建后的
`AgentBrowserMac.app` 实测了真实入口：`打开菜单` → `连接` 进入
`connectionState=connected` 和 `renderedFrames>0`；`接管` 后
`controlMode=control`、`inputReady=true`、`visibleContent=native-video`；
然后 `返回观察`。连接入口本身没有缺陷。新增自动入口把 `返回观察` 后 fixture
`inspect` 与连接前基线一致作为 fail-loud gate；该 gate 已在本机运行并通过
（`PASS_AUTOMATED`），换环境需重新运行。

这还不是完整 P5/P6 或 M1 通过。原生点击、键盘、滚动和中文 IME 仍需人工
验证；自动脚本把这些步骤写入回执的 `manual` 段，状态为 `PENDING_HUMAN`。

自动入口需要图形会话和 Accessibility 权限：

```sh
export OBSCURA_SRC=/absolute/path/to/obscura
export OBSCURA_PROTOCOL_ROOT="$OBSCURA_SRC/protocol/browser"
export OBSCURA_BIN_DIR="$OBSCURA_SRC/target/release"
node scripts/mac-user-acceptance.mjs
```

回执在 `evidence/mac-user-acceptance/<run>/receipt.json`。自动化通过时
`result` 是 `PASS_AUTOMATED`，不是完整用户路径 PASS。完整人工步骤见
[docs/macos-client.md](macos-client.md)。

## Mac 一次性配置与直接启动

2026-10-22 在独占 worktree 上用隔离 fixture Host 实测了持久配对路径，结果
`PASS_AUTOMATED`。回执在
`evidence/mac-persisted-pairing/<run>/receipt.json`。两个关键 gate 是
`PERSISTED_IMPORT` 与 `PERSISTED_AUTO_CONNECT`：先在临时 HOME 的默认存储导入
配对文件，再以不带任何参数的方式启动应用，应用自己连上 Host 并显示画面。

配置只做一次，之后每次双击应用都能直接连接：

1. 打开 `AgentBrowserMac.app`。
2. 选择菜单 `连接` → `导入本机配对…`。
3. 在系统选择面板中选择导出的 `.abconnection` 文件。
4. 关闭应用，再次打开。不需要参数，也不需要重新配对。

导入写入默认存储 `~/Library/Application Support/AgentBrowser/mac-pairing/`：
目录权限 `0700`，`endpoint.txt` 与 `token` 权限 `0600`。先写临时文件再改名，
中途失败不会留下半个存储。默认存储同时有这两个文件才算已配置。

存储解析顺序是：先看 `AGENTBROWSER_MAC_PAIRING`（应用带的 `--pairing-dir`
只为测试设它），再看默认存储。自动连接每次启动最多一次，只在默认存储路径下
发生，且要求 `connectionSetup.state=configured` 与空闲状态。带 `--pairing-dir`
的启动保持手动连接，因为它是测试和开发入口。第一次自动连接之后，断开、重连和
重试都由人决定，真实连接错误照常显示。

导入失败会给出类型化原因并保留原来的存储：`PAIRING_BUNDLE_INVALID`、
`PAIRING_BUNDLE_READ_FAILED`、`PAIRING_STORE_UNAVAILABLE`。原因里不含令牌。

自动化复现命令：

```sh
node scripts/mac-user-acceptance.mjs --persisted-pairing \
  --receipt-dir evidence/mac-persisted-pairing
```

该模式在 `target/persisted-home-<run>` 建临时 HOME，导入后以无参数方式启动
应用，跑完接管、返回观察和 `inspect` 比对，最后删除临时 HOME 并记录在
`cleanup.scratch_home`。本机真实存储不会被碰到。

说明一处待确认的产品口径：`docs/design.md` 写的是手机优先。本轮把 Mac 一次性
配置与直接启动当作本轮交付优先项，没有改写总产品顺序。是否需要把文档改成 Mac
优先，需要用户确认后再动。

## 已交付的产物

| 产物 | 位置 | 身份 |
| --- | --- | --- |
| Android APK | 历史构建结果；旧 worktree 路径已失效，本 worktree 不含 APK | 包名 `com.agentbrowser.probe`，versionCode `2`，versionName `0.2.0-probe`；按 `docs/android-probe.md` 重新构建 |
| Mac 应用 | `apps/macos/build/AgentBrowserMac.app` | 由 `scripts/build-macos.sh build` 生成；不固定 SHA |
| Mac ZIP | `generated/modules/macos-shell/lib/AgentBrowserMac.app.zip` | 由 `scripts/build-macos.sh` 生成 |
| Host 服务 | `scripts/host-service/host-service` | 协议版本 `11`，由本项目生命周期入口启动 |

## 第一步：构建并安装 Android 应用

```sh
bash scripts/android.sh assembleDebug
adb install -r apps/android/app/build/outputs/apk/debug/app-debug.apk
```

升级安装保持签名不变，因此可以覆盖旧版本。旧 worktree 的 APK 路径和 SHA 只是
历史结果，不能当作当前 worktree 的产物。

## 第二步：安装并启动 Host 服务

只用项目生命周期入口，不要手工启动 Host。`install` 幂等创建访问令牌，`start` 读取它。`trust init` **不是** `start` 的前置条件。

```sh
ROOT="$HOME/Library/Application Support/AgentBrowser/host-service"
scripts/host-service/host-service install --root "$ROOT" \
  --binary /absolute/path/to/obscura-host \
  --protocol-version 11
scripts/host-service/host-service start --root "$ROOT"
scripts/host-service/host-service status --root "$ROOT"
```

## 第三步：在 Mac 上导出连接文件

1. 打开 `AgentBrowserMac.app`。
2. 选择菜单 `连接` → `配置手机连接…`。
3. 在面板中填写 `端点主机` 与 `端点端口`。
4. 选择 `生成连接文件…`，在保存面板中选择位置并确认。

面板按 §7.7 的顺序调用服务：`trust init` 记录端点地址，`trust endpoint-refresh` 绑定端点，`trust export` 写出连接文件。也可以用 CLI 完成同样的三步：

```sh
scripts/host-service/host-service trust init --root "$ROOT" \
  --endpoint-host <endpoint-host> --endpoint-port 8443
scripts/host-service/host-service trust endpoint-refresh --root "$ROOT" --timeout 30
scripts/host-service/host-service trust export --root "$ROOT" \
  --output <private-dir>/pairing.abconnection
```

要点：

- 连接文件只有两个字段：`endpoint` 与 `token`。令牌是唯一的凭据，Host 是唯一的比对者。
- `trust init` 是幂等的面板步骤：它记录地址，保留已有令牌与监听身份，不轮换任何东西。
- `trust export` 的输出目录必须是私有目录（`0700`）。
- `trust export` 需要已刷新的端点回执。缺少时按情况报 `ENDPOINT_NOT_REFRESHED` 或 `ENDPOINT_REFRESH_REQUIRED`。
- 服务生成并持有令牌。Mac 不读取、不显示、不记录、不存储它。

## 第四步：在 Android 上导入连接文件

1. 打开应用。
2. 打开配置连接面板。
3. 选择导入。
4. 系统打开 Android DocumentsUI。选择刚导出的 `.abconnection` 文件。
5. 导入成功后应用进入已配置状态。

导入只做四件事：读文件、解 JSON、取 `endpoint` 与 `token`、写两个文件。没有传输码输入步骤，也没有权限或配置文件选择。导入端不做任何本地校验：`format`、`version`、端点形状、端口范围、令牌是否空白都不检查。导入一个错误的文件是允许的，它会在连接时失败。失败时应用显示一个类型化错误：`BUNDLE_INVALID`（文件读不出、JSON 解不开或两个字段缺任一）或 `CONFIG_PICKER_UNAVAILABLE`（无法打开选择器）。

## 第五步：连接并浏览

1. 在配置面板中确认状态为已配置。
2. 在页面地址输入 Host 端点地址，然后提交。
3. 应用进入浏览模式。远端画面显示在原生视频区，WebView 负责页面交互。

## 已知限制

这些限制已在证据中记录，不能当作通过。

- 中文输入法：当前验收模拟器没有可用的中文输入法子类型或中文输入法包。真实中文组合输入未验证。
- 系统输入法焦点：`dumpsys input_method` 显示 `mInputShown=false`、`mServedInputConnection=null`。系统输入法抬升未验证。
- 跨节点 Tailscale：指定的 Android 节点 `15T`（`100.104.163.65`）离线。跨节点 Tailscale 未验证。
- 一次性传输码：该交付路径已随配对仪式一起删除。连接文件里只有端点与令牌，Mac 与 Android 之间不再传递传输码。
- Host 范围：配对与准入在服务生命周期 Host 上验证；本地浏览在 fixture 启动的同一 r8 Host 上验证。
- 独立 DOM 回读：服务生命周期 Host 不提供独立 DOM 回读通道。Tailscale 浏览中的点击由 Host 应用的画面重绘证明，不由独立 DOM 读取证明。
- 稳定性：`testRealNetworkControlAndFrames` 在本环境中出现过两次与断言无关的失败后通过。浏览门禁当前不稳定。
- Mac 原生输入：Mac 客户端已通过连接、真实画面、接管和返回观察；原生点击、键盘、滚动和中文 IME 仍需人工验证，自动回执只标记 `PENDING_HUMAN`。

## 已通过的 Android 浏览范围（历史结果）

在 fixture 启动的 r8 Host 上，远端视频、真实点击、拖动滚动和观察/接管均已通过。
以下点击和滚动证据来自 Android 客户端，不是 Mac 原生输入。

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

完整 M1 未交付。仍未完成的验收包括：BB01–BB14 全量、真实 HTTPS 登录与配置文件重启、弱网与画质行为、本地/LAN/Tailscale/Relay 矩阵、核心回归、最新 main 端到端、独立评审、双仓库远端交付、版本包与 OTA、基线与项目记忆、资源清理。Mac 原生点击、键盘、滚动和中文 IME 也仍需人工验证。

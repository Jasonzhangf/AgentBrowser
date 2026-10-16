# Mac + Android 联测验收手册

本手册给出可重复执行的手机 ↔ Mac 直连验收步骤。
验证对象：Mac 端 Obscura `host` + `endpoint`，AgentBrowser macOS 原生应用，AgentBrowser Android 应用。

## 0. 固定路径

```text
AgentBrowser worktree : /Volumes/Intel/playground/agentbrowser/m1-mac-android-live-test-20261013
Obscura 二进制目录     : /Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009/target/release
Obscura 协议目录       : /Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009/protocol/browser
Mac 局域网地址         : 192.168.0.3
```

```sh
WT=/Volumes/Intel/playground/agentbrowser/m1-mac-android-live-test-20261013
R8=/Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009
```

## 1. 构建

### 1.1 Android APK

```sh
cd "$WT"
OBSCURA_PROTOCOL_ROOT="$R8/protocol/browser" bash scripts/build-android.sh
```

产物：`apps/android/app/build/outputs/apk/debug/app-debug.apk`

### 1.2 macOS 原生应用

```sh
cd "$WT"
OBSCURA_PROTOCOL_ROOT="$R8/protocol/browser" bash scripts/build-macos.sh build
```

产物：`apps/macos/build/AgentBrowserMac.app`

### 1.3 联测 fixture（真实 Host + endpoint）

```sh
cd "$WT"
OBSCURA_PROTOCOL_ROOT="$R8/protocol/browser" \
  cargo build --release -p agentbrowser-android \
  --example device_fixture \
  --config "patch.crates-io.obscura-host-protocol.path=\"$R8/protocol/browser\""
```

产物：`target/release/examples/device_fixture`

## 2. 启动 Mac 端 Host + endpoint

`device_fixture` 从 stdin 读取 `status` / `inspect` / `quit`。
用一个 FIFO 保持 stdin 打开：

```sh
FIFO=/tmp/an-fixture.fifo
mkfifo -m 600 "$FIFO"
( sleep 86400 > "$FIFO" ) &
cd "$WT"
OBSCURA_BIN_DIR="$R8/target/release" \
OBSCURA_ENDPOINT_BIND_IP=192.168.0.3 \
  target/release/examples/device_fixture < "$FIFO" > /tmp/an-fixture.out 2> /tmp/an-fixture.err
```

第一行 stdout 是 JSON，含 `fixture`、`bundle`、`endpoint`、`session`：

```sh
head -1 /tmp/an-fixture.out
```

后续命令：

```sh
printf 'status\n'  > /tmp/an-fixture.fifo   # Host 会话状态
printf 'inspect\n' > /tmp/an-fixture.fifo   # 读取远端 DOM 探针
printf 'quit\n'    > /tmp/an-fixture.fifo   # 停止 fixture
```

`inspect` 只在 agent 持有控制时可用。手机或 Mac 接管期间返回 `CONTROL_REQUIRED`。

## 3. Android 手机关联与验收

1. 安装 APK：

   ```sh
   adb -s <phone> install -r "$WT/apps/android/app/build/outputs/apk/debug/app-debug.apk"
   ```

2. 把 pairing bundle 推到手机下载目录：

   ```sh
   adb -s <phone> push "$(head -1 /tmp/an-fixture.out | python3 -c 'import json,sys;print(json.load(sys.stdin)["bundle"])')" /sdcard/Download/pairing.abconnection
   ```

3. 打开 AgentBrowser，点“打开菜单”。
4. 点“导入”，在系统文件选择器里选“下载 → pairing.abconnection”。
5. 菜单里“连接：”应显示 Mac 的 `endpoint`。
6. 点“连接”。

期望：

- 菜单“状态”为 `connected`。
- “控制”为 `observe`。
- `Transport` 为 `wss`。
- `Codec` 为硬件解码器（本机为 `c2.qti.avc.decoder`）。
- 画面出现远端页面：红色 `touch` 按钮、`type here` 输入框、`AgentBrowser live Host`、蓝色滚动区。

Host 侧核对：

```sh
printf 'status\n' > /tmp/an-fixture.fifo
```

`attachments` 至少为 1，且 `viewport_owner` 指向手机 attachment。

### 3.1 控制闭环

1. 菜单点“接管”。
2. `status` 应显示 `control.phase.type == "human"`，`attachment_id` 等于手机 attachment。
3. 点远端红色按钮。
4. 菜单点“返回观察”。
5. `inspect` 应返回 `{"clicked":1,...}`。

已实测结果：手机接管后点击得到 `clicked:1`。

## 4. macOS 原生应用验收

先准备 pairing 目录。macOS 客户端读 `endpoint.txt` 和 `token` 两个文件，
不是 `.abconnection` JSON。可从 bundle 生成：

```sh
FIX=$(head -1 /tmp/an-fixture.out | python3 -c 'import json,sys;print(json.load(sys.stdin)["fixture"])')
MACPAIR=/tmp/an-mac-pairing
mkdir -p "$MACPAIR" && chmod 700 "$MACPAIR"
python3 - "$FIX/pairing.abconnection" "$MACPAIR" <<'PY'
import json, pathlib, sys
b = json.load(open(sys.argv[1])); p = pathlib.Path(sys.argv[2])
(p / 'endpoint.txt').write_text(b['endpoint'] + '\n')
(p / 'token').write_text(b['token'] + '\n')
PY
```

启动：

```sh
cd "$WT"
OBSCURA_PROTOCOL_ROOT="$R8/protocol/browser" \
  bash scripts/build-macos.sh run --pairing-dir "$MACPAIR"
```

连接：

1. 点窗口右上角工具栏的菜单按钮（☰）。
2. 点“连接”。

期望：

- “状态”变为 `connected`。
- “控制”为 `observe`。
- 画面出现远端页面。
- 日志出现 `MAC_NATIVE_SEND op=connect` 与持续 `op=ack_frame`。

日志位置：启动命令的 stdout。

已实测结果：

- `connectionState=connected`、`state=playing`、`renderedFrames` 持续增长。
- `visibleContent=native-video`、`geometryMatches=true`。
- 窗口内显示远端页面截图已保存。
- 点“接管”后 Host `control.phase` 为 `{"type":"human","attachment_id":3}`。

## 5. 已知缺口

### 5.1 Mac 客户端输入路径：已验证可用

`docs/mac-client-input-probe.py` 是无 GUI 探针。

它同时启动 fixture 和 `AgentBrowserMacBridge`，并驱动完整输入前置链：

```text
connect -> viewport -> takeover -> frame -> ack_frame -> click / gesture
```

实测回执（2026-10-16）：

```text
connect    connectionState connected
viewport   1180x704 landscape
takeover   control {"epoch":3,"phase":{"type":"human","attachment_id":2}}
ack        第 2 帧后 inputReady=true
click      op=click 通过 → clicked 0 -> 1
gesture    pointer_down + pointer_up 通过 → clicked 1 -> 2
```

结论：Mac 客户端的 `click` 操作与手势（`editing` + Gesture binding）两条路径

都能真正作用于远端页面。协议层与桥接层正确。

### 5.2 输入前置条件

`input_ready()` 需要同时满足：

1. 已展示一帧；
2. `session.viewport` 已设置；
3. `viewport_pending` 为 false；
4. 本客户端持有控制；
5. 展示帧的 session / document / viewport revision 与状态一致。

只 ack 一帧不够。必须在 `viewport` 之后 ack 到第二帧，`inputReady` 才为 true。

不满足时输入返回 `DISPLAY_NOT_READY_OR_CONTROL_NOT_GRANTED`。

### 5.3 GUI 点击已能送达，但 连接 按钮不可用（真实缺陷）

`docs/mac-gui-click-e2e.py` 用辅助功能坐标加真实鼠标事件驱动 GUI。

本轮先解决了两个测试环境问题：

1. `cliclick` 在本机不送达事件。改用 System Events 的 `click at {x,y}`，
   并且必须在同一个 osascript 内先激活应用。
2. 本机已有一个他人启动的旧版 AgentBrowser 实例，窗口位置相同。
   必须先把探针自己的窗口移到不重叠的位置，否则点击落到旧实例上。

修正后，真实点击确实到达 WebView。证据：对 `关闭菜单` 按钮发真实点击，菜单关闭。

但 `连接` 按钮仍然不触发：

```text
click 连接: OK 3450,401 front=true
connectionState: "connectionState":"idle"
send counts: {'MAC_NATIVE_SEND op=status': 237}
```

从未出现 `op=connect`。面板显示：

```text
连接 未连接
需要配置连接
请先配置或导入连接
```

而原生桥接层在同一进程内报告：

```text
connectionSetup: {"error":null,"label":"Local pairing","state":"configured"}
networkConfigured: true
```

统计本轮 205 条 `op=status` 回执，全部为 `configured`。

`canConnect` 要求 `connectionSetup.state === 'configured'`。
UI 判为未配置，所以按钮禁用。

进一步证据：UI 渲染的字符串 `请先配置或导入连接` 在当前源码和已构建的
`app.js` 中都不存在。当前源码与构建产物用的是 `请先选择并导入连接文件`。

即运行中的界面代码与构建产物不一致。这是阻断 Mac 图形界面发起连接的真实缺陷。

未定位：界面代码与构建产物不一致的确切来源。已排除 WebKit 缓存和进程池共享。
把 `app.js` 中的该字符串替换为标记后重启，界面仍显示旧字符串。

## 6. 注意

- 不要把 pairing token 写入文档、日志或聊天。
- `fixture` 目录是临时目录，重启后会被清理。
- 手机换网络后 `adb` 可能显示 `offline`。用 `adb connect <ip>:5555` 重连。
- 合成点击必须用 System Events 的 `click at`，不能用 `cliclick`。
- 本机有他人启动的旧版 AgentBrowser 实例，验收前先把窗口移到不重叠位置。

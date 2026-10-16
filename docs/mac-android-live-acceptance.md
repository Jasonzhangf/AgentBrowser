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

### 5.3 Mac 图形界面：整链已通过

`docs/mac-gui-click-e2e.py` 用辅助功能坐标加真实鼠标事件驱动 GUI。

本轮先修好三个测试环境问题：

1. `cliclick` 在本机不送达事件。
2. `first process whose unix id is N` 在本机返回错误的进程。两个同名实例同时运行时，
   它总是返回先启动的那个。必须改成遍历 `every process` 逐个比对。
   这一点此前让所有 GUI 结论都指向了他人启动的旧实例。
3. 面板按钮坐标要按窗口原点的相对偏移算，偏移量取自实际截图。

还有一个关键点：System Events 的 `click at {x,y}` 只对辅助功能树里存在的元素有效。

原生视频视图是普通 NSView，不在辅助功能树里，所以收不到这种点击。

必须改用 CoreGraphics 的真实 HID 事件（`CGEventCreateMouseEvent` 加 `CGEventPost`）。

修正后，Mac 图形界面整链实测通过：

```text
op=connect   1 次 → connectionState "connected"
op=viewport  1 次
op=ack_frame 335 次
op=takeover  1 次 → control {"epoch":3,"phase":{"type":"human","attachment_id":2}}
返回观察       → control {"epoch":4,"phase":{"type":"agent"}}
```

页面点击实测（接管状态下，菜单已关闭）：

```text
click red-button (90,50):   delta {'op=status': 35, 'op=ack_frame': 27, 'op=editing': 2}
click input (100,119):      delta {'op=status': 36, 'op=ack_frame': 27, 'op=editing': 2}
click blue (400,220):       delta {'op=status': 36, 'op=ack_frame': 28, 'op=editing': 2}
click blue-low (600,520):   delta {'op=status': 35, 'op=ack_frame': 27, 'op=editing': 2}
```

每个点击产生 2 条 `op=editing`，即 `pointer_down` 加 `pointer_up`。

释放控制后远端页面自检：

```text
{"clicked":1,"text":"","scrollY":0,"maxScroll":0,"scrollTrace":[0],"editorEvents":[]}
```

`clicked` 由 0 变 1，只有红色按钮那一次命中。图形界面点击真实作用到远端页面。

截图确认界面显示远端页面：红色 `touch` 按钮、`type here` 输入框、
`AgentBrowser live Host` 文本和蓝色长页面。

结论：Mac 原生应用的关联、页面浏览与控制三项在图形界面上全部可用。

### 5.4 点击注入方式对照

| 方式 | WebView 元素 | 原生视频视图 |
| --- | --- | --- |
| `cliclick` | 不送达 | 不送达 |
| System Events `click at` | 送达 | 不送达 |
| CoreGraphics `CGEventPost` | 送达 | 送达 |

验收和自动化一律使用 CoreGraphics 方式。

### 5.5 Android 侧：安装、导入、连接、显示已通过

本轮在 Android 16 模拟器（`sdk_gphone64_arm64`，arm64-v8a）上重跑手机侧验收。

```text
adb install -r apps/android/app/build/outputs/apk/debug/app-debug.apk   Success
adb shell am start -n com.agentbrowser.probe/.MainActivity              启动成功
```

配对与连接步骤全部通过：

1. 把配对文件推到 `/sdcard/Download/pairing.abconnection`。
2. 点 `☰`，点 `导入`，在 DocumentsUI 里选中该文件。
3. 面板显示 `连接：192.168.0.3:<端口>`，`连接` 按钮变为可用。
4. 点 `连接`，面板显示 `已连接` 与 `停止`。

Mac 侧同时可核对：

```text
lsof -nP -iTCP:<端口>   两条来自 qemu-system 的 ESTABLISHED
fixture status           attachment_id 1，connectionState 已连接
```

模拟器把宿主机局域地址 `192.168.0.3` 直接打通，不需要改成 `10.0.2.2`。
改成 `10.0.2.2` 反而连接被拒。

远端页面在模拟器上正常显示。截图能看到红色 `touch` 按钮，
位于 `[0,210]-[470,471]`。

模拟器用 `adb shell input tap` 点击 `☰` 可以开关面板。
但面板内 `接管` 的点击坐标尚未校准，本轮没有完成模拟器上的接管与页面点击。

这是在模拟器上继续验收的下一步，与真机无关。

## 6. 注意

- 不要把 pairing token 写入文档、日志或聊天。
- `fixture` 目录是临时目录，重启后会被清理。
- 手机换网络后 `adb` 可能显示 `offline`。用 `adb connect <ip>:5555` 重连。
- 点击注入用 CoreGraphics 的 `CGEventPost`，不要用 `cliclick`。
- 查进程必须遍历 `every process` 比对 `unix id`，不能用 `first process whose unix id is N`。
- 本机有他人启动的旧版 AgentBrowser 实例。两者窗口位置相同，验收前先确认目标进程。
- 模拟器验收前先确认 `adb devices` 里选中的是哪个设备，本机同时有真机与模拟器。
- `device_fixture` 的命令通道是 stdin 明文行命令，例如 `status`、`inspect`，
  不是 JSON。

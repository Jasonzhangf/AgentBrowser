# M1 Host Nav MVP DAG 独立复核记录

## 复核对象

- 图文件：`docs/goals/m1-host-nav-mvp-dag.json`
- 图版本：`m1-host-nav-mvp@2`
- 覆盖范围：普通浏览器顶栏、远程 pointer gesture、Host page operation、remote focus snapshot、本地 IME 输入桥、15T 可见证据。

## 节点到源码 Owner 映射

| 语义节点 | Owner / 实现范围 |
| --- | --- |
| 捕获浏览器 UI 或手势 | `apps/android/app/src/main/java/com/agentbrowser/probe/MainActivity.java` |
| 派发浏览器/pointer/key/text 命令 | `MainActivity.java`、`NetworkSession.java`、`packages/android-bridge/src/lib.rs`、`packages/client-connection/src/transport.rs` |
| client transport 投递 | `packages/client-connection/src/transport.rs` |
| endpoint 接收操作 | Obscura `crates/obscura-host/src/endpoint/channels.rs` |
| Host 页面操作 | Obscura `crates/obscura-host/src/worker.rs`、`crates/obscura-browser/src/input.rs` |
| remote focus 快照 | Obscura `crates/obscura-host/src/daemon.rs`、`worker.rs`、`protocol/browser/src/lib.rs` |
| 本地 IME 桥 | `MainActivity.java`、`NetworkSession.keyEvent`、`NativeConnection.keyEvent` |
| 15T 可见证据 | `evidence/m1-host-nav-mvp/gesture-ime-real-15t-*` |

## 复核结论：INCOMPLETE

1. DAG 已覆盖当前目标的主要链路：浏览器 UI/手势捕获 → 类型化 transport → endpoint/host → focus 探测 → 本地 IME → 15T 证据。
2. 已有 15T 证据证明：app 可从 launcher 进入，远程连接状态为 `connected/control/inputReady`，顶栏地址栏、后退/前进/刷新/打开按钮可见，导航命令可使 document revision 从 0 变 1。
3. 仍未完成/未验证项：
   - 合成 swipe 在多次 15T 矩阵中 diff=0，尚未证明滚动/长按/点击链接稳定可用。
   - pinch/two-finger pan 需真实多点输入验证，当前 ADB 无法模拟真实多点手势。
   - 点击远程 input → `remote_focus.editable=true` → 本地 IME → 文本/backspace/Enter → 远程页面更新，尚未完成端到端证据。
   - Host 侧导航失败时具体 error 已补日志，但当前测试环境未拿到完整新日志闭环。
4. 因此不能标记 PASS；当前图用于约束后续补齐与复核，不代表运行验收完成。

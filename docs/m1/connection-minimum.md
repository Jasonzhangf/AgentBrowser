# 连接路径：需求、DAG、测试与消融清单

状态：图与清单。未实现、未测试、未提交。等确认后再动手。

本文只描述"连接 → 显示 → 交互"这一条主线。它是当前唯一目标。

---

## 1. 要求

### 1.1 用户要求（最高优先级，覆盖此前验收框架）

1. 核心功能只有一个：connect → display → interact。
2. 连接路径上"安全和校验"只要不是必须的，全部删掉。即使为了安全，也是后面的事。一个都不留。
3. 不做任何校验。profile 之类的本地校验全部去掉。
4. 只保留必要的连接配置：endpoint 地址 + 配对证书/密钥 + 传输。
5. 不要反复启动，不要空等。找根因，修根因。
6. 消融掉没有必要出现的功能和测试，只保证最小必要性。

### 1.2 黑盒验收合同（BB01–BB14）

第一道用户门槛：**BB01 → BB02 → BB03 → BB05 → BB06**。任一项失败优先修主链。

与本目标直接相关的三条：

| ID | 断言 | 与用户要求的关系 |
| --- | --- | --- |
| BB01 首次启动 | 新安装 → launcher → UI 选择 Host/配对 → 连接；页面可见、地址栏可用；**用户无需工程文件** | 一致。BB01 本身就要求去掉工程仪式 |
| BB05 输入类型 | 视频点 text/password/number/email/textarea/contenteditable → 自动系统键盘；远端有光标与内容；无本地第二表单框 | 一致。这是"interact" |
| BB13 连接矩阵 | 每路径实际可看、点、输入；显示 transport 和真实路径。**错账号/过期授权不能读写 session** | **冲突**。见 §6 决策 1 |

### 1.3 需求层面的硬事实（来自本轮实测）

- Mac 连不上的真根因：部署的 Host 未启用 `render` feature。Host 回 `Host media unavailable: Frames require a render-enabled Host`。换成 render-enabled Host 后 connect PASS。
- 连接被拒的直接原因：Host 端 remote admission 授权层（`REMOTE_PROFILE_SCOPE_REQUIRED` / `REMOTE_INITIAL_PROFILE_ACCESS_DENIED`）。
- Mac connect 路径**从不消费**传输码。它只需要配对目录的四个文件：`endpoint.txt`、`ca.der`、`client.der`、`key.der`。task-21 中 Mac 在无传输码、无 scope 步骤下到达 `connected`。
- 传输码唯一的用途是解开配对包 p12。它不是连接能力，只是导入仪式的载体。

---

## 2. 当前 DAG（现状）

### 2.1 客户端（Android / Mac）

```
[1] 配对材料                endpoint.txt + ca.der + client.der + key.der      <- 唯一必要的配置
      |
[2] 配对包导入               SAF picker -> p12 --(传输码解密)--> ConnectionBundle.parse
      |                       -> 代际/暂存/AtomicFile/lease/identity 状态机
      |                          (ConnectionSetupSession.java, 786 行)
      |                       -> active-generation.json
      |
[3] 连接                    Connector::connect -> WSS /control
      |
[4] 显示                    media frames -> AnnexBDecoder -> 视频区
      |
[5] 交互                    touch -> RemoteInput; tap -> RemoteImeView -> 系统 IME
```

节点 [2] 里，`[1]` 才是必要的。其余是仪式加状态机。

### 2.2 Host（Obscura）

```
[A] endpoint mTLS accept      配对证书认证设备
      |
[B] remote-admission.sock     RegistryFile::authorize -> scope 校验 -> initial profile grant -> session reserve
      |
[C] 每命令授权                verify_current_authorization + REMOTE_COMMAND_FORBIDDEN
      |
[D] profile catalog 绑定      default profile / initial profile
      |
[E] session + media/render
```

`[A]` 已认证设备。`[B]`、`[C]`、`[D]` 不产生连接能力，只拒绝连接。

### 2.3 Host service（AB python）

```
[i]   trust init / trust endpoint-refresh      endpoint 地址            <- 必要
[ii]  trust provision                           scope + initial-profile  <- 仪式
[iii] admin catalog list / admin membership set profile 成员关系          <- 仪式
[iv]  trust export (+ --include-transfer-code)  配对包 + 传输码           <- 传输码是仪式
[v]   lifecycle start / stop                                             <- 必要
```

### 2.4 当前用户实际要走的步骤（`docs/user-slice-connection-guide.md`）

Host 侧 6 条命令（含 provision / catalog / membership / endpoint-refresh / export），Mac 侧 6 步（含权限选择、登记导出、一次性传输码弹窗），Android 侧 7 步（含 DocumentsUI、Transfer code、IMPORT）。

这就是"那么多选项"。

---

## 3. 最小 DAG（目标）

```
客户端   [1] 配对材料 (4 个文件)  ->  [3] 连接  ->  [4] 显示  ->  [5] 交互
Host     [A] endpoint mTLS        ->  [E] session + media/render
服务     [i] endpoint 地址        ->  [v] lifecycle
```

配置入口只做一件事：把 `endpoint.txt` + `ca.der` + `client.der` + `key.der` 写进配对目录。
不需要：传输码、p12 解密、profile 选择、scope 选择、membership、代际/暂存/lease/identity 状态机。

---

## 4. 消融清单 — 功能

### 4.1 已完成删除（未提交）

**Obscura（ob-media-owner，树已 `cargo check` 通过）**

| 文件 | 删除内容 |
| --- | --- |
| `daemon.rs` | `verify_current_authorization`、`remote_request_gate`（含 `REMOTE_COMMAND_FORBIDDEN`）、scope map（`REMOTE_PROFILE_SCOPE_REQUIRED`）、registry 加载、`AuthFailure` 映射、grant 相等校验、principal/grant 设备不一致、`ObserveHumanControl` 校验、default-profile 绑定拒绝、profile 存在性拒绝、access-migration 拒绝、access 拒绝、`ProfileSelect` access 块、attach/request access 块 |
| `remote_authorization.rs` | `AuthFailure`、`RegistryFile::authorize`、`unix_now` |
| `remote_admission.rs` | 错误枚举 8 → 2；`RemoteScope::wire_name` |
| `endpoint/channels.rs` | `forward_remote_request`、`remote_command_error`、`is_terminal_authorization_denial`、`terminal_response`、对应单元测试 |
| `catalog.rs` | `remote_catalog`、`ProfileAccess::grants`（**越出声明 scope，已报告**） |
| `tests/remote_admission_r1.rs` | 删 rp09/rp12/rp14/rp20/rp24；rp01/rp04/rp_migration/rp11 反转为断言新行为 |

保留：mTLS 传输、配对证书投影（`--authorized-clients`）、session reserve/spawn、media/render、admission 帧形状。

**AB（mac-client-owner）**

| 文件 | 删除内容 |
| --- | --- |
| `scripts/host-service/host_service.py` | `generate_transfer_code`、`validate_transfer_code`、`TRANSFER_CODE_ALPHABET/LENGTH`、`build_device_transfer_p12` → `build_device_p12`、document/receipt 里的 `transfer_code`、`--include-transfer-code` 及其脱敏分支、`admin_membership_set`、`_export_membership_receipt`、`_admin_validate_membership_receipt`、`_admin_require_intent`、`admin membership set` subparser 与 dispatch、membership 校验块、`initial_profile_grant` revision 门、`catalog_membership` receipt 字段 |
| `ConnectionBundle.java` | 只留 `parse(raw)`；`BUNDLE_P12_UNREADABLE`；固定非秘密口令 |
| `ConnectionSetupSession.java` | 传输码字段/提示/对话框/`CodePrompt` hook、`required` 准入态、`admitIntentLocked`、`CONFIG_ORDINARY_IMPORT_BLOCKED`、`abortRequested`/`ImportAbort`/`finishCancel` |

`MainActivity.java` 净变化为零（diff 里的 +4 行是 android-replay-owner 的 tap hook）。

### 4.2 待删除

**Obscura**
- `tests/endpoint.rs`：三处 `REMOTE_COMMAND_FORBIDDEN` 断言（agent attach / resize / flush）+ B1 denial 测试 + 去掉 `--authorization-registry`，使其成为"无授权步骤"回执。
- `tests/blackbox_b1_denial_fence.rs`：整文件只测已删的 denial fence。
- `crates/obscura-media/src/bin/webrtc_receiver.rs:197-199`：断言 `REMOTE_COMMAND_FORBIDDEN`（`webrtc_endpoint.rs` 的 fixture 会用它）。**不在任何 owner 的 scope 内，需要指派。**

**AB**
- `packages/ui-plugins/main.tsx`：收缩 setup 面板选项面。
- `apps/macos/Sources/main.swift`：传输码弹窗（:2063）、provisioning 门（"Disabled until the real declared local authority probe reports available"）、`scopeButtons`（:1829）。
- `apps/macos/connection-bridge/src/main.rs`：配对/准入要求。
- `packages/client-connection/src/transport.rs:681-690`：task-19 的 `TASK19_RAW_CONTROL_FRAME` 调试残留（产品路径上每帧打印）。
- `generated/modules/macos-shell/lib/.../host_service.py`：重新生成（不要手改副本）。
- `ConnectionSetupSession.java` 剩余 786 行状态机：代际/暂存/AtomicFile/lease/identity/reconcile/classify/retainedAttempts 是否继续消融到最小。**见 §6 决策 6。**

---

## 5. 消融清单 — 测试

### 5.1 必须删除（只断言已删仪式）

| 测试 | 原因 |
| --- | --- |
| OB `tests/blackbox_b1_denial_fence.rs` | 整文件只测 denial fence |
| OB `tests/remote_admission_r1.rs` rp09/rp12/rp14/rp20/rp24 | 已删 |
| OB `tests/endpoint.rs` 三处 `REMOTE_COMMAND_FORBIDDEN` | 已删 |
| OB `remote_authorization.rs` 内 2 个 `#[test]` | 随 `RegistryFile::authorize` 删除 |
| AB `test_host_trust_public.py` case 12 | 传输码/成员关系；且 `openssl -passin pass:` 与新的加密容器不一致 |
| AB `ConnectionBundleTest.java` | 调用已删的 2-arg `parse`，断言已删的 `BUNDLE_TRANSFER_CODE_*` |
| AB `ConnectionSetupDeviceTest.java` | 调用已删的 `setCodePromptHook`、`IntentType`、`repair_import_allowed` |
| AB `scripts/connection-admission.mjs`、`validate-admission.mjs`、`device-pairing.py` | 若只测准入/传输码仪式 |

### 5.2 必须保留（证明核心功能）

| 测试 | 证明什么 |
| --- | --- |
| OB `tests/endpoint.rs` | mTLS 端点 + 媒体（去掉授权断言后） |
| OB `tests/webrtc_endpoint.rs` | 媒体接收/重绑/终态 |
| OB `tests/remote_editing.rs`、`editor_context_public_c2.rs`、`editor_events_public_r2.rs` | 远端编辑、composition、stale 拒绝 |
| OB `tests/lease_cancel_r2.rs` | 手势/组合租约取消 |
| OB `tests/persistent.rs`、`navigation_profile.rs`、`profile_replay_r2.rs` | 页面状态、viewport 选举、profile 持久化 |
| OB `blackbox_b4/b5/b6/b7/b8/b9/b10` | encoder/session 生命周期（见 §6 决策 2） |
| AB `NetworkDeviceTest.java` | 真实用户路径（连接/浏览/触控/IME） |
| AB `AnnexBDeviceTest`、`LayoutDeviceTest`、`WebRtcDeviceTest`、`AccountDeviceTest`、`ProbeDeviceTest` | 解码、布局、WebRTC、账号、探针 |
| AB `packages/*/*.test.ts`（7 个） | UI/domain 单元 |
| AB `scripts/real-path-replay.py`、`mac-connect-gate.mjs` | 真实入口 gate |

### 5.3 测试面的连带事实（必须一起处理，否则树是红的）

- `valid.abconnection` 两份 fixture 仍用旧码 `ABCDEFGHJKMN` 加密 → 新的 `parse` 会报 `BUNDLE_P12_UNREADABLE`。
- `test_host_trust_public.py` 只在旧的 `NoEncryption` 状态通过过。
- JDK 17 无法暴露 `cryptography` 生成的 p12 里的 CA/endpoint cert bag，只暴露私钥别名。openssl/keytool 生成的 fixture 可以。→ 见 §6 决策 4。

---

### 5.4 Android 导入流程事实（android-replay-owner 实测）

- 配对包 JSON 形状：`{format, version, transport, endpoint, device_id, device_label, created_at_unix, p12_base64, p12_sha256}`。PKCS#12 由 `build_device_p12(root, device_id)` 生成。
- 普通导入路径：chrome `drawer-toggle` → `connection-import` → DocumentsUI（`Download` / `pairing.abconnection`）→ 应用 → 轮询 `connectionSetup.projection().state == "configured"`，并断言 `pairing/active-generation.json` 变化。传输码对话框删除后，harness 不应再找 `EditText`/`IMPORT`。
- 两个 chrome 点击都受 Android 16 InputDispatcher race（`NO_INPUT_CHANNEL`）影响。可行补救是 `touchChromeVerified(id, delivered)`：重试一次并轮询可观察效果（抽屉 `data-drawerOpen`、DocumentsUI 是否出现）。这条修复本身已 8/8 通过。
- **setup 契约尚未稳定**：过去一小时内 `IntentType`、`admitIntentLocked`、`abortRequested`、`repair_import_allowed`、`repair_connection_import` 被删又被恢复。`ConnectionSetupDeviceTest.java` 仍是红的（43/91/126/149 行）。任何 harness 工作都必须等它稳定。

### 5.5 当前工作树状态（未提交，三方交叉）

| 文件 | 归属 | 状态 |
| --- | --- | --- |
| `RemoteImeView.java` | android-replay-owner | 完整（3 处编辑） |
| `MainActivity.java` | 交叉 | 磁盘上当前只有 android-replay-owner 的 4 行 tap hook |
| `NetworkDeviceTest.java` | android-replay-owner | **不编译**：`:334` 调用 `startImportForTest(Uri)`，而 `ConnectionSetupSession` 恢复成 `startImportForTest(Uri, IntentType)` |
| `scripts/real-path-replay.py` | android-replay-owner | 完整 |
| `ConnectionBundle.java` | mac-client-owner | 编辑完成，但磁盘 fixture 是旧的 |
| `ConnectionSetupSession.java` | mac-client-owner | 完成 |
| `host_service.py` | mac-client-owner | 完成，但最后一次编辑（加固定口令）未验证 |
| `test_host_trust_public.py` | mac-client-owner | 与新的加密容器不一致（case 12 用空口令） |
| Obscura 6 个文件 | ob-media-owner | 完成，`cargo check` 通过 |

**两处报告互相矛盾，必须消解**：
- 口令：android-replay-owner 记录的是"不加密、空口令打开"；mac-client-owner 最后改成 `BestAvailableEncryption(P12_PASSPHRASE)`，固定口令 `agentbrowser-pairing`。两者不能同时成立。见 §6 决策 4。
- `startImportForTest` 签名：一方删参数、一方恢复参数。

**仍在运行的资源（未清理）**：模拟器 `emulator-5554`；Host `state=running pid=58977 endpoint_pid=59026 endpoint_generation=33 protocol_version=11 authorization_state=refreshed profile_root=/private/tmp/abn-lan-profile`。

## 6. 决策（已按你的指令判定）

判定原则：只有"连接必需"才留。配对证书是必需的，因为它是 mTLS 传输本身。除此之外全部删除。

| # | 事项 | 判定 | 依据 |
| --- | --- | --- | --- |
| 1 | BB13 的授权断言 | BB13 保留；断言改为"未配对设备无法建立 session"；删除 profile/scope 断言 | 配对证书已经认证设备。没有有效证书就建立不了 session。所以这条断言由 mTLS 满足，不需要额外授权层 |
| 2 | `blackbox_b4/b6` | 保留测试；改名字/注释去掉授权措辞 | 它们实际测 encoder 生命周期与并发 finalize，不是授权 |
| 3 | `webrtc_receiver.rs:197-199` | 指派 ob-media-owner，扩写 scope | 它是 `webrtc_endpoint.rs` 的 fixture，不动则那个 suite 必红 |
| 4 | 凭证形状 | **配对包只携带 endpoint + token**。客户端证书、CA、p12、口令全部删除 | 用户指令：客户端证书和 CA 一个都不需要，用一个 API Key / Token 即可 |
| 5 | `REMOTE_SESSION_UNAVAILABLE` | 接受 | 其他授权码已删，只剩 session spawn 失败这一种 |
| 6 | `ConnectionSetupSession` 深度 | 消融到"解析 → 写 4 个文件" | 代际/暂存/lease/identity 服务的是原子提交+回滚+租约，属于过度设计 |
| 7 | `catalog.json` 的 `access` 记录 | 删除 | 已无读者 |
| 8 | `REPAIR` / `IntentType` | 消融。导入只有一条路 | 最小必要性 |
| 9 | setup 契约 | 冻结，见 §7 | 不冻结则 harness 每轮追移动目标 |
| 10 | admission 帧 | 只留 Host 真正读的字段；删 `scopes`、`initial` | 未读字段是死重 |

**只有第 4 条需要你确认**，因为它改变配对包的对外格式。其余都是你指令的直接推论。

---

## 7. 冻结的最小契约

### 7.1 配对包 `.abconnection`（新格式）

```json
{
  "format": "agentbrowser.connection",
  "version": 1,
  "endpoint": "<host>:<port>",
  "token": "<不透明字符串>"
}
```

没有 p12、没有口令、没有客户端证书、没有 CA、没有传输码、没有 scope、没有 profile、没有 device_id、没有 created_at。

导入后客户端只写两个文件：`endpoint.txt`、`token`。

### 7.2 传输与凭证

- 客户端连到 `endpoint` 的 `/control`，在 WebSocket 升级请求上带 `Authorization: Bearer <token>`。
- Host 只做一件事：比较 token。相等 → 建 session；不等 → `REMOTE_TOKEN_INVALID`。
- 删除客户端证书校验（mTLS client auth）、CA 校验、`--authorized-clients` 配对证书投影、`remote_authorization.rs` 整个注册表。
- **这是唯一保留的检查。** 它同时满足 BB13"错凭证不能读写 session"。

### 7.3 Host 服务（AB python）

保留命令：
- `lifecycle start / stop / status`
- `trust init --endpoint-host <host> --endpoint-port <port>`（生成 token）
- `trust endpoint-refresh`
- `trust export --output <私有 0700 目录>/pairing.abconnection`（写 endpoint + token）

删除：证书与 CA 生成、p12、传输码、`trust provision` 的 scope 与 initial-profile、`admin catalog list`、`admin membership set`、`--include-transfer-code`。

### 7.4 Host（Obscura）

连接路径只剩：TLS 监听 → token 比较 → session reserve/spawn → media/render。

`obscura-host` argv：`--access-token <token>`。**Host 是唯一的 token 比对者**，因为 session 是受保护资源，其 owner 负责把门。

`obscura-endpoint` argv（**冻结**）：

```
--listen <host:port>
--host-dir <path>
--socket-dir <path>
--server-cert <trust>/server.der        # 服务器自身 TLS 身份，服务自动生成，不进用户配置
--server-key  <trust>/server-key.der
--media-bin <path>
--ffmpeg <path>
--applied-receipt-path <path>
--endpoint-generation <N>
```

删除：`--client-ca`、`--authorized-clients`、`--token`。

endpoint 把 `Authorization: Bearer` 原样转发给 Host，并把 Host 的拒绝映射成客户端的错误帧 `REMOTE_TOKEN_INVALID`。同一个 secret 只比对一次。

applied receipt（**冻结**）：只保留 `schema_version`、`endpoint_generation`、`endpoint_pid`、`bound_address`。删除 `authorization_revision`、`registry_sha256`、`projection_sha256`。

`trust endpoint-refresh` 保留，但它现在只重新绑定并记录新地址，不再需要授权回执。

admission 帧只留 `request_id`、`token`、viewport。删 `principal`、`scopes`、`initial`、registry 加载。

客户端不校验服务器证书（用户不持有任何 CA 文件）。

### 7.5 客户端（Android / Mac）

配置入口只做：选文件 → 解析 §7.1 → 写 `endpoint.txt` + `token` → 连接。

`ConnectionSetupSession` 只剩解析、写两个文件、投影状态。`startImportForTest(Uri)` 单参数。

Mac 配对目录：`endpoint.txt` + `token`（不再有 `ca.der` / `client.der` / `key.der`）。

### 7.6 客户端投影（harness 依赖，冻结）

`connectionSetup.projection()` = `{ state, error, label }`，`state ∈ { unconfigured, configured }`。

删除 `repair_import_allowed`、`active_revision`、`repair_connection_import`。`active-generation.json` 不再存在；harness 改为断言 `state == "configured"` 且两个文件存在。

### 7.7 生命周期顺序（冻结）

```
install  →  start  →  配置面板（trust init 记录地址 → trust endpoint-refresh 绑定 → trust export 导出）
         →  手机导入  →  连接
```

`trust init` **不是** `start` 的前置条件。access token 由 `install` 幂等生成（存在则不轮换），位置 `<root>/trust/token`。`start` 读它；只有真的不存在才是硬错误。

这样用户只需 install + start，然后打开配置面板导出连接文件。

---

## 8. 下一步（执行中）

1. 按 §4/§5 一次性完成消融，不留半成品。
2. 跑**一次**端到端：endpoint + token 直接连上 → 远端页面出渲染帧 → 触摸与输入正确映射。全程无授权步骤。

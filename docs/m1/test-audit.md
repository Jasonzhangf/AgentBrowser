# M1 测试审计：充分性与抓错能力

审计对象：本次需求相关的测试。需求是 connect → display → interact，全程无授权步骤。
审计方法：每项测试必须回答两个问题。

- **Q1 是否足够**：用例能否证明需求、关键不变量和受影响行为正确。证据够就停，不按数量或覆盖率扩面。
- **Q2 能否抓错**：测试绑定哪个具体失败模式，系统错成什么样时哪条断言必须失败。必须有红测或可执行反例。没有就写"未验证"，不能凭设想宣称。

## 1. 需求与不变量

| 编号 | 内容 |
| --- | --- |
| R1 | 已配对设备直接连上 Host，不需要 membership / scope / grant / transfer-code 等任何授权步骤 |
| R2 | 远端页面能显示，有渲染帧 |
| R3 | 触摸与输入正确映射到远端页面 |
| R4 | 连接路径上唯一的检查是 token 比对；错误 token 不得建立 session |
| I1 | 配对包只有 `endpoint` + `token`，没有证书、CA、p12、口令、传输码、scope、profile |
| I2 | Host 是唯一的 token 比对者；endpoint 只原样转发 |
| I3 | 生命周期是 install → start；`trust init` 不是 `start` 的前置条件 |

## 2. 充分性：需求到证据的映射（Q1）

| 需求 | 证据 | 原始行 |
| --- | --- | --- |
| R1 | `evidence/task47-ime-raise/acceptance-1/run.json` 的 `ordinary-admission` 门 | `panel=configured endpoint=192.168.0.3:51883 expected=192.168.0.3:51883 endpointPublished=True tokenPublished=True parser=passed` |
| R1 | `acceptance-1/admission.json` | `{'panelState': 'configured', 'endpoint': '192.168.0.3:51883', 'endpointPublished': True, 'tokenPublished': True}` |
| R1 | 服务侧无授权命令 | `test_removed_ceremony_is_absent` 读真实 `trust --help`，只剩 init/status/export/endpoint-refresh |
| R2 | `remote-video` 门 | `renderedFrames=3->4 ticket=6` |
| R2 | `acceptance-1/real-path.json` | `renderedFramesAfterTyping=38 displayedTicket=6` |
| R3 | `real-touch-focus` 门 | `target=node-46 kind=input frameChanged=True` |
| R3 | `system-ime-focus` 门 | `mInputShown=true mServedInputConnection=RemoteInputConnectionImpl{...RemoteImeView$Connection@bda5240}` |
| R3 | `ascii-lands-in-remote-page` 门 | `ascii='hello' hostFieldText='hello' typingNote=None` |
| R3 | OB `endpoint.rs` token 端到端 | 真实点击返回 `input.state == "succeeded"`，本地 attach 读到 `window.clicked == 1` |
| R4 | `scripts/wrong-token-slice.py`，2/2 | `code":"REMOTE_TOKEN_INVALID"`；`readyFrame=False closed=True` |
| I1 | `test_export_bundle_public` | 精确字段集，0600，无 `p12`/`client_cert`/`scope`/`device_id`/`transfer_code` 字节 |
| I2 | endpoint 不再有 `--token` | `server.rs` 边缘比对已删；唯一比对是 OB `daemon.rs:882` 的 `expected == request.token` |
| I3 | `verify-host-service` | `HOST_SERVICE_REAL_SUBPROCESS_PASS`（打印点 `scripts/host-service/test_host_service.py:1515`），全程零 `trust init` 调用。**本里程碑未保存该输出**：现存日志来自更早里程碑（`docs/runs/m1-service-endpoint-refresh-r1-20261005/r2/raw/real_subprocess_matrix.log`、`docs/runs/m1-host-service-host10-r1-20261003/evidence/verify.stdout`） |

**Q1 结论：R1–R4 与 I1–I3 各有至少一条黑盒证据，且都有原始行。充分。**

不按数量扩面的依据：R1–R3 由**同一次**真实运行同时证明，这是最强的形式。R4 由独立的黑盒切片证明。不变量由服务与协议的公开面证明。

## 3. 抓错能力（Q2）

### 3.1 已用红测或反例证明

| 测试 | 绑定的失败模式 | 必须失败的断言 | 红测证据 |
| --- | --- | --- | --- |
| `scripts/real-path-replay.py` | 远端编辑器保持聚焦时 IME 不再抬起 | `pollImeRaised` → `System IME did not raise within 8000ms` | **演示过红→绿**：修复前 7/8 次运行在此失败（`evidence/task47-ime-raise/root-cause.md`），修复后 8/8 PASS |
| `scripts/host-service/verify-host-service` | `status`/`stop` 对活的记录 PID 崩溃 | 子进程非零退出 | **演示过红→绿**：`state_identity()` 的 `NameError` 让每次 status/stop 死掉；修复后 PASS |
| `testRemoteImeReraiseSlice` | 同一次聚焦会话内 dismiss 后再点同一字段不再抬起 | `ime-reraise.json` 的超时 | **演示过红→绿**：E2 两轮 `fix-e2-2`、`fix-e2-3` 均 PASS |
| `remote_admission_r1.rs` 解析类用例（rp33/34/35/36/41/42/43） | 帧边界、超长、非法 code 被误当成正常帧 | 各自的 typed 拒绝断言 | 转换时这些用例先红后绿（`before-raw-lines.txt` 记录了删除前的原始行） |
| `scripts/wrong-token-slice.py` | 比对接受了本该拒绝的 token | `wrong-token-refused-with-code` 与 `wrong-token-no-session-stream` | **演示过红→绿**：把 Host 的 token 设成切片用的"错误" token 后，切片变红——`raw={"type":"ready","version":11,"session_id":"c2f1b05e-..."}`，客户端拿到了 session。恢复原配置后重新 PASS（`evidence/task47-ime-raise/wrong-token-negative-control/`） |
| `testRemoteImeNoEditorSlice`（负对照） | 没有可写远端编辑器时仍然抬起 IME | `mInputShownAfterTap=false`、`attemptsAfterTap=0`、`eligible=false` | **已跑并 PASS**：`https://example.com/` 上点击后 `eligibleBeforeTap/AfterTap=false`、`identity=null`、`attemptsAfterTap=0`（`evidence/task47-ime-raise/ime-no-editor-run2/`） |

### 3.1.1 本次审计删掉的两个死门

`wrong-token-slice.py` 原有 4 个门。反例实验证明其中两个**对结果不敏感**：

- `wrong-token-no-media-grant` 与 `wrong-token-no-encoder-child` 在反例里保持绿色——即使 session 已经建立。原因是裸 `/control` 连接只开 session 并回 `ready`，`/media` 仍因没有任何绑定而 403，也没有 encoder adapter。这两个门在 token 被拒和被接受时输出相同，**证明不了任何东西**。
- 已删除，不是保留。留下的 2 个门都已在反例中变红。

`wrong-token-no-session-stream` 最初也不敏感：它把 session 帧当成拒绝行消费，所以 session 打开时 `ready_seen` 仍是 false。现已改为检查每一帧，反例中变红。

这是本次审计最实际的产出：一个看起来有 4 项覆盖的测试，实际只有 2 项在起作用。

### 3.2 只是论证，尚未验证

| 测试 | 声称绑定的失败模式 | 为什么未验证 |
| --- | --- | --- |
| `remote_admission_r1.rs` 的错误 token 用例 | 比对接受了本该拒绝的 token | 黑盒切片已用反例证明（§3.1），但 OB 这个用例本身没有注入对照 |
| `blackbox_b6`（重锚后） | admission 被无关 session 的 finalize 串行化 | 第一次转换测到 2.98 s，说明测量本身失败过；但没有注入串行化来证明新断言会红 |
| `test_removed_ceremony_is_absent` | 已删仪式重新出现在 `trust --help` | 陈旧的 "exactly once" 字符串是**人工读出来的**，不是它报红。它读真实表面，但抓错能力未被触发过 |
| `probe.test.ts::connectionSetup admits exactly the frozen configured and unconfigured projections` | 状态集漂移导致合法投影被拒 | `'required'` 那个真实 bug 是人工发现的；测试编码了契约，但未被观察到红 |
| `test_install_creates_token_public` | `install` 不建 token，或第二次 install 轮换 token | 新增，未观察到红 |

## 4. 缺口

1. **端到端验收只有一次运行。** 它同时证明 R1+R2+R3，但只有 1 个样本。
2. **该次运行没有走到 IME 重抬路径。** `system-ime-focus` 首点即成功，所以重抬依赖 E2 那次独立运行，不在同一次验收里。
3. **harness 前置条件会 flake。** `testRemoteImeNoEditorSlice` 第一次运行死在 `ordinary-admission`：DocumentsUI 打开了，但 `Download` 根节点在整个等待期内没有出现（`junit.framework.AssertionFailedError ... NetworkDeviceTest.java:312`）。同样的代码、同样的 `reinstall` 模式刚在 `acceptance-1` 通过，其 logcat 显示 `Download` 在 (627,252) 被点到。这是 harness 前置导航的 flake，不是产品回归。重试一次即过。
   影响：任何依赖 DocumentsUI 的门都可能偶发失败。因此不能把单次失败直接当成产品缺陷，也不能把"重试后通过"当成稳定。
4. **5 项抓错能力未验证**（§3.2）。其中 `blackbox_b6` 与 `remote_admission_r1.rs` 的错误 token 用例最靠近连接路径。
5. **OB 测试已全部转绿**（`remote_admission_r1.rs` 30 passed，`webrtc_endpoint.rs` 19 passed + 3 显式 ignored）。但它们的**抓错能力**未验证（§3.2），所以 OB 侧证据在"能否抓错"这一问上仍不完整。

## 5. 已删除的错误测试

| 测试 | 判定依据 | 处置 |
| --- | --- | --- |
| `NetworkDeviceTest.testOrdinaryTailscaleBrowse` | 三处错误叠加：(1) `:943` 断言 `endpoint.startsWith("wss://100.66.1.82:")`，而 §7.1 冻结的 endpoint 形状是 `<host>:<port>`，断言已不可能成立；(2) 硬编码 `100.66.1.82` 这台设备，换任何机器都不可复现，且该设备已离线；(3) 覆盖与 `testOrdinaryRealHttpsAndRemoteIme` 重复（`configured` → `connect` → `renderedFrames>=2 && inputReady` → `touch` → 画面变化），而像素变化已由 `real-path-replay.py` 的 `real-path-before-tap.png`/`after-tap.png` 覆盖 | **已删除**。`scripts/android.sh assembleDebugAndroidTest` → `BUILD SUCCESSFUL in 21s` |
| `wrong-token-slice.py` 的 `wrong-token-no-media-grant`、`wrong-token-no-encoder-child` | 反例实验中 session 已建立，这两个门仍为绿。裸 `/control` 连接只开 session 并回 `ready`，`/media` 仍 403，也没有 encoder adapter，所以两个门对 token 是否被拒不敏感 | **已删除**（§3.1.1） |

删除的依据不是"数量太多"，而是这三项各自的断言在当前契约下**要么必假、要么不敏感**。它们留在套件里会让"全绿"失去意义。

保留待确认，本次不删：`AccountDeviceTest`。它引用 `relayFile("ca.der")`、`wrong-ca.der`、`deviceId`，属于 **relay 账号路径**（登录、注册设备、吊销），不是本次改动的连接路径。它目前没有被任何 runner 引用，所以是"未验证"，不是"错误"。要删它必须先确认 relay 账号面是否仍在范围内。

同样未被 runner 引用但断言真实行为的：`AnnexBDeviceTest.testHostAccessUnits`、`ProbeDeviceTest.testRealUiMediaLifecycle`、`LayoutDeviceTest` 两项、`WebRtcDeviceTest.testTypedWebRtcReachesNativeMedia`。它们测的是媒体与布局，不在本次改动范围，保留。

### 5.1 审计后保留的、容易被误判为"错误"的测试

| 测试 | 为什么看起来错 | 为什么保留 |
| --- | --- | --- |
| `webrtc_endpoint.rs` 的 3 个 `#[ignore]`（`r10_f5_*`） | 默认不跑，等于没有覆盖 | 它们是**显式跳过并记录原因**，且没有注入时断言 `F5 DEVICE INVALID`，不会伪造通过。运行器 `f5-blackbox.py` 存在于 OB 主仓的 `.worker-runs/m1-p3-r10/r10-webrtc/`，不在本 worktree。符合"缺 gate 显式失败，绝不 mock 成功" |
| `probe.test.ts::connectionSetup rejects sensitive, overlength, control, and malformed fields` 的 86/91/92/93 行 | 用例里出现 `transfer_code`、`repair_import_allowed`、`active_revision`、`state:'required'` 这些已删概念 | 断言本身**正确且当前有效**：投影解析器必须拒绝未知字段与未知状态（§7.6 只允许 `unconfigured`/`configured`）。字段名只是"未知字段"的实例，不是对被删功能的断言。命名陈旧，断言有效，保留 |

这两类都不删。删掉它们会减少真实保障，属于"按数量砍测试"，不是消融。

## 6. 新增测试的判定

按用户规则，新增前必须给出失败可能性的依据、失败影响、执行与维护成本。概率未知就写未知。

**当前没有必须新增的测试。** 需求 R1–R4 与不变量 I1–I3 都已有黑盒证据（§2），不需要按数量或场景类别扩面。

唯一值得做的动作不是新增测试，而是给已有测试补反例，**已完成**：

- **对照实验**：把 Host 的 token 设成切片用的"错误" token，确认 `wrong-token-slice.py` 变红，然后恢复。
- 失败可能性依据：未知（没有历史数据）。
- 失败影响：高。这是连接路径唯一的检查，失效等于任何客户端都能建 session。
- 成本：一次本地运行，无新增测试代码。
- 结果：**变红**，并顺带删掉两个死门（§3.1.1）。R4 的抓错能力由论证变为证明。

新增测试的判定规则保留在此，供后续变更使用。

## 7. 与冻结契约的对应

本审计对照 `docs/m1/connection-minimum.md` §7。契约变更时，§2 的映射必须同步复核，§3 的判定按变化范围重查。

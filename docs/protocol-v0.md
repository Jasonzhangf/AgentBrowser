# v0 协议契约与首轮探针

状态：可审阅、可校验的设计草案，非 runtime ABI 发布。2026-09-05。

模块职责来自 [architecture.md](architecture.md)，operation 语义来自 [design.md](design.md)。此处细化首轮协议；正式实现前 browser ABI 移至 Obscura 的唯一 owner，relay ABI 移至 AgentBrowser `protocol/relay/`，删除对应草案定义，仅保留链接。没有两份并行 runtime 真源。

## 三层标识与授权

| 层 | 身份 | 有效期 / 规则 |
| --- | --- | --- |
| 账号目录 | accountId、deviceId、hostId、hostIncarnation | 设备注册密钥绑定；Host 重启产生新 incarnation；目录是发布投影 |
| 浏览器控制 | sessionId、tabId、attachmentId、controlEpoch | Session 不随连接销毁；Host 独占 epoch；只允许当前控制者写入 |
| 物理传输 | connectionGeneration、tunnelId、streamGeneration | 换路失效旧连接；视频和输入通道绑定相同对端授权 |

operationId 在同一 Host incarnation 内不可重复用于不同命令。重发先比较原命令及身份；相同请求返回原状态，不重新执行；不同内容同 ID 返回冲突；Host 重启后原请求返回结果未知，调用者不能当作新请求自动执行。

## 建连和数据通道

1. Relay account 登录、设备密钥注册和目录查询。端点列表不携带 Host 长期 bearer token。
2. 客户端获取绑定自身设备和目标 Host 的短期连接授权，双方验证对端身份及权限。
3. ConnectionService 优先尝试 UDP WebRTC/Tailscale；失败后显式建立 WSS relay tunnel。每条候选绑定独立 attempt ID；认证失败不降低认证要求。
4. 完成 `hello` 协商：协议版本、支持的 operation、codec、分辨率、帧率及通道能力。协商失败返回 typed error，不能假报 ready。
5. Host `attach` 返回当前 Session/Tab/viewport/控制权快照和 revision，再按顺序订阅增量；客户端遇到 gap 请求新快照，不自行合并历史状态。
6. media ready 单独确认。UI 选择观察或请求接管；有画面不意味着有控制权。

本地 endpoint 使用预配对本机身份，也执行步骤 4-6。接管不改变物理连接，换路不创建 BrowserSession。

WSS relay 分两个 tunnel channel：control（可靠有序、包括操作和状态）与 media（H.264 access units）。控制包不得插入网页对象或视频 codec 配置。media 采用二进制长度帧，字段包含 stream generation、frame sequence、PTS、keyframe、codec configuration reference、内容长度；只允许经过认证且长度受限的帧进入解码器。codec configuration 与首个关键帧必须齐备才显示。

端到端握手/密钥轮转库尚待选择，v0 不声称加密协议已完成；实现该层之前必须锁定成熟库和双方设备身份验证方式。控制与媒体可复用同一授权上下文但密钥用途分离。

## 原子 operation 的 Host 状态机

```text
received → rejected
         → queued → running → succeeded
                            → failed_stopped
                            → outcome_unknown
```

`rejected` 表示未开始、无本操作影响；`failed_stopped` 可能已有部分影响，但 Host 已确认不再注入输入且已释放按键/触点；`outcome_unknown` 不允许交接或自动重放。状态转换基于执行器事实，不根据日志或视频推断。schema 只能校验消息形状，不能证明此状态机已执行。

接管请求在 Host 接受点形成写入屏障：拒绝或暂停未开始的 Agent operation；当前 operation 仍允许其内部事件完成；没有运行中 operation 时立即移交。正常完成或 failed_stopped 后，递增 controlEpoch 并发布 granted。outcome_unknown 保持等待/错误，直到确认停止。先开始 operation 还是先接受接管由同一串行 owner 决定。

交还 Agent 作为显式 release_control 请求处理，仍在人工 operation 的结束边界执行；默认不因人工断连自动恢复 Agent，避免不明操作继续。该异常/交还策略为本轮建议，尚未用户单独确认。

## 首轮 schema 范围

[protocol-draft.schema.json](protocol-draft.schema.json) 只定义 click、input_text 的请求及接管请求/operation 结果形状，供 P1 契约讨论与一致性检查。其他 operation 仍按 design.md 声明，不在支持集中就显式拒绝。位置采用 CSS viewport 坐标；文档和 viewport revision 与控制 epoch 均必填，实际匹配必须由 Host 验证。

建议首版请求文本上限 4096 个 JSON Schema 字符；客户端提交前显式检查，超限返回错误或由 Agent 在 operation 边界拆分。数值上限为协议建议，不代表已对用户数据做裁剪。所有 ID 上限 128 字符，未知字段拒绝；高频事件不经 UI event bus。

## 预检结果（本轮实际运行）

| 检查 | 结果 | 证明范围 |
| --- | --- | --- |
| AppSDK verify | `ok:true, project_id:agentbrowser, stage:draft` | 真实设计项目配置可验证；modules 空，无 runtime 实现 |
| Guidance compile | `GUIDANCE_MAIN_MUTATION_FORBIDDEN` | 无 origin/main 基线，原状态保留；未编译成功 |
| Guidance status | `GUIDANCE_MODULES_EMPTY` | 设计期未登记虚假模块；没有 active PlanRecord |
| Android adb | 用户指定 Tailscale 15T 后连接成功；型号 PLZ110，Android 16/API 36，arm64，1216×2640，WebView 153.0.8010.11 | 已读取真机能力，尚未安装/运行 AgentBrowser |
| Mac 工具链 | Xcode 26.3、Cargo 1.97.1、Node 22.22.2 | 工具存在；未证明产品构建 |
| Cordis 3.18.1 | Node 中插件 start=1、dispose=1，断言通过 | 进程内生命周期；使用 zterm 已安装依赖只读执行，未安装 AgentBrowser 依赖；未证明 WebView bundle |
| H.264 VideoToolbox | 原始 testsrc2 → 硬件编码（allow_sw=0）→ 解码成功；720×1280，共 30 帧 | 编码前无 PNG；解码为 FFmpeg 路径，未证明 Obscura raw-frame、网络、原生 UI 接收 |
| Tailscale | Running；对 15T 的 ping 经实际 LAN endpoint 直达，约 4ms，远程 ADB 成功 | 当前点对点路径有实测；未证明跨公网打洞、WebRTC 或 Relay |
| Android codec 声明 | vendor XML 包含 c2.qti.avc.decoder 与 HEVC decoder | 仅设备声明；未运行 MediaCodec 解码 |
| 协议 schema | Draft202012Validator.check_schema 通过，13 个正反样例均符合预期 | 消息形状校验；不证明执行状态机和权限检查 |

H.264 临时样本：`/tmp/agentbrowser-media.NxcIpn/portrait.h264`，可能被系统清理，不作为发布产物。命令与真实输出在本任务工具记录中；不把手写表格当机器 PASS。

## 下一步实际探针

首个 Git 基线和独占工作树就绪后：构建 Android 最小原生壳 + Cordis UI；分别将测试 H.264 经本地、UDP/Tailscale、WSS relay 送入 Android 解码显示；加入点击/文本 operation 回执与 takeover 竞争测试。测试流成功后才接 Obscura paint，最后同入口回放目标页面。网络、解码、浏览器三层结果分别记录。

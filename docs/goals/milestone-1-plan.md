# M1：手机优先的可用远程浏览器

本文件是 M1 执行与验收计划。产品语义引用 ../design.md，模块和协议 owner
引用 ../architecture.md；不另建协议真源。旧计划的状态是历史记录，执行前以
当前源码、已安装产物及本轮证据核对。此任务直接实施，不再生成同任务提示词。

## 目标与范围

完成可安装、可日常启动的首个版本：Mac 运行 Agent 和 Obscura daemon；Android
与 Mac UI 可 attach/detach、观察和接管，页面持续存在。UI 采用现有 Cordis
插件边界，媒体和输入在原生层处理。交付两仓 main、可复现版本基线、milestone
产物及基于已验证事实重建的 AppSDK 项目 memory。

M1 包括基础导航、点击、滚动、英文与中文输入、表单可见绘制、横竖屏/键盘、
双端共享布局、原子操作交接、重连、最小账号/设备目录与真实直连/Relay 链路。
不能将当前预配对 WSS 探针改名为完整 M1。网络范围继承既有设计：本地连接、
UDP/WebRTC 和 Tailscale 优先、直接路径不可用时走已协商的 WSS Relay。

不包含 Linux/iOS 客户端交付、完整浏览器兼容性、运行态跨 Host 迁移、HEVC
优化、零拷贝、多码率或高性能指标。H.264 必须可用；HEVC 不得是唯一协议。
不以扩展这些非目标延后可用版本。

## 恢复入口与待修复问题

- AgentBrowser 当前保留候选：playground/network-replay-integrity，ae5edbc。
  执行时核对 commit 和工作树，不将旧摘要当作当前事实。
- 已有真机证据：该工作树 evidence/network/84f531396ab549dfa89010c778826f9c。
  347×580 CSS 与源画面一致，真实触摸、忙时尺寸合并、DOM 输入、重连通过。
  这不是横竖屏、多端、可见文字或完整产品通过的证据。
- 初轮比例/验收入口改动 AGY PASS；其后的连接层和治理变更尚未获最终组合
  admission/review。主包与测试包必须都验证安装身份，不能复用旧测试 APK。
- AppSDK 当前阻断 MODULE_DEPENDENCY_NOT_FROZEN。核对 development dependency
  与 frozen artifact dependency 的实际契约，选择真实受支持路径。禁止删除
  必需依赖、伪造冻结、手写编译记录或修改锁文件来掩盖失败。若确需修改 AppSDK，
  先落实其独立 owner、契约、回归及验证后的升级，再回到产品交付；产品修复继续。
- Obscura 保留候选位于 playground/obscura-fork/playground/mobile-viewport；
  旧 form-value-paint 候选未完成，不能直接合并。当前表单 DOM 已更新但画面仍
  显示 placeholder；修实际表单状态/绘制 owner，不保留 JS/native 双份可写值。
- 15T 为授权 Android 真机，通过 Tailscale、ADB 5555 使用；地址、在线状态、
  安装包和前台状态必须实时核对。已有会话不因测试被擅自替换。

## 技术方案与文件边界

| Owner | 文件/模块入口 | 必须实现的行为 |
| --- | --- | --- |
| Android platform | apps/android/、packages/android-bridge/ | 实测可用 stage、IME/insets、原生触摸与解码、生命周期隔离 |
| Client connection | packages/client-connection/ | 认证、连接代次、显示确认、明确路径选择、旧输入拒绝且不误杀正常连接 |
| Cordis UI/domain | packages/ui-kernel/、packages/ui-plugins/、packages/client-domain/ | 连接/导航、观察/等待/接管提示、输入、可理解的错误状态 |
| Mac platform | 按 architecture.md 绑定当前或新建 Mac 平台入口 | 原生接收与输入，复用共享 UI/连接 owner，不复制 Host |
| Relay | services/relay/、protocol/relay/，先核实实际目录 | 最小账号、设备绑定、目录/同步、信令及 WSS 数据面、跨账号隔离 |
| Obscura Host/protocol | fork 中 protocol/browser/ 及 Host 实际入口 | Session/Profile 持久 owner、统一 viewport、原子 operation/控制权仲裁 |
| Obscura render/JS/media | fork 中实际 DOM/JS/paint/media owner | 单一表单当前值、正确失效与绘制、共享原始帧直接编码 H.264 |
| Verification | scripts/network-replay.py、scripts/validate-android.mjs、scripts/connection-admission.mjs、两仓相关测试 | 本轮产物、依赖、安装身份和实际入口证据可追溯 |

尚未存在的模块须按架构先绑定最小 owner/map，再实现；不复制 zterm 的终端
生命周期或协议。两仓使用 Obscura 唯一 Browser ABI 的明确版本绑定。

手机参与时以手机可用区域决定共享页面布局；所有观察者接收相同 viewport
revision 和源尺寸。按完整宽高规格仲裁，不拼接不同设备的最小宽高。旋转和
键盘变化保留文档、JS、表单、滚动状态，不 reload；新状态与对应画面确认后
才恢复输入。观察不暂停 Agent，但仍遵守统一布局规则。

接管请求阻止新的 Agent operation；已经接受的当前点击/输入等 operation
结束并释放输入状态后才交接。输入法 composition 到 commit/cancel 是明确
边界。异常或结果未知不得伪报完成，不自动重放点击/提交。人工断连进入 Host
已声明的暂停/恢复语义，不能凭断连自行恢复 Agent 写权限。

## 验证矩阵

| 场景 | 必需证据 |
| --- | --- |
| 表单与导航 | 真 UI 打开确定样本，真实触摸聚焦、输入/清空/替换/提交；DOM 与可见像素同时正确；SPA 与滚动样本 |
| 统一布局 | Android+Mac 同 Session，同 viewport revision/源尺寸；竖屏、横屏、键盘弹出收起，无页面拉伸或内容裁切 |
| 状态保持 | 布局切换、detach/reconnect 前后文档身份、JS 计数、输入与滚动状态连续；无人观察时页面任务继续 |
| 交接 | 观察无写入；Agent 当前操作结束前人工被拒绝，结束后旧 Agent/排队操作被拒绝；显式交还可继续 |
| 故障边界 | 旧坐标/代次、操作失败与未知、半途断网、解码失败、后台和 Surface 销毁，无重复提交、卡键、残留资源 |
| 网络 | 本地、LAN/跨网 UDP、Tailscale、强制断直连后的 WSS Relay 均有真实视频和 operation 回执；账号隔离与失效凭据拒绝 |
| 路径真实性 | 分开记录 network path 与 transport；Tailscale 底层不可证明时标 unknown；信令成功不是媒体成功 |
| 产物/交付 | 两仓定向测试与必需构建、当前主/测试 APK 哈希、Mac 安装重启、真实客户端入口、正式 admission/review、main 远端回执 |

Obscura 按项目规则使用 nextest，选择真正启用 raster 的绘制测试；零测试或
仅 JS value 测试不能证明绘制。截图用于验证与诊断，不进入主视频编码链。
每次回放使用独立 run 身份，失败不能消费旧 PASS/截图。仅在影响输入变化时
重跑相关验证；不为收集数量而重复全套。

## 实施顺序

1. 读两仓 AGENTS、现有候选及交接，核对远端最新 main。在各仓新的干净 owner
   worktree 接续候选；保护所有旧工作树，不 reset/清理他人改动。
2. 将剩余工作拆成有明确文件归属的独立模块。需要并行时使用有隔离 worktree
   和 claim 的 worker，统一串行集成；旧坏 session 不复用。
3. 优先修表单可见绘制、手机输入/键盘/旋转、状态与控制交接，完成 Android
   可用闭环；同时处理互不依赖的 Mac 客户端和开发依赖治理问题。
4. 补最小账号/目录、真实直连与 Relay；Mac 与 Android 完成同一 Host 多端回放。
   不擅自删减已确认的网络验收项来宣布 M1。
5. 完成两仓当前组合候选的正式质量验收与 review。若存在具体阻断，修唯一
   owner 后重跑受影响验证；独立工作继续，不停留在报告或反复请求继续。
6. 在已有授权范围内集成、推送两仓 main；记录各自远端 commit、协议绑定和
   构建身份。按仓库版本规则选取不冲突的 M1 tag/版本，发布可复现安装产物。
7. 在产品验收通过以后建立基础基线和 milestone。再按各仓 project-memory
   技能重建/同步 AppSDK 项目 memory，记录已验证架构、版本、入口、运行方式、
   证据索引与实际限制；验证 memory 可检索/恢复。不晋升未授权的全局规则。

## 风险与完成定义

主要风险是 Obscura 实际网页兼容性、IME/事件语义、帧与控制状态竞争、多端
viewport 仲裁、网络身份隔离及开发依赖冻结门禁。分别以真实样本和负例定位，
不能添加自动聚焦、固定等待、截图视频或跳过鉴权来使测试变绿。

完成必须同时满足：上述核心流程和矩阵通过；Android/Mac 安装包可用；Host
独立存活；已确认的网络路径真实可用；两仓正式 gates/review/main 回执齐全；
版本与协议依赖可复现；M1 baseline、产物、运行说明和项目 memory 均存在且
可验证。报告产品、源码、安装、发布和资源清理的实际完成状态。

最终交付附版本/commit、安装产物路径、启动连接说明、测试与 review 证据、
已知非目标限制和 memory 验证结果。不得以计划完成、探针通过或单端截图
代替 M1；遇到必须用户提供的外部资源才明确提问，其余授权工作持续推进。

## 长程执行与调度协议（2026-09-06）

本节定义长程任务的管理方式；它不改变产品协议、模块 owner 或验收标准。
主线程是唯一集成与交付 owner。worker 只在声明的 clean worktree 和文件范围内
实现、验证并提交候选；worker 的 PASS、thread 完成或本地 commit 都不等于集成、
main、远端或产品验收通过。

### 主线程职责

主线程负责：读取最新 main 和本计划；拆分依赖图；决定唯一 owner 和非目标；为
每个 worker 分配 worktree、文件范围、输入资源和验收证据；维护串行 merge queue；
处理冲突和跨模块协议；运行组合候选的 pinned AppSDK admission、AGY review、
真实双端回放；配置并核对两仓保护；按授权 merge/push；生成版本、产物、启动说明、
baseline 和 project memory；最后分别报告源码、测试、构建、安装、重启、入口回放、
review、merge、远端回执和资源清理。

主线程不把可独立完成的实现细节抢回本地，也不让 worker 直接改 main、互相覆盖
worktree、替代组合验证或自行宣布 M1 完成。主线程自己保留架构决策、跨仓协议裁决、
集成顺序、冲突解决、最终 runtime matrix、发布与 memory 重建。

### worker 分工

worker 必须使用 Luna（`gpt-5.6-luna`，`max`）并在新 session 或独立 worktree
工作。每次派发只包含一个可验收的 bounded slice、唯一文件 owner、禁止触碰的路径、
依赖 commit、资源 claim、红测/绿测和交付格式。适合派发的任务包括：Obscura 单一
协议或渲染 owner、Android 单一平台入口、Mac 单一平台入口、Relay 单一 wire/API、
定向测试与真实回放、只读 review/证据整理。不得把“完成 M1”、跨仓自由重构、main
合并、最终发布或多 owner 混合任务派给 worker。

worker 交付包必须包含：branch/worktree、候选 commit、实际变更文件、测试/build
命令和原始结果、入口回放及证据目录、产物与哈希、已知失败/未验证项、仍占用的资源、
安全释放说明。证据缺失时状态为 `candidate/incomplete`，不能写成 PASS。

### 并发、session 与资源

最多同时运行 5 个实现/回放 worker，主线程保留至少 1 个调度槽；依赖关系明确前
不并发，互斥资源不得并发。常用 lanes 为 Obscura、Android、Mac、Relay、治理/回放，
每个 lane 只有一个 owner。坏 session 不复用：创建新的 Luna session 和新的
`playground/<task>` worktree，先接收旧 worker 已提交状态，再继续未完成范围；旧
worktree 保留到证据、合并和清理义务完成。

Android 15T、Mac 前台、端口、PID、fixture、ADB 隧道、临时目录和浏览器 profile
都必须先 claim 再使用。15T 不清 data、不替换用户会话、不重置信任；Mac 前台和
他人 PID 不抢占。清理只允许资源 owner 使用明确 PID/句柄执行，禁止按进程名、路径
或批量命令推断归属。pinned AppSDK witness 复制到每个 worker 自己的 ignored
evidence 工具目录并置于 PATH；禁止全局 init、installer、pin-lock、升级、回滚。

### 等待与进度

派发后 30 秒做一次初始 snapshot；活跃 worker 之后每 60 秒检查一次，长构建或真机
回放使用 120 秒间隔，单次最长等待 5 分钟。使用带 cursor 的批量 wait；无变化不重复
叙述。连续三个周期没有新证据时，主线程发送一次明确的状态请求（完成/阻断/下一条
证据/资源）；仍无响应则暂停该 claim，保留现场并启用新的 Luna session，不强杀旧 PID。
等待期间主线程继续做不依赖该结果的审查、协议准备或其他 lane 集成。worker 报告
失败时只修首个 owner 边界，保留失败证据并按受影响 gate 重跑；不得用 timeout、截图、
日志或 silence 推断成功。

### 集成与交付

worker 候选按依赖顺序进入主线程组合 worktree；每次只集成一个候选，记录 source
commit、tree、SDK witness、配置、依赖和资源身份，先跑受影响定向 gate，再跑组合
admission/review。冲突由主线程依据 owner 和协议真源解决，不能把两个实现并存成
fallback。review PASS 后才按用户授权串行 merge/push；两仓分别记录远端回执。
产品验收、main 交付、版本/产物 baseline、资源释放、project-memory 重建是独立
状态，必须逐项有证据。未满足 M1 DoD 时 goal 保持 active，不能以 worker 数量、计划
完成或局部 PASS 收尾。

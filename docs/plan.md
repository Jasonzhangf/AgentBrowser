# 讨论与实施计划

当前阶段：设计讨论；无运行时模块已实现。步骤状态只说明计划进展，PASS 以各仓规范证据为准。

| 阶段 | Owner / 范围 | 前置 | 验收证据 | 当前状态 |
| --- | --- | --- | --- | --- |
| P0 治理启动 | AgentBrowser：项目配置、设计与计划 | 用户授权已给出 | 初始化结果、真实项目绑定验证、讨论决策记录 | agentbrowser draft verify 通过；Guidance/worktree 未就绪 |
| P1 契约讨论 | AgentBrowser 产品契约；Host 协议 owner 待落盘 | D1-D4 | 布局、控制权、断连及错误语义明确，协议真源唯一 | 点击/输入级 operation 已确认；目录草案已写，异常交接与 D2/D3 待细化 |
| P1a 模块与连接设计 | 两仓 owner，定义见 architecture.md | Cordis、daemon、Relay 用户约束 | 明确唯一 owner、直连/中继协议、参考实现缺口 | 设计已落盘；未绑定实现模块 |
| P2 最小兼容性探针 | Obscura 原始帧/输入；Android 显示/IME | 真机型号与目标页面确定 | 登录/表单/SPA/滚动样本；Cordis WebView/native bridge 与原生 H.264 接收；区分 engine 与客户端缺陷 | Mac 编码和 Cordis Node 预检通过；15T ADB 已连，产品链路未执行 |
| P2a 连接与 Relay 纵向闭环 | AB-05/06、OB-04，配合平台 host | P1a 的协议绑定、P2 媒体探针 | 账号登录/设备注册/目录、UDP/Tailscale、强制阻断直连后 WSS 输入与视频 | 未开始 |
| P3 持久 Host | Obscura Session owner | P1、Obscura 干净工作树 | UI 断开后 JS/请求继续，重连无页面重建 | 未开始 |
| P4 Android 主流程 | 两仓：统一帧、H.264、触控、IME | P2、P2a、P3 | 触摸、中文组合输入、横竖屏、重连真机证据 | 未开始 |
| P5 Mac 观察与控制交接 | AgentBrowser Mac；Host 仲裁 | P4 | 双端同 layout/buffer，旧输入拒绝，Agent/人工交接 | 未开始 |
| P6 首版交付 | 两仓各自验证/review | P3-P5 | 各仓 commit/构建身份、必要安装、同入口回放、review 分别通过 | 未开始 |

## 核心实际入口验收

Mac 启动 Host/Agent → Android attach，保留页面状态并统一手机 layout → UI 选择观察，Agent 继续操作 → 请求接管，UI 显示等待，Agent 完成当前操作且不启动新操作 → Host 确认移交后人工触摸和中文输入 → Mac 以观察模式 attach 看到同布局 → Android 切横竖屏，两端同步 → 按待确认的交还协议恢复 Agent 控制 → Android 断连，Agent 继续 → Android 重连，原页面状态连续。

针对 D1/D4 增加验收：布局切换前后的文档身份、JS 状态与表单值保持；观察不会暂停 Agent 或注入人工输入；接管请求与下一项操作竞争时只有 Host 仲裁后允许的一方执行；当前操作完成前人工输入被拒绝，完成后旧 Agent/排队写操作被拒绝；执行方失联不伪报接管成功。

原子 operation 验收（定义以 `design.md` 为唯一源）：按下/释放之间不可交接；input_text 结束后可接管且随后 Enter 不执行；IME commit/cancel 前不可交接；拖动结束前不应用新的 viewport；只读等待不阻塞接管；网页异步请求继续不被误判为 Agent 未暂停；失败的部分影响显式报告；operation ID 重发不造成重复点击或提交。每个场景分别验证正常结束、失败和断连，并区分“失败已停止”与“执行状态未知”。

同时验证：旧 viewport 点击拒绝、手机网络中断不销毁页面、断连无卡键/残留触摸、无观察者仍执行页面任务、视频协商失败显式报告、未经授权客户端不能读取或控制 Session。记录资源和延迟实测，不预设高性能门槛；持续队列增长仍是缺陷。

连接矩阵：本地配对、LAN UDP、跨网 UDP、Tailscale 实际路径、直连不可用的 WSS Relay 分别完成视频显示与 operation 回执。WSS 视频繁忙时控制队列独立有界；网络切换不重复 operation，旧 generation 不更新状态；跨账号隧道拒绝；目录过期不得显示为 confirmed；UI/Cordis 插件卸载不销毁 daemon 页面。Tailscale 未确认底层路径时报告 unknown。配置/枚举/信令成功不替代上述真机入口证据。

## 当前证据与限制

- 2026-09-05：两个 GitHub 仓库只读访问成功；AgentBrowser 无远端提交；Obscura fork main 为设计中的基线。
- `appsdk prepare` 生成 draft；确认当前用户给出的初始化范围后 `appsdk init` 成功。
- 初始 `appsdk verify` 返回 `ok:true, project_id:change-me, stage:draft`，仅证明 SDK 模板可验证。
- `appsdk guide status` 返回 `GUIDANCE_NOT_COMPILED`；bootstrap 为只读提案入口，未创建执行计划记录或伪造生命周期 PASS。
- Collab 报告无 live tmux pane，peer bootstrap pending；本轮无多 worker 或共享写入。
- 尚无初始 main，无法从 origin/main 建立开发工作树。代码开发前先单独授权并建立首个基线提交，再按全局工作树规则执行。
- 没有 commit、push、安装、重启或运行时验证。本轮不改变已有 Obscura checkout 的 upstream remote。

本轮追加：用户同意继续后采用治理配置提案；`appsdk verify` 对 `agentbrowser/draft` 通过。`guide compile` 返回 `GUIDANCE_MAIN_MUTATION_FORBIDDEN`，`guide status` 返回 `GUIDANCE_MODULES_EMPTY`；未手工写 compiled manifest、PlanRecord 或补假模块。正式代码前仍需首个 main 基线与独占 worktree。

能力预检与协议形状验证结果见 [protocol-v0.md](protocol-v0.md)。用户指定 Tailscale 上的 15T 作为真机，远程 ADB 已连通；该路径地址每次从 live Tailscale 状态读取，不固化账号网络地址到产品契约。具体设备应用/媒体入口验收仍未执行。

## 下一轮讨论顺序

D1/D4 与点击/输入级 operation 已确认，具体目录和完成边界草案见 `design.md`；模块与网络策略已在 `architecture.md` 定义。v0 协议已有 schema 草案。下一步先取得首个 main 基线，在独占工作树完成真实 Android 壳、Cordis/native bridge、H.264 和连接探针。D2/D3、异常交接与加密握手库继续细化。治理配置已应用；Git 基线提交/推送仍需明确授权。

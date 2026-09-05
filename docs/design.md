# AgentBrowser 设计讨论基线

状态：讨论稿。确认需求来自当前用户对话；其余标为提案。2026-09-05。

## 已确认需求

1. UI 与 browser core 分离，core 可运行于不同设备；UI 可 attach/detach，通过本地或网络连接。
2. 修改用户指定的 Obscura fork，承载浏览器执行和持久 Host；AgentBrowser 承载客户端 UI。
3. 大兼容小：手机存在时统一手机 layout，所有观察者对齐；支持横屏和竖屏，统一渲染 buffer。
4. 第一版 Mac 本地运行 Agent 与 Host；远端客户端先做 Mac 和 Android，手机体验优先。
5. 第一版不苛求性能；正确交互、持续运行和证据真实性仍须保证。
6. 视频从原始帧编码，不经过 PNG 中转；H.264 默认，HEVC 按能力启用，非唯一协议。
7. 使用 AppSDK 开始项目治理，与设计和计划讨论同步推进。
8. UI 尽量使用 Cordis 插件架构；浏览器以 daemon 运行。Relay 提供账号、信息同步及 WebSocket 数据面；连接优先 UDP/Tailscale，之后 Relay。参考 zterm 的连接系统。

## 当前代码事实

审查基线：Obscura `72c84adcc6ec3ea4a7144adb4e45d4d3038ebcda`，与用户 fork 当前 main 一致。源码只读结论，未测帧率或真机交互。

| 事实 | fork 内证据 |
| --- | --- |
| 连接拥有 CdpContext/运行时，断连 abort processor | `crates/obscura-cdp/src/server.rs`，`run_connection` |
| paint 可返回原始 Pixmap；上层截图返回 PNG | `crates/obscura-render/src/paint.rs`、`crates/obscura-js/src/runtime.rs` |
| screencast 先截图，必要时解码 PNG 后重编码 | `crates/obscura-cdp/src/domains/page.rs`，`encode_screencast_frame` |
| 触控方法成功空返回 | `crates/obscura-cdp/src/domains/input.rs`，`dispatchTouchEvent` |
| cookies 持久化不等于完整 profile | `crates/obscura-browser/src/context.rs`、`crates/obscura-js/js/bootstrap.js` |
| 媒体、IndexedDB 等仍有缺失或占位行为 | `crates/obscura-js/js/bootstrap.js` |

## 架构入口

[architecture.md](architecture.md) 是模块、计划路径、协议 owner、Cordis 边界和连接策略的唯一源。部署为 AgentBrowser 客户端、Obscura daemon、Relay 服务三部分。Obscura 自己的构建与测试门禁仍由该仓库维护，AgentBrowser 的 AppSDK PASS 不替代它。

Session 存活独立于 Attachment；断连只释放观察和控制连接。Host 重启恢复持久状态与运行中断连保活分开验收。不承诺运行态跨 Host 迁移。

控制、视频和输入按 architecture.md 的显式 WebRTC/WSS backend 传输，所有写操作汇入同一 Host owner。截图兼容模式需显式协商、显示状态，不能隐藏视频失败。SSRF/文件访问策略独立于客户端连接权限。

渲染链：现有 retained layout/paint → 原始帧 → 颜色转换 → H.264 → WebRTC。所有观察者共享同一布局与输出尺寸，客户端只缩放显示。原始帧包含像素格式、尺寸、stride、颜色与时间信息；Session/viewport 决策保存在 typed control resource，不写入网页 payload。

## 第一轮待讨论决策

| ID | 问题 | 推荐方案 | 状态 |
| --- | --- | --- | --- |
| D1 | 手机加入时“刷新”是否 reload？ | 保留页面状态重新布局；不自动 reload | 已确认 |
| D2 | 手机离开后是否恢复桌面？ | 保持最后布局，控制者可切桌面，防止短断连抖动 | 待用户确认 |
| D3 | 多手机尺寸与方向仲裁 | Host 选最受限实际规格；横竖屏由控制者决定；比较规则需绑定完整宽高和 CSS DPR | 待细化 |
| D4 | Agent 与人同时操作 | UI 提供观察/接管；观察不影响 Agent；在点击、输入等 operation 边界移交 | 已确认；具体 operation 契约见下文草案 |
| D5 | Android 客户端技术 | Cordis 共享 UI 插件 + 原生连接/媒体/IME，具体模块见 architecture.md | 方向已确定；构建与媒体桥待探针 |
| D6 | Profile 首版范围 | 至少验收目标站点登录状态；缺失存储不得以 cookies 保存冒充完成 | 待目标站点选择 |

横竖屏切换时 Host 递增 viewport revision。输入携带对应 revision，旧坐标拒绝；客户端收到新控制状态和对应画面后恢复点击。断连释放按键和触摸状态。图片/视频和状态到达顺序必须纳入协议测试。

## D1 / D4 已确认交互语义

重新布局保留当前文档、JS 和表单状态，不自动重新导航或 reload；页面本身响应 resize 的行为仍正常执行。

UI 明确提示选择“观察模式”或“接管模式”。观察不抢控制权、不暂停 Agent，也不向页面注入人工操作。手机 attach 仍参与已确认的统一手机布局策略；“不影响”不意味着维持原桌面 viewport。

请求接管后停止启动新的 Agent 操作，已经开始的当前操作完成后才转交人工控制；不强行打断当前操作，也不等待整个 Agent 任务结束。

建议由 Host 仲裁以下状态（协议细节尚未实现）：

| UI 状态 | Agent | 人工页面输入 |
| --- | --- | --- |
| 观察模式 | 正常操作 | 禁止；可请求接管 |
| 等待接管：等待当前操作完成 | 完成当前操作，不启动下一项 | 禁止 |
| 接管模式：你正在控制 | 暂停操作 | 允许 |

Host 确认移交后 UI 才进入接管模式。操作完成需由执行方的正式生命周期确认，不能根据画面静止、日志、固定等待时间或 DataChannel 已送达推断。排队但尚未开始的操作不得在接管期间执行。

待细化边界：当前操作失败/超时/执行方失联时如何确认不再写入；接管请求取消、人工断连及交还 Agent 的行为。建议边界未确认时显式保持等待/错误状态，不凭超时自动判定接管成功。

## 浏览器原子 operation

用户已确认：一次操作指点击、输入等浏览器 operation。以下为具体协议定义草案，尚未实现。

原子性表示控制权不能在一个 operation 内移交，其他操作者不能交错注入输入；不表示 DOM、网络、页面脚本被冻结，也不承诺事务回滚。一个 operation 可以包含多个底层输入事件或 CDP 命令。登录、搜索、填写整个表单等任务必须拆成 operation，不能包装为一个不可接管的大操作。

### 首版 operation 目录与完成边界（建议）

| Operation | 一个操作的范围 | 完成边界 |
| --- | --- | --- |
| `click` / `tap` | 一次点击；必要的定位、按下、释放及点击派发 | 对应派发完成且无按住状态 |
| `double_click` / `long_press` | 一次具有独立语义的双击或长按手势 | 完整事件序列和释放完成；长按持续时间有上限 |
| `input_text` | 向当前选区插入一次请求中的有限文本；不暗含提交表单 | 文本编辑及同步输入事件派发完成 |
| `replace_text` | 明确目标字段的一次替换 | 字段编辑及对应事件派发完成；不得伪装为真实逐键输入 |
| `press_key` | 一次按键或快捷键，如 Enter、Ctrl+A | 所有关联按键释放，事件派发完成 |
| `scroll` | 一次请求的有限滚动量；连续手势在结束点封口 | 本次滚动输入处理结束；不等待页面网络空闲 |
| `drag` / `swipe` / `pinch` | 一次完整指针或触摸序列 | up/end/cancel 已处理，无残留捕获或触点 |
| `select_option` | 一次选项选择 | 选择及对应同步事件完成 |
| `navigate` / `back` / `forward` / `reload` | 一次导航命令 | 导航提交或显式失败；load/站点业务完成由独立观察等待判断 |

具体支持集通过能力协议声明；目录不代表现有 Obscura 已支持。操作参数必须有显式大小/时长边界；超限拒绝或由调用方拆分，不能静默截断。双击必须显式声明，不能把两次独立 click 自动拼成不可接管操作。

Android IME 的一次 composition 从 start 到 commit/cancel 构成一个输入操作，不能在预编辑文本中间转移控制权。候选词确认后下一次输入属于下一 operation。IME/触摸中途断连需先确认 cancel/释放完成才能移交；超时仅说明异常，不能证明输入已停止。

### 接管与执行仲裁（建议）

Host 每个 Session 同时执行至多一个可写 operation。控制请求与 operation 开始由同一 owner 串行裁决：接管先被接受则不再启动新 operation；operation 先开始则只允许它执行到结束。暂停的队列不能靠旧控制代次继续执行。

例：Agent 计划 `click(输入框) → input_text(查询词) → press_key(Enter)`。在 input_text 进行中请求接管，完成这次文本输入后移交；Enter 不执行。不得把整段搜索工作流视为同一个 operation。

手机加入触发布局更新也须在当前输入 operation 的安全边界应用，避免在按下和释放之间更换坐标系。Host 在新 viewport 生效后拒绝旧 revision 输入；正常网页自身的动态布局仍可能发生。

观察/等待（截图、读取状态、等待元素或加载）与可写 operation 分开。只读等待不持有写操作执行权，不能阻止接管。任意 evaluate 不能自称只读；可写脚本不得绕过 Host 仲裁，脚本异步能力与完成契约未定义前不作为首版通用原子操作。

### 完成、失败与协议记录（建议）

Host typed operation record 保存 operation ID、Session/Tab、操作者、控制代次、文档/viewport revision、操作类型、生命周期和结构化错误；操作参数按类型校验，控制记录不进入网页 payload。执行失败允许已有部分页面影响，必须显式报告，不能自动回滚或重放点击/提交。

执行方确认 operation 正常结束或失败且已不再注入输入，并清理其按键/触点后，Host 才可移交。执行方失联、超时而是否仍在写入不明时保留等待/错误状态；不能将未知状态记作完成。相同 operation ID 的重发查询原结果，不再次执行；去重记录不可用时显式返回结果未知。

点击触发的 fetch、导航、计时器可以在操作结束和人工接管后继续运行，这是网页正常行为。operation 完成不等于业务成功；暂停 Agent 仅禁止后续 Agent 操作，不冻结网页。

## 暂不纳入首版

HEVC 优化、零拷贝、simulcast、多码率、Linux 客户端交付、运行态跨 Host 迁移、完整通用浏览器兼容性承诺。视频网页播放不是远程页面视频传输，两者独立验收。

# Native 内容区与抽屉补链设计

状态：`DESIGN_READY` / `NOT_IMPLEMENTED`。产品 source 零 diff；本文件只做设计，不宣称已安装、已连网或已通过黑盒。

设计 SHA：`d0a88eada5748bbb22f20e7ff48b67a282b3abb0`
设计 tree：`57fda4c681ce91b08784679f3c54bc1b024e137b`
分支：`codex/m1-native-chrome-layout-design-r1-20261003`
输入：`origin/main bc9d67ad037be2c7119bee692f9ad362417ffd05`，组合 W6、Chrome、epoch、Android 准确输入、Mac264eb92 产品增量。

## 1. 首次偏离与唯一 owner

稳定 Android 证据：
`/Volumes/Intel/playground/agentbrowser/m1-android-launcher-baseline-r1-20261003/docs/runs/m1-android-launcher-baseline-r1-20261003/evidence/01-launch-window.xml`
`.../evidence/02-drawer-window.xml`

- 窗口：`[0,0][1080,2400]`。
- 原生 stage：`[0,63][1080,1366]`，视频 clip：`[173,63][906,1366]`。
- WebView：`[0,1366][1080,2337]`，即被原有 `LinearLayout` 当成 370dp 面板使用。
- 浏览器工具栏：`[0,1366][1080,1631]`。
- 抽屉：`Dialog browser-drawer [84,1366][1080,2337]`，只覆盖 WebView 面板，不覆盖应用窗口；`02-drawer-window.xml` 中标签页/Profile/质量/诊断所有节都掉到 `bottom=2337`，视觉上被截断在 WebView 底部。

首次偏离点：`apps/android/app/src/main/java/com/agentbrowser/probe/MainActivity.java` 的 `layoutChrome()`（当前基线约 L136-141）。该函数按 `networkSelected` 把 WebView 设为 `370dp` 或 `136dp` 面板，并让 stage 占据“剩余区域”。这是 native 容器尺寸决策，不是 CSS 可修复或 WebView 内测量可修复的问题。

唯一 owner：

| 对象 | Owner | 现状 |
| --- | --- | --- |
| 应用窗口、系统 insets、IME 遮挡、内容整流矩形 | AB-04 `apps/android/` / `apps/macos/` | `MainActivity.layout={stage,webView}` 及 Mac `NSSplitViewController` 是宿主布局 owner |
| WebView 在应用窗口内的容器几何 | AB-04 原生容器；UI 只通过 typed intent 表达抽屉态 | 当前 WebView 被设为持久面板 |
| 抽屉是否打开、焦点和内容状态 | AB-02/03 UI & client-domain | `main.tsx` 的 `drawerOpen` 是本地状态 |
| 远端 browser viewport | Host 唯一；客户端只按已有 `ViewportDeclaration.css_width/css_height` 申报本地内容区 | 当前 `reportViewport()` 从 stage 申报 CSS px |
| 指针/caret 显示变换 | 原生 consumer：Android `videoClip` 逆变换，Mac `pageCoordinates`/`viewPoint` 共用同一变换 | 实现已存在，本设计不改坐标语义 |

当前 WebView hosted mode 不只是“抽屉单页太小”，它把浏览器 chrome 与页面投影写成了同一 flex 容器。正确边界是：原生页面投影在 stage，chrome WebView 在浏览态只占顶部，抽屉展开时同一 WebView 作为全窗 overlay。

## 2. 最小唯一布局方案

采用“薄 chrome + 全窗 overlay 抽屉”：

1. Android / Mac 应用根容器由原生 owner 改为单一 `FrameLayout` / 单一 content view。
2. 原生视频 stage 占据“内容区”：窗口去掉系统 bars/insets，再去掉浏览态 chrome 高度。
3. 同一 WebView 是 UI 载体：
   - 浏览态：WebView 按原生给定的 `ChromeRect` 布局，只承载工具栏/根页面，content 区域保持透明/空态并禁用页面输入。
   - 抽屉态：UI 发出 `presentation` typed intent，原生把 WebView 临时扩展到整个 `WindowRect`（主线程布局，不发 reload），CSS 抽屉用 fixed 覆盖全窗并带 backdrop。
   - 抽屉关闭：UI 发 `presentation` 关闭，WebView 回 `ChromeRect`，stage 恢复全内容显示，远端 viewport 不变。
4. `reportViewport()` 只在内容区（`StageRect`）变化时申报；抽屉开/关、backdrop、WebView 临时扩展、地址栏本地的 IME 都不申报远端 viewport。
5. 输入路由由 presentation 状态决定：
   - 浏览态：网页输入走原生 Surface/MediaCodec/RemoteIme/NSTextInputClient 的既有路径；WebView 不承载页面点击/IME。
   - 地址栏 focus 是本地 chrome 输入，与其关联的 IME 只进 `browser-address`，不进入远端编辑。
   - 抽屉打开：整体是本地 UI layer，所有 touch/mouse 由 WebView layer 消费，不转换为远端 DOM 点击、click、scroll 或编辑事件。
   - 抽屉关闭：焦点回到此前远端编辑上下文或地址栏真实上下文，不清空 Host attachment。

该方案不新增任意 JS hit-test、不转发网页点击、不复制 WebView 第二输入框，也不改变 Host wire。

## 3. typed 布局控制接口

新增最小 UI 控制面，冻结如下（实现随设计 PASS 后进入 `client-domain/probe.ts` 与原生接收边界）：

```ts
// UI -> native
type NativePresentationIntent =
  | { op: 'set_presentation'; state: 'browsing' | 'overlay' }

// snapshot projection（只读，不用于控制真源）
// 新字段仅进入 parseSnapshot 类型校验，不是第二 owner：
// windowRect: {x,y,width,height}      // 应用窗口像素/point 本地坐标
// contentRect: {x,y,width,height}     // 浏览态远端/空态内容区本地坐标
// chromeRect: {x,y,width,height}      // 浏览态 WebView 容器本地坐标
// presentation: 'browsing' | 'overlay'
```

约束：

- 单位：Android 使用逻辑像素（px = dp × density；CSS px 用于 `ViewportDeclaration`），Mac 使用 AppKit points；`ViewportDeclaration.css_width/css_height` 继续由平台按同一 CSS px 语义申报。
- 唯一真源：`windowRect/contentRect/chromeRect` 来自原生布局 owner；`presentation` 的当前值只由 UI 发出的 typed intent 返回结果确认，不从 UI 的 `data-drawer-open`、日志、DOM `getBoundingClientRect` 或 status payload 推断。
- Android/Mac 接收边界：命令只处理 `set_presentation`，未知字段拒绝；`overlay` 只扩展 WebView 容器，不改变 stage 尺寸、不 `requestApplyInsets`、不触发 `reportViewport`。
- 不消费 profile ABI；不扩展 Obscura wire；不改变原 input/edit/operation 语义。

## 4. 需要到达的实际几何预期

以 Android 1080×2400 稳定证据为示例：

- 系统 insets 顶部约 63px（状态栏）。
- 浏览态 chrome 为薄栏：窄屏双行约 216px（含导航/地址/菜单，可再按实现固定 min-height）；stage 内容区应接近 `[0,63][1080,?]`，底部不再留 370dp 空面板。
- 抽屉态 backdrop + drawer 应覆盖 `[0,0][1080,2400]`，drawer 右宽最大约 `min(92vw,390dp)`。
- 不连接时也有现代浏览器首屏：顶栏 + 内容区空态；不能把 `empty-state` 当成第二页面输入框。

此数字是设计前的期望形态，实际产品验证以 `blackbox-plan.md` 的矩形断言为准。

## 5. 与既有图关系

新增 `dagpipe/graphs/m1-native-chrome-layout.graph.json`，独立一个业务对象流：布局意图 → 内容矩形 + presentation → 输入路由 → 资源释放。它复用已有 m1-chrome-request、m1-chrome-lifecycle、m1-navigation、m1-viewport、m1-pointer、m1-editing、m1-focus。图已在 `dagpipe graph validate` 校验通过。

## 6. 禁止与边界

- 不重写 Cordis 组件、样式主题、浏览器 wire。
- 不把视频、IME、gesture 转成 WebView 事件模拟。
- 不为 drawer 或 local chrome 修改 Host viewport；不因抽屉开关 reload。
- 不改 `NetworkSession` 的 typed command 和输入 lease。
- 不在本次触碰 Profile、首次连接、Media、launcher 作者的产品文件；`MainActivity`/`NetworkSession` 后续由主控串行交接。

## 7. 剩余能力

- 未安装/未启动浏览器或 app。
- 未跑 Rust/V8/Media 构建；只读源码和稳定 launcher XML/PNG。
- 真机/runtime window 黑盒留给控制器后续授权。
- 本 run 只产出设计和独立 consumer 设计计划，不代替产品编码或架构 review。

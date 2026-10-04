# Native 内容区与抽屉补链设计（R2）

状态：`DESIGN_READY` / `NOT_IMPLEMENTED`。产品 source 零 diff；本文件只做设计，不宣称已安装、已连网或已通过黑盒。

分支：`codex/m1-native-chrome-layout-design-r2-20261003`
输入：`origin/main bc9d67ad037be2c7119bee692f9ad362417ffd05`，冻结树 `d11150fdad7b39daab895b69f9b269abb0b70144` / tree `9cba13e4c4d16c22cea2d5f2afc970a24f26f472`。产品准确来源已继承，不重复 apply。
review 输入：`m1-native-chrome-layout-design-20261003-r1`，三项 P1。

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

### 1.1 R1 三个 P1 的唯一偏离点与裁决

| R1 P1 | 唯一偏离点 | R2 裁决 |
| --- | --- | --- |
| IME 呈现/申报矛盾 | `MainActivity.reportViewport()`（L307-313）从 `stage.getWidth()/getHeight()` 申报；`stage` 尺寸来自含 `ime()` 的 root padding（L54-58），R1 同时要求“按 stage 变化申报”与“IME 不申报”，两条规则不可能同时满足 | 拆成 `AdvertisedViewportRect` 与 `PresentedContentRect` 两个独立矩形：申报矩形由窗口 metrics + system bars + chrome 派生，排除 IME；呈现矩形才包含 IME。申报触发集冻结，见 §2 |
| 空态缺渲染 owner | `main.tsx` 空态在 `.viewer`（L396-403），R1 把该 WebView 限定到 `ChromeRect`，而 `MainActivity.stage` 无空态渲染器 | 空态复用同一 WebView：连接且具备正式原生呈现状态前 WebView 覆盖全应用范围（含顶部 chrome）；具备状态后 WebView 缩到 `ChromeRect`，原生视频填 `ContentRect`。见 §3 |
| 失败取消释放不可验 | R1 只有“返回 `{rejection}`”，图末端只有泛化 `layout-lifecycle-release`，没有触发、资源清单、result consumer | 冻结 intent `id`、`rejection.reason`、资源清单、唯一释放 owner 与唯一出口；所有结果落到既有 SESE 图末端 `layout-resource-terminal-result`。见 §4、§5 |

### 1.2 唯一 owner

| 对象 | Owner | 现状 |
| --- | --- | --- |
| 应用窗口、窗口 metrics、系统 bars insets、IME insets、内容区 | AB-04 `apps/android/` / `apps/macos` | `MainActivity.layout={stage,webView}` 与 Mac `NSSplitViewController` 是宿主布局 owner |
| `AdvertisedViewportRect` / `PresentedContentRect` / `ChromeRect` / `WebRect` 计算与应用 | AB-04 原生布局 owner | R2 新增计算，当前无实现 |
| WebView 在应用窗口内的容器几何 | AB-04 原生容器；UI 只发 typed intent | 当前 WebView 是持久面板 |
| 抽屉是否打开、焦点和内容状态 | AB-02/03 UI & client-domain | `main.tsx` 的 `drawerOpen` 是本地状态 |
| 远端 browser viewport | Host 唯一；客户端只按 `ViewportDeclaration.css_width/css_height` 申报 | 当前 `reportViewport()` 从 stage 申报 |
| 指针 / caret 显示变换 | 原生 consumer：Android `videoClip` 逆变换，Mac `pageCoordinates`/`viewPoint` | 实现已存在，本设计不改坐标语义 |
| 未连接空态渲染 | UI（`main.tsx` `.viewer-empty`）承载；AB-04 只负责给 WebView 正确容器矩形 | 内容已在 `.viewer`，缺容器 owner 决策 |
| 布局资源释放与结果消费 | AB-04 原生 owner 唯一释放；result consumer 为意图发起方 | R2 新增契约，当前无实现 |

当前 WebView hosted mode 不只是“抽屉单页太小”，它把浏览器 chrome 与页面投影写成了同一 flex 容器。正确边界是：原生页面投影在 stage，chrome WebView 在浏览态只占顶部，抽屉展开时同一 WebView 作为全窗 overlay。

## 2. 最小唯一布局方案

采用“薄 chrome + 全窗 overlay 抽屉”：

1. Android / Mac 应用根容器由原生 owner 改为单一 `FrameLayout` / 单一 content view。
2. 原生视频 stage 占据 `ContentRect`：窗口去掉系统 bars，再去掉浏览态 chrome 高度。
3. 同一 WebView 是 UI 载体：
   - 浏览态（已具备正式原生呈现状态）：WebView 按原生给定的 `ChromeRect` 布局，只承载工具栏/根页面。
   - 抽屉态：UI 发出 `presentation` typed intent，原生把 WebView 临时扩展到整个 `WindowFrameRect`（主线程布局，不发 reload），CSS 抽屉用 fixed 覆盖全窗并带 backdrop。
   - 抽屉关闭：UI 发 `presentation` 关闭，WebView 回 `ChromeRect`，stage 恢复全内容显示，远端 viewport 不变。
4. `reportViewport()` 只在 `AdvertisedViewportRect` 变化时申报；抽屉开/关、backdrop、WebView 临时扩展、local 地址栏 IME、remote 编辑 IME 都不申报远端 viewport。
5. 输入路由由 presentation 状态决定：
   - 浏览态：网页输入走原生 Surface/MediaCodec/RemoteIme/NSTextInputClient 的既有路径；WebView 不承载页面点击/IME。
   - 地址栏 focus 是本地 chrome 输入，与其关联的 IME 只进 `browser-address`，不进入远端编辑。
   - 抽屉打开：整体是本地 UI layer，所有 touch/mouse 由 WebView layer 消费，不转换为远端 DOM 点击、click、scroll 或编辑事件。
   - 抽屉关闭：焦点回到此前远端编辑上下文或地址栏真实上下文，不清空 Host attachment。

该方案不新增任意 JS hit-test、不转发网页点击、不复制 WebView 第二输入框，也不改变 Host wire。

## 3. 四矩形 typed geometry 契约

typing 是本地 geometry 真源，和 Host viewport owner 严格分开；四个矩形不互相推导，各自由原生布局 owner 从系统真源测量。实现随设计 PASS 后进入 `client-domain/probe.ts`、`MainActivity.java` 与 `main.swift` 的对应接收边界。

```ts
type Rect = { x: number; y: number; width: number; height: number };

// UI -> native，唯一布局意图
type NativePresentationIntent =
  | { id: string; op: 'set_presentation'; state: 'browsing' | 'overlay' };

// snapshot projection（只读，不是控制真源）
type NativeLayoutProjection = {
  windowFrameRect: Rect;      // 应用窗口内容帧，不含系统 bars
  advertisedViewportRect: Rect; // 申报给 Host 的远端 viewport 矩形（排除 IME）
  presentedContentRect: Rect;   // 当前实际呈现内容区（含 IME 压缩）
  chromeRect: Rect;             // 浏览态 WebView 容器矩形
  webRect: Rect;                // 当前 WebView 实际容器矩形（浏览态 = chromeRect；全应用态 = windowFrameRect；overlay = windowFrameRect）
  visibleContent: 'webview' | 'native-video'; // 当前内容区由谁可见
  presentation: 'browsing' | 'overlay';
  revision: number;           // 本地布局 revision，每次几何应用成功自增
};
```

约束：

- **单位**：Android 一律用逻辑像素 dp（px = dp × density）；Mac 一律用 AppKit points。CSS px 只用于 `ViewportDeclaration.css_width/css_height`，由平台在同一语义下换算，不混入本地矩形。
- **四条测量边**：
  - `windowFrameRect`：Android 由 `WindowManager.getCurrentWindowMetrics().getBounds()` 减去 system bars insets 得到（`WindowMetrics`，minSdk 30 可用）；Mac 由 `window.contentView!.bounds` 得到。
  - `advertisedViewportRect`：Android 由 `windowFrameRect` 减去薄 chrome 高度得到，**不含 IME**；Mac 由 content bounds 减去 chrome 高度得到。
  - `presentedContentRect`：Android 由 `windowFrameRect` 减去薄 chrome 高度再按当前 IME inset 收缩得到（会随 IME 变化）；Mac 无软键盘 IME，等于 `advertisedViewportRect`。
  - `chromeRect`：薄 chrome 的固定高度条带，横屏换向，见 §4。
- **唯一真源**：四个矩形全部来自原生布局 owner；`presentation` 当前值只由 `set_presentation` 的 applied 结果确认；`revision` 只由原生自增。不得从 UI 的 `data-drawer-open`、日志、DOM `getBoundingClientRect`、首帧或 status payload 反推。
- **不得从 stage 当前尺寸反推申报矩形**：申报用 `advertisedViewportRect`，呈现用 `presentedContentRect`；两者物理隔离，`reportViewport()` 禁止再读 `stage.getWidth()/getHeight()`。
- **接收边界**：命令只处理 `set_presentation`，未知字段拒绝；`overlay` 只扩展 `webRect` 到 `windowFrameRect`，不改 `stage` 尺寸、不 `requestApplyInsets`、不 `reportViewport`。
- 不消费 profile ABI；不扩展 Obscura wire；不改变原 input/edit/operation 语义（`NetworkSession.declareViewport` 签名不变，只换调用来源）。

## 4. viewport 与 IME 唯一策略（P1 #1）

### 4.1 AdvertisedViewportRect 的稳定输入集

`reportViewport()` 只在这三种输入变化时触发一次，其余事件不触发：

1. **旋转 / 物理窗口尺寸变化**：Android `onConfigurationChanged` 重测 `windowFrameRect`；Mac window resize。这是唯一的 resize 触发，且直接进入 Host 既有 `ViewportDeclaration` 路径发布新 revision。
2. **系统 bars 变化**（导航栏模式、折叠屏分屏、多窗口）：`windowFrameRect` 变，重测。
3. **薄 chrome 高度变化**：横竖屏切换导致 chrome 布局换向（§4.3），`chromeRect` 高度变，重测。

**明确不触发**：local 地址栏 IME 开/闭、remote 编辑 IME 开/闭、drawer 开/关、backdrop、WebView 临时扩展、`presentation` 切换、`visibleContent` 切换、连接/断连。这些只改变 `presentedContentRect` 与 `webRect`，不改变 `advertisedViewportRect`，不产生新 revision。

### 4.2 PresentedContentRect 与等比 fit

- 原生等比 fit 使用 `presentedContentRect`：Android `videoClip` 按 `presentedContentRect` 尺寸做 `min(w/visibleWidth, h/visibleHeight)` 缩放并居中；Mac `VideoSurfaceView.bounds` 即呈现区。留边（letterbox）不得点击，沿用既有逆变换越界拒绝（`MainActivity.networkTouch` L191-195、Mac `pageCoordinates` L690 bounds 检查）。
- IME 开闭时：`presentedContentRect` 变化 → `videoClip` 重新 fit 同一远端 buffer → 本地 transform 与 caret/选区/composition 同步更新（Mac `viewPoint` L696 与 `firstRect` L1404 共用同一 transform）。远端 buffer、Host revision 与 document 都不变。
- frame/transform/caret/输入共用同一个 `presentedContentRect`：不得为 caret 另算一套矩形，不得为 IME 伪造 Host 配置。

### 4.3 Android 根窗口与 chrome 高度的具体实现约束

- **`android:windowSoftInputMode="adjustResize"`**：AndroidManifest 的 activity 必须显式声明 `adjustResize`（当前缺失），否则 IME 走 `adjustPan`，`presentedContentRect` 无法反映真实遮挡。
- **root padding 只扣除 system bars，不扣 IME**：`layout.setOnApplyWindowInsetsListener`（MainActivity L54-58）当前用 `systemBars() | ime()` 设置 padding，导致 stage 尺寸随 IME 变。改为：根 padding 只扣 `systemBars()`；IME inset 单独读取并只参与 `presentedContentRect` 计算，不参与 `advertisedViewportRect`。
- **申报矩形只用 `WindowMetrics` + system bars + 薄 chrome**：`advertisedViewportRect` 从 `WindowManager.getCurrentWindowMetrics().getBounds()`（`WindowMetrics`，API 30+）减 system bars 减 chrome 高度得出；禁止从 `stage` 当前尺寸反推。
- **薄 chrome 高度**：UI 通过 `data-chrome-height`（或等价 typed 投影）申报实际 CSS px 高度，原生换算成 dp/point 后写 `chromeRect`；横竖屏若换向则重算并触发申报。
- **Mac points 对应关系**：Mac 无软键盘 IME；`advertisedViewportRect == presentedContentRect`（均以 AppKit points 计）。`updateViewport()`（main.swift L1523-1530）当前用 `videoView.bounds * backingScaleFactor`，必须改为：以 `advertisedViewportRect` points 计算 CSS px（不再乘 `backingScaleFactor`，CSS px 与 points 1:1），断连后无 `advertisedViewportRect` 时不申报。

### 4.4 冻结的真值断言（取代 R1 的条件断言）

- local 地址栏 IME 开→关：`viewportRevision` 前后**相同**，Host document 不变，`presentation` 不变，本地 `revision` 与本地 transform 变化。
- remote 编辑 IME 开→关：同上，且 `EditorContext`、selection、composition 不因 IME 开闭重发。
- 旋转：`viewportRevision` **必须变化一次**（唯一 resize 触发），document/session/control 不变。
- drawer 开→关：`viewportRevision` 前后**相同**，drawer 覆盖 `[0,0][1080,2400]`。

真值断言在 `blackbox-plan.md` 以绑定 revision 的矩形断言落地，不得回退为“若同意方案 / 或走 Host 声明”。

## 5. 未连接空态渲染 owner（P1 #2）

### 5.1 单一 WebView 双容器

复用同一 Cordis viewer，在同一 WebView 内渲染全应用内容范围，不新增 native 第二空态、不新增第二网页输入框：

| 阶段 | WebView 容器 | 内容区可见者 | UI 表现 |
| --- | --- | --- | --- |
| 未连接 / 未具备正式原生呈现状态 | `webRect = windowFrameRect`（覆盖全应用，含顶部 chrome） | WebView（`.viewer-empty` 在内容区居中） | 顶栏 + 内容区空态，二者同一 WebView |
| 已连接且具备正式原生呈现状态 | `webRect = chromeRect` | 原生视频（填 `ContentRect`） | 顶栏 WebView，视频填内容区 |
| drawer overlay | `webRect = windowFrameRect`（临时） | WebView overlay | 全窗 backdrop + drawer |

- **内容区 owner**：`main.tsx` 的 `.viewer`/`.viewer-empty`（L396-403、CSS L88-100）继续是唯一空态渲染者；R2 只改它的容器来源——浏览态窄容器由原生给 `chromeRect`，全应用态由原生给 `windowFrameRect`。空态不再依赖 `.viewer` 假定自己占满内容区。
- **`.viewer` 职责收敛**：浏览态 `.viewer` 只保留为本地 chrome 下的透明占位（`pointer-events:none`，已被 CSS L95 覆盖），不再承担远端页面投影；远端投影在 native stage。

### 5.2 具不具备原生呈现状态的 typed 判定

`visibleContent` 由原生 owner 依 typed 状态给出，禁止从 firstframe/日志推断 control 权：

```
visibleContent = (connectionState == 'connected'
                  && inputReady
                  && displayed.frame.session_id == host.session_id
                  && !host.viewport_pending) ? 'native-video' : 'webview'
```

- 数据来自既有 `NetworkSession.inputReady()`（L42-45，已校验 requestedViewport==submittedViewport、hostReady、displayed document/viewport 与 host 一致）与 `NativeConnection`/Mac `input_ready`（main.rs L1269-1282）。不新增第二套判定。
- `displayed` 或 `host` 缺失（未连接、首帧未到、viewport pending、operation running）→ `visibleContent='webview'`，WebView 覆盖全应用，空态可见。
- 转入：`connected && inputReady` 首次为真 → `webRect` 收窄到 `chromeRect`，原生 stage 显示。退出：`connectionState` 变 `stopping`/`stopped`/`error` 或 `inputReady` 转假 → `webRect` 回 `windowFrameRect`。
- 网络中断：`state != 'connected'` → `visibleContent='webview'`，WebView 回全应用态，空态 + 错误提示可见；不清空 profile/session。

### 5.3 组合与冲突规则

| connection state | 是否 `inputReady` | presentation | `webRect` | 内容区可见 |
| --- | --- | --- | --- | --- |
| disconnected / idle / starting | - | browsing | windowFrameRect | WebView 空态 |
| connected | false | browsing | windowFrameRect | WebView（等待最新画面/空态） |
| connected | true | browsing | chromeRect | 原生视频 |
| connected | true | overlay | windowFrameRect | WebView overlay |
| connected | false | overlay | windowFrameRect | WebView overlay |
| stopping / error | - | overlay | windowFrameRect | WebView overlay（先关 overlay 再回落） |

规则：overlay 优先于 `visibleContent`；overlay 关闭后回到 `visibleContent` 决定的行。不得用 `firstframe`、DOM `getBoundingClientRect` 或日志判定控制权。

### 5.4 输入路由 owner

- WebView 全应用态（含空态）与 overlay 态：touch/mouse 由 WebView layer 消费，**不**转发远端 pointer/click/scroll/edit。
- 浏览态且 `visibleContent='native-video'`：touch/mouse 仅走 native video → 既有 `networkTouch`（L178-253）/ Mac `mouseDown/Dragged/Up/scrollWheel`（L574-673），WebView `pointer-events:none`（CSS L95）。
- local 地址栏 IME 只进 `browser-address`；remote 编辑 IME 由 `RemoteImeView`（L45-70）/ Mac `NSTextInputClient` 承载，两者互不串输入。

## 6. overlay 生命周期与资源释放（P1 #3）

### 6.1 一一闭环的 typed 结果

`set_presentation` 是同步 typed 命令，UI 发 intent 后**只在 native 确认 applied 几何后才显示有效状态**：

```ts
type PresentationResult =
  | { id: string; op: 'set_presentation'; status: 'applied'; geometry: { webRect: Rect; revision: number } }
  | { id: string; op: 'set_presentation'; status: 'rejected'; reason: 'NO_AUTHORITY' | 'ACTIVITY_NOT_RESUMED' | 'GEOMETRY_UNAVAILABLE' };
```

闭环路径（全部落到既有 SESE 图唯一出口 `layout-resource-terminal-result`）：

| 事件 | 生产者 | 校验 | 结果 | consumer |
| --- | --- | --- | --- | --- |
| UI 打开 drawer | UI | - | 发 `set_presentation({id, state:'overlay'})` | native |
| native 应用成功 | AB-04 | `webRect == windowFrameRect` 且主线程 | `applied` + 新 `revision` | UI 显示有效 overlay 状态 |
| native 拒绝 | AB-04 | 无 authority / Activity 未 resumed / 几何不可用 | `rejected` + reason | UI 不显示假成功；drawer 视觉回退；不误点远端 |
| 系统 Back | UI | overlay 打开 | 先 `set_presentation(browsing)`，再关 drawer | drawer 关闭，app 留前台 |
| drawer close / outside | UI | - | `set_presentation(browsing)` | WebView 回 `chromeRect` |
| 旋转 | OS | drawer 打开 | 保留 overlay，`webRect` 重算为新 `windowFrameRect` | 输入恢复，caret 用新 transform |
| Activity teardown / UI 卸载 | OS/UI | - | 唯一释放 owner 执行，见 §6.2 | `layout-resource-terminal-result` |

### 6.2 资源清单、唯一释放 owner 与触发

所有本地布局资源由 AB-04 原生 owner 唯一持有与释放，永久资源不因 drawer close 重建：

| 资源 | 类别 | 创建 | 唯一释放触发 | 释放动作 |
| --- | --- | --- | --- | --- |
| `layout.setOnApplyWindowInsetsListener` | 永久（Activity 生命周期） | `onCreate` L54 | `onDestroy` | 移除 insets listener，不留 Activity 引用 |
| `stage.addOnLayoutChangeListener` | 永久 | `onCreate` L90 | `onDestroy` | 移除 layout-change listener |
| `webView` + JS bridge | 永久 | `onCreate` L99-105 | `onDestroy` L359-360 | `removeJavascriptInterface` + `destroy`（已存在） |
| `RemoteImeView` | 永久 | `onCreate` L68 | `onStop`（失焦）或 `onDestroy` | `deactivate()`（L65-70）取消候选、隐藏 IME、`clearFocus` |
| Mac viewport `Timer`（0.2s, repeats） | 永久 | main.swift L1502 | `applicationShouldTerminate` / window close | `invalidate()`，禁用时移除 RunLoop 引用 |
| `set_presentation` intent 处理 | 每次调用（非永久） | 命令到达 | 命令返回即结束 | 不留残留 listener；drawer close 不重建任何永久资源 |

- drawer close 只发一次 `set_presentation(browsing)` 并应用几何；**不**移除/重建 layout listener、webView、远程 IME 或 timer。
- 释放 owner 与 `layout-resource-terminal-result` 的 consumer 分离：owner 是 AB-04 原生，consumer 是 intent 发起方（UI 或 Activity）。

### 6.3 失败 / 取消 / 释放的终点语义

- `rejected`：无假成功、不误点远端。UI 收到 `rejected` 后不切换有效 overlay 状态；drawer 若已视觉打开需回退到关闭。reason 唯一，供黑盒断言。
- 取消（系统 Back）：先关 overlay 再退 drawer，保留 app 前台；不把 Back 转成远端导航或页面动作。
- 释放（teardown / UI 卸载）：由 §6.2 唯一 owner 在对应触发点执行；终点是 `layout-resource-terminal-result`，带 `status ∈ {applied, rejected, released}`。
- 这些路径全部复用既有 SESE 图，**不新增平行图、不新增回边**；成功/拒绝/取消/释放都在同一唯一出口表达（对齐 m1 既有图的“typed 结果唯一出口”约定）。

## 7. 需要到达的实际几何预期

以 Android 1080×2400 稳定证据为示例：

- 系统 bars insets 顶部约 63px（状态栏）。
- 浏览态 chrome 为薄栏：窄屏双行约 216px（含导航/地址/菜单，可再按实现固定 min-height）；`advertisedViewportRect` ≈ `[0,63][1080,2400-63-chromeH]`，底部不再留 370dp 空面板。
- 抽屉态 backdrop + drawer 应覆盖 `[0,0][1080,2400]`，drawer 右宽最大约 `min(92vw,390dp)`。
- 未连接时：`webRect = windowFrameRect`，顶栏 + 内容区空态在同一 WebView，不能把 `empty-state` 当成第二页面输入框。

此数字是设计前的期望形态，实际产品验证以 `blackbox-plan.md` 的矩形断言为准。

## 8. 与既有图关系

沿用既有 `dagpipe/graphs/m1-native-chrome-layout.graph.json`（单一业务对象流：布局意图 → 内容矩形 + presentation → 输入路由 → 资源释放），只补节点语义与末端结果类型，不新增第二套图。它复用已有 m1-chrome-request、m1-chrome-lifecycle、m1-navigation、m1-viewport、m1-pointer、m1-editing、m1-focus。图由 `dagpipe graph validate` 校验通过。

## 9. 禁止与边界

- 不重写 Cordis 组件、样式主题、浏览器 wire。
- 不把视频、IME、gesture 转成 WebView 事件模拟。
- 不为 drawer 或 local chrome 修改 Host viewport；不因抽屉开关 reload。
- 不从 stage 当前尺寸反推申报矩形；不从 `firstframe`/日志推断 control 权。
- 不改 `NetworkSession` 的 typed command 和输入 lease。
- 不在本次触碰 Profile、首次连接、Media、launcher 作者的产品文件；`MainActivity`/`NetworkSession` 后续由主控串行交接。

## 10. 后继产品范围（不属本次）

本节只声明能力归属，不属本设计 run 的实施范围：

- AB-04 Android `MainActivity.java` / `RemoteImeView.java`：四矩形测量、`adjustResize`、root padding 拆分、`set_presentation` 接收、资源释放。
- AB-04 Mac `main.swift` / `connection-bridge`：单 content view、points 申报、`set_presentation` 绑定。
- AB-02/03 UI/domain：`set_presentation` intent、`NativeLayoutProjection`、`.viewer` 容器来源、`PresentationResult` 消费。

## 11. 剩余能力

- 未安装/未启动浏览器或 app。
- 未跑 Rust/V8/Media 构建；只读源码和稳定 launcher XML/PNG。
- 真机/runtime window 黑盒留给控制器后续授权。
- 本 run 只产出设计和独立 consumer 设计计划，不代替产品编码或架构 review。

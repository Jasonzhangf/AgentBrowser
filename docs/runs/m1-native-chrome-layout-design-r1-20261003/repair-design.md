# Native 内容区与抽屉补链设计（R5）

状态：`DESIGN_READY` / `NOT_IMPLEMENTED`。产品 source 零 diff；本文件只做设计，不宣称已安装、已连网或已通过黑盒。

分支：`codex/m1-native-chrome-layout-design-r5-20261004`
输入：`origin/main bc9d67ad037be2c7119bee692f9ad362417ffd05`，原冻结树 `d11150fdad7b39daab895b69f9b269abb0b70144` / tree `9cba13e4c4d16c22cea2d5f2afc970a24f26f472`。
R5 从 exact r4 `0e387b36af08edacb97ad1b8ed0d28b0a0eaf5a6` / tree `0deb5dca5369c1339867960300b1bef1c362e2c6` 直接继续，不重复组合。
review 输入：`m1-native-chrome-layout-design-20261004-r4` 实际 failed/code_failure、review exit0/validJSON 一项 P1；不是 provider 错误，不重试旧候选取 PASS。

## 1. 首次偏离与唯一 owner

稳定 Android 证据：
`/Volumes/Intel/playground/agentbrowser/m1-android-launcher-baseline-r1-20261003/docs/runs/m1-android-launcher-baseline-r1-20261003/evidence/01-launch-window.xml`
`.../evidence/02-drawer-window.xml`

- 窗口：`[0,0][1080,2400]`。
- 原生 stage：`[0,63][1080,1366]`（display/root 坐标）；视频 clip：
  `[173,0][906,1303]`（相对 stage 的坐标；换算 root 后为
  `[173,63][906,1366]`）。这两个层级数值不能同时当作 root-local 值应用。
- WebView：`[0,1366][1080,2337]`，即被原有 `LinearLayout` 当成 370dp 面板使用。
- 浏览器工具栏：`[0,1366][1080,1631]`。
- 抽屉：`Dialog browser-drawer [84,1366][1080,2337]`，只覆盖 WebView 面板，不覆盖应用窗口；`02-drawer-window.xml` 中标签页/Profile/质量/诊断所有节都掉到 `bottom=2337`，视觉上被截断在 WebView 底部。

首次偏离点：`apps/android/app/src/main/java/com/agentbrowser/probe/MainActivity.java` 的 `layoutChrome()`（当前基线约 L136-141）。该函数按 `networkSelected` 把 WebView 设为 `370dp` 或 `136dp` 面板，并让 stage 占据“剩余区域”。这是 native 容器尺寸决策，不是 CSS 可修复或 WebView 内测量可修复的问题。

### 1.1 历史 P1 的唯一偏离点与裁决

| Review P1 | 唯一偏离点 | 裁决 |
| --- | --- | --- |
| IME 呈现/申报矛盾 | `MainActivity.reportViewport()`（L307-313）从 `stage.getWidth()/getHeight()` 申报；`stage` 尺寸来自含 `ime()` 的 root padding（L54-58），R1 同时要求“按 stage 变化申报”与“IME 不申报”，两条规则不可能同时满足 | 拆成 `AdvertisedViewportRect` 与 `PresentedContentRect` 两个独立矩形：申报矩形由窗口 metrics + system bars + chrome 派生，排除 IME；呈现矩形才包含 IME。申报触发集冻结，见 §2 |
| 空态缺渲染 owner | `main.tsx` 空态在 `.viewer`（L396-403），R1 把该 WebView 限定到 `chromeRect`，而 `MainActivity.stage` 无空态渲染器 | 空态复用同一 WebView：连接且具备正式原生呈现状态前 WebView 覆盖 `drawableWindowRect`（含顶部 chrome）；具备状态后 WebView 缩到 `chromeRect`，原生视频填 `presentedContentRect`。见 §3 |
| 失败取消释放不可验 | R1 只有“返回 `{rejection}`”，图末端只有泛化 `layout-lifecycle-release`，没有触发、资源清单、result consumer | 冻结 intent `id`、`rejection.reason`、资源清单、唯一释放 owner 与唯一出口；所有结果落到既有 SESE 图末端 `layout-resource-terminal-result`。见 §4、§5 |
| R3 几何应用矛盾 | R3 同时要求 edge-to-edge root、root `systemBars` padding，以及把已经扣过 bars 的 `safeContentRect`/`chromeRect`/`drawableWindowRect` 当 root 子 View 矩形应用 | 删除 root `systemBars` padding；`drawableWindowRect` 是 root local；只派生一次 `safeContentRect`；chrome/overlay 与 stage 的 frame 按各自直接父级的 root-local/可直接应用的 bounds 使用，不允许 padding-compensation 第二路径 |
| R4 父子坐标重复 | R4 要求 `stage` 与嵌套 `videoClip` 都使用 `presentedContentRect` 的 root-local left/top；实际层级是 `stage.addView(videoClip)`，Android child left/top 相对直接父容器，非零 top 会把 stage 偏移重复到 clip | 保留实际层级：只有 stage 的 frame 接收 `presentedContentRect` root-local position/dimensions；videoClip 接收 stage-local aspect-fit/center rect；SurfaceView 接收 clip-local coded-frame geometry/cropping；有效可见区域由 native parent transform 一次算出，pointer/caret/selection/composition 共用该 transform |

### 1.2 唯一 owner

| 对象 | Owner | 现状 |
| --- | --- | --- |
| 应用窗口、窗口 metrics、系统 bars insets、IME insets、内容区 | AB-04 `apps/android/` / `apps/macos` | `MainActivity.layout={stage,webView}` 与 Mac `NSSplitViewController` 是宿主布局 owner；stage/videoClip 的父子层级保持不变 |
| `AdvertisedViewportRect` / `PresentedContentRect` / `ChromeRect` / `WebRect` 计算与应用 | AB-04 原生布局 owner | R5 继续细化 parent-space transform，当前无实现 |
| WebView 在应用窗口内的容器几何 | AB-04 原生容器；UI 只发 typed intent | 当前 WebView 是持久面板 |
| 抽屉是否打开、焦点和内容状态 | AB-02/03 UI & client-domain | `main.tsx` 的 `drawerOpen` 是本地状态 |
| 远端 browser viewport | Host 唯一；客户端只按 `ViewportDeclaration.css_width/css_height` 申报 | 当前 `reportViewport()` 从 stage 申报 |
| 指针 / caret 显示变换 | 原生 consumer：Android `videoClip`/source 逆变换，Mac `pageCoordinates`/`viewPoint` | 实现已存在，本设计不改坐标语义，只显式冻结 parent chain |
| 空态渲染 | UI（`main.tsx` `.viewer-empty`）承载；AB-04 只负责给 WebView 正确容器矩形 | 内容已在 `.viewer`，缺容器 owner 决策 |
| 布局资源释放与结果消费 | AB-04 原生 owner 唯一释放；result consumer 为意图发起方 | R4 新增契约，当前无实现 |

当前 WebView hosted mode 不只是“抽屉单页太小”，它把浏览器 chrome 与页面投影写成了同一 flex 容器。正确边界是：原生页面投影在 stage，chrome WebView 在浏览态只占顶部，抽屉展开时同一 WebView 作为全窗 overlay。

## 2. 最小唯一布局方案

采用“薄 chrome + 全窗 overlay 抽屉”：

1. Android / Mac 应用根容器由原生 owner 改为单一 `FrameLayout` / 单一 content view；Android root 不设置任何 `systemBars` padding，系统 bars 只在派生矩形时计算一次。
2. 原生视频 stage 是 root 直接子 View，接收 `presentedContentRect` 的
   root-local position/dimensions；不得先 padding root 再二次应用已扣 bars 的
   矩形。嵌套 `videoClip` 不接收 root-local x/y，只接收 stage-local aspect-fit/
   center rect。
3. 同一 WebView 是 UI 载体：
   - 浏览态（已具备正式原生呈现状态）：WebView 按原生给定的 `ChromeRect` 布局，只承载工具栏/根页面。
   - 抽屉态：UI 发出 `presentation` typed intent，原生把 WebView 临时扩展到整个 `drawableWindowRect`（主线程布局，不发 reload），CSS 抽屉用 fixed 覆盖全窗并带 backdrop。
   - 抽屉关闭：UI 发 `presentation` 关闭，WebView 回 `ChromeRect`，stage 恢复全内容显示，远端 viewport 不变。
4. `reportViewport()` 只在 `AdvertisedViewportRect` 变化时申报；抽屉开/关、backdrop、WebView 临时扩展、local 地址栏 IME、remote 编辑 IME 都不申报远端 viewport。
5. 输入路由由 presentation 状态决定：
   - 浏览态：网页输入走原生 Surface/MediaCodec/RemoteIme/NSTextInputClient 的既有路径；WebView 不承载页面点击/IME。
   - 地址栏 focus 是本地 chrome 输入，与其关联的 IME 只进 `browser-address`，不进入远端编辑。
   - 抽屉打开：整体是本地 UI layer，所有 touch/mouse 由 WebView layer 消费，不转换为远端 DOM 点击、click、scroll 或编辑事件。
   - 抽屉关闭：焦点回到此前远端编辑上下文或地址栏真实上下文，不清空 Host attachment。

该方案不新增任意 JS hit-test、不转发网页点击、不复制 WebView 第二输入框，也不改变 Host wire。

## 3. typed geometry 契约

typing 是本地 geometry 真源，和 Host viewport owner 严格分开；`drawableWindowRect`/`safeContentRect` 由原生布局 owner 从系统真源测量，其余矩形只从这两个已冻结源按下方规则派生。实现随设计 PASS 后进入 `client-domain/probe.ts`、`MainActivity.java` 与 `main.swift` 的对应接收边界。

```ts
type Rect = { x: number; y: number; width: number; height: number };

// UI -> native，唯一布局意图
type NativePresentationIntent =
  | { id: string; op: 'set_presentation'; state: 'browsing' | 'overlay' };

// native 已实际呈现的 frame 身份；由原生显示 owner 在显示确认时写入
type NativeDisplayedFrameIdentity = {
  generation: number;
  sessionId: string;
  tabId: string;
  documentRevision: number;
  viewportRevision: number;
};

// snapshot projection（只读，不是控制真源）
type NativeLayoutProjection = {
  drawableWindowRect: Rect;      // root-local；SystemUI 实际可绘制 window/root bounds
  safeContentRect: Rect;         // root-local；drawableWindowRect 显式扣 systemBars insets 一次
  advertisedViewportRect: Rect;  // root-local；从 safeContentRect 派生并排除 IME，唯申报给 Host
  presentedContentRect: Rect;    // root-local；从 safeContentRect 派生，另扣当前 IME 有效重叠
  chromeRect: Rect;              // root-local；safeContentRect 内的薄 chrome 容器矩形
  webRect: Rect;                 // root-local；WebView 是 root 直接子 View
  visibleContent: 'webview' | 'native-video'; // 由 displayedFrame 与当前呈现身份匹配决定
  displayedFrame?: NativeDisplayedFrameIdentity; // native 已实际显示的 frame；缺失即无效
  currentTabId: string | null;   // 同一 Host 边界的当前 tab identity；不可得时为 null
  presentationViewport: Rect;    // root-local；产出该 displayed frame 时 stage 实际使用的 presentedContentRect
  geometryMatches: boolean;      // displayedFrame 匹配当前身份/effective viewport，且 stage 实际 frame == presentedContentRect
  presentation: 'browsing' | 'overlay';
  revision: number;           // 本地布局 revision，每次几何应用成功自增
};
```

约束：

- **单位与 origin**：typed projection 中的 Rect 均为 Android 逻辑 dp
  （px = dp × density）或 Mac AppKit points；Android 的 origin 是
  edge-to-edge window/root 本地 `(0,0)`，不混入 screenshot/display 原点。初始
  实现若无 effective window offset 能力，native 本地 origin 允许从 `(0,0)` 开始，
  但必须生成一致的 screenshot 映射证据并报告精确 offset，不能把未测量的全
  display 坐标当固定契约。**Rect 的 space/parent 不能只由数值推断**：下表是
  typed root-local 与 native derived parent-local 的唯一分层契约。
- **测量与派生边界**：
  - `drawableWindowRect`：Android 是单一 edge-to-edge root 的 drawable bounds，逻辑 dp，包含系统栏区域；root 不重复扣 `systemBars`。`WindowMetrics.getBounds()` 的物理 px 先减 window/root 坐标偏移，再按 density 转 dp。Mac 是 `window.contentView!.bounds`，AppKit points，不乘 `backingScaleFactor`。Android root 定义需保留当前 Activity 的实际 bounds 并加证据断言：若 window offset `(0,0)` 与 `drawableWindowRect` origin `(0,0)` 不相等，必须显式报告该差异；不得在没有 offset 能力时改用未量测全显示坐标。
  - `safeContentRect`：从 `drawableWindowRect` 显式扣 systemBars insets 一次，仍在同一 native 本地 origin/单位。
  - `advertisedViewportRect`：从 `safeContentRect` 派生并扣薄 chrome，**不含 IME**；Mac 无软键盘 IME，等于 `safeContentRect` 减 chrome。
  - `presentedContentRect`：从 `safeContentRect` 派生，另扣当前 IME 有效重叠并只扣一次，再扣 chrome；会随 IME 变化。不得从已经扣过 bar 的 root 再重复扣。
  - `chromeRect`：从 `safeContentRect` 派生的薄 chrome 条带，横屏换向，见 §4。
- **应用边界（按直接父级一次）**：`drawableWindowRect` 是 Android root 的完整
  本地坐标；root 不设置任何 padding。`safeContentRect` 是唯一一次从系统 bars
  派生得到的矩形。root 直接子 View（`stage`、`webView`）使用 root-local
  bounds；`stage` 的直接子 View 只使用 stage-local bounds；`videoClip` 的直接子
  View 只使用 clip-local bounds。不得把 root-local `stage` x/y 再加到
  `videoClip`，也不得把 `videoClip` 的实际 root bounds 冒充其 LayoutParams。
- **唯一真源**：几何全部来自原生布局 owner，两条系统测量源是 `drawableWindowRect`/`safeContentRect`，其余矩形只按同一 origin/单位派生；`presentation` 当前值只由 `set_presentation` 的 applied 结果确认；`revision` 只由原生自增。不得从 UI 的 `data-drawer-open`、日志、DOM `getBoundingClientRect`、首帧或 status payload 反推。
- **不得从 stage 当前尺寸反推申报矩形**：申报用 `advertisedViewportRect`，呈现用 `presentedContentRect`；两者物理隔离，`reportViewport()` 禁止再读 `stage.getWidth()/getHeight()`。
- **接收边界**：命令只处理 `set_presentation`，未知字段拒绝；`overlay` 只扩展 `webRect` 到 `drawableWindowRect`，不改 `stage` 尺寸、不 `requestApplyInsets`、不 `reportViewport`。WebView 是 root 直接子 View，因此它的 `webRect` 始终是 root-local；不得把 `chromeRect`/`drawableWindowRect` 转成 stage-local。
- **presentation 独立性**：`visibleContent` 只看 native 已实际完成的 displayed-frame 身份与当前 session/document/viewport/geometry 是否匹配；`inputReady`、`control_mode`、`commandPending`、`operation_running` 只决定 IME/写入资格，绝不决定视频可见。没有有效 displayed frame 或几何不匹配时才显示 `webview`/空态。
- 不消费 profile ABI；不扩展 Obscura wire；不改变原 input/edit/operation 语义（`NetworkSession.declareViewport` 签名不变，只换调用来源）。

### 3.1 每个 Rect 的 space、units 与 parent

| 对象 | space / units | parent / 用途 |
| --- | --- | --- |
| `drawableWindowRect`、`safeContentRect`、`advertisedViewportRect`、`presentedContentRect`、`chromeRect`、`webRect`、`presentationViewport` | Android root-local dp；Mac content points | typed projection；root 直接子 View 或 Host 申报只消费 root-local 值 |
| `stage` frame | root-local px（由 `presentedContentRect` dp × density 一次换算） | root 直接子 View；只接收 `presentedContentRect` 的 x/y/width/height |
| `videoClip` frame | stage-local px | stage 直接子 View；`scale = min(stageWidth/visibleSourceWidth, stageHeight/visibleSourceHeight)`，width/height 为 visible source 的 fit 尺寸，x/y 为 `(stageSize - fitSize)/2`；绝不加 stage 的 root-local x/y |
| `SurfaceView` frame | clip-local px | videoClip 直接子 View；使用 coded-frame 尺寸和同一个 `scale`，位置由 visible-source origin 决定；当前 Android frame contract 的 visible-source origin 为 `(0,0)`，coded padding 超出 clip 的部分由 `clipChildren` 裁掉且不可点击 |
| `RemoteImeView` | stage-local px | 实际父容器是 stage；1×1 focus anchor 保持 stage-local；任何 caret/candidate 位置通过 stage + fit clip + source transform 的 root/window 坐标一次计算，不把 root-local stage origin 塞进它的 LayoutParams |

有效可见图像 rect（root/window）是
`stage origin + videoClip fit origin + visible-source origin × scale`，由 native
parent chain 一次换算得到；它不是第二个 typed 控制真源。typed projection 只拥有
上表第一行的 root-local 容器/申报 Rect 与 displayed-frame 身份，fit clip、coded
SurfaceView 与有效可见图像 rect 都是 native owner 从这些真源和当前 frame
geometry 派生的结果。`presentedContentRect` 是可用容器，aspect fit 可留下
letterbox bands；bands 不属于可见图像，也不能被当作点击目标。

## 4. viewport 与 IME 唯一策略（P1 #1）

### 4.1 AdvertisedViewportRect 的稳定输入集

`reportViewport()` 只在这三种输入变化时触发一次，其余事件不触发：

1. **旋转 / 物理窗口尺寸变化**：Android `onConfigurationChanged` 重测 `drawableWindowRect`/`safeContentRect`/`advertisedViewportRect`；Mac window resize。这是唯一的 resize 触发，且直接进入 Host 既有 `ViewportDeclaration` 路径发布新 revision。
2. **系统 bars 变化**（导航栏模式、折叠屏分屏、多窗口）：`safeContentRect` 变，重测。
3. **薄 chrome 高度变化**：横竖屏切换导致 chrome 布局换向（§4.3），`chromeRect` 高度变，重测。

**明确不触发**：local 地址栏 IME 开/闭、remote 编辑 IME 开/闭、drawer 开/关、backdrop、WebView 临时扩展、`presentation` 切换、`visibleContent` 切换、连接/断连。这些只改变 `presentedContentRect` 与 `webRect`，不改变 `advertisedViewportRect`，不产生新 revision。

### 4.2 PresentedContentRect 与等比 fit

- 原生等比 fit 使用 `presentedContentRect`：Android 先把
  `presentedContentRect` 的 root-local dp 转成 px，只给 **stage** 设置显式
  left/top/width/height；stage 的直接子 `videoClip` 使用 stage-local
  `min(stageWidth/visibleWidth, stageHeight/visibleHeight)` 的 fit 尺寸和居中
  x/y；SurfaceView 使用同一个 `scale` 的 coded-frame 尺寸与 clip-local source
  origin。Mac `VideoSurfaceView.frame` 即 `presentedContentRect` 的 AppKit
  points，并作为其 content view 的局部 fit bounds。留边（letterbox）不得点击，
  沿用既有逆变换越界拒绝（Android `networkTouch` 的 clip-local 坐标映射、Mac
  `pageCoordinates` L690 bounds 检查）。
- IME 开闭时：`presentedContentRect` 变化 → stage frame 变化 → `videoClip` 在
  新 stage bounds 内重新 fit 同一远端 buffer → 本地 transform 与 caret/选区/
  composition 同步更新。Android pointer 从 `SurfaceView` 事件坐标减去
  visible-source origin×scale，再除以同一个 `scale`；caret/candidate 的
  root/window 位置用 `stage origin + videoClip fit origin +
  visible-source origin×scale` 一次换算。Mac `viewPoint` L696 与 `firstRect`
  L1404 继续共用同一 transform。远端 buffer、Host revision 与 document 都不变。
- frame/transform/caret/输入共用同一个 native parent chain：不得为 caret 另算
  一套 root-local 矩形，不得把 `videoClip` 当作 root 直接子 View，不得为 IME
  伪造 Host 配置。

### 4.3 Android 根窗口与 chrome 高度的具体实现约束

- **`android:windowSoftInputMode="adjustResize"`**：AndroidManifest 的 activity 必须显式声明 `adjustResize`（当前缺失），否则 IME 走 `adjustPan`，`presentedContentRect` 无法反映真实遮挡。
- **root 是单一 edge-to-edge 坐标且无 padding**：Android 采用一个
  edge-to-edge root，逻辑 dp 原点 `(0,0)`；root bounds 即
  `drawableWindowRect` 且 root 不设置任何 `systemBars` padding。
  `layout.setOnApplyWindowInsetsListener`（MainActivity L54-58）当前用
  `systemBars() | ime()` 设置 padding，这是缺陷来源；改为只读取 insets 供
  rect 派生，绝不调用 `setPadding`。`safeContentRect` 显式扣 bars 一次，
  `advertisedViewportRect`/`chromeRect`/`presentedContentRect`/`webRect` 从它
  派生；root 直接子 View 使用 root-local bounds，stage 的直接子 View 使用
  stage-local fit，SurfaceView 使用 clip-local coded/source geometry。
- **申报矩形只用 `WindowMetrics` + system bars + 薄 chrome**：`WindowMetrics.getBounds()` 物理 px 先减 window/root 坐标偏移，再按 density 转 dp，得到 `drawableWindowRect`；显式扣 system bars 一次得 `safeContentRect`，再扣 chrome 高度得 `advertisedViewportRect`；禁止从 `stage` 当前尺寸反推。
- **薄 chrome 高度**：UI 通过 `data-chrome-height`（或等价 typed 投影）申报实际 CSS px 高度，原生换算成 dp/point 后写 `chromeRect`；横竖屏若换向则重算并触发申报。
- **Mac points 对应关系**：Mac 无软键盘 IME；`advertisedViewportRect == presentedContentRect`（均以 AppKit points 计）。`updateViewport()`（main.swift L1523-1530）当前用 `videoView.bounds * backingScaleFactor`，必须改为：以 `advertisedViewportRect` points 计算 CSS px（不再乘 `backingScaleFactor`，CSS px 与 points 1:1），断连后无 `advertisedViewportRect` 时不申报，也不得从 `videoView.bounds` 或 display 像素反推。
- **每次活动/旋转/IME 同源**：每次活动生命周期、旋转或 IME 变化都重测这组
  Rect；pointer、caret、selection、composition 共用 stage-local fit clip 与
  clip-local coded/source transform 的同一 parent chain，不做第二套坐标。
  `advertisedViewportRect` 只在物理窗口/viewport 真变化时申报；IME、presentation
  与本地 chrome 操作不申报。
- **应用与 fit 同源**：Android stage 只使用 root-local
  `presentedContentRect`；videoClip 只使用 stage-local fit/center；SurfaceView
  只使用 clip-local coded/source geometry。pointer 不得从 stage 或 root 尺寸另算
  offset，caret 不得从 UI DOM 反算；Mac 的 `VideoSurfaceView.frame`、pointer 和
  caret 必须来自同一 content points/bounds 与 image fit transform。

### 4.4 冻结的真值断言（取代 R1 的条件断言）

- local 地址栏 IME 开→关：`viewportRevision` 前后**相同**，Host document 不变，`presentation` 不变，本地 `revision` 与本地 transform 变化。
- remote 编辑 IME 开→关：同上，且 `EditorContext`、selection、composition 不因 IME 开闭重发。
- 旋转：`viewportRevision` **必须变化一次**（唯一 resize 触发），document/session/control 不变。
- drawer 开→关：`viewportRevision` 前后**相同**；overlay 的实际 typed `webRect` 必须等于当前 `drawableWindowRect`/实际 app window 范围，drawer 内容避开 system bars。示例 `[0,0][1080,2400]` 只有在实际测量得到 window offset `(0,0)`、density 与 insets 后成立，不能当作未量测固定 PASS。

真值断言在 `blackbox-plan.md` 以绑定 revision 的矩形断言落地，不得回退为“若同意方案 / 或走 Host 声明”。

## 5. 未连接空态渲染 owner（P1 #2）

### 5.1 单一 WebView 双容器

复用同一 Cordis viewer，在同一 WebView 内渲染全应用内容范围，不新增 native 第二空态、不新增第二网页输入框：

| 阶段 | WebView 容器 | 内容区可见者 | UI 表现 |
| --- | --- | --- | --- |
| 未连接 / 无有效 displayed frame / 几何不匹配 / waiting | `webRect = drawableWindowRect`（覆盖全应用，含顶部 chrome） | WebView（`.viewer-empty` 在内容区居中） | 顶栏 + 内容区空态，二者同一 WebView |
| 已连接且具备正式原生呈现状态 | `webRect = chromeRect` | 原生视频（填 `presentedContentRect`） | 顶栏 WebView，视频填内容区 |
| drawer overlay | `webRect = drawableWindowRect`（临时） | WebView overlay | 全窗 backdrop + system-bar-safe drawer |

- **内容区 owner**：`main.tsx` 的 `.viewer`/`.viewer-empty`（L396-403、CSS L88-100）继续是唯一空态渲染者；R5 只改它的容器来源——浏览态窄容器由原生给 `chromeRect`，全应用态由原生给 `drawableWindowRect`。空态不再依赖 `.viewer` 假定自己占满内容区。
- **`.viewer` 职责收敛**：浏览态 `.viewer` 只保留为本地 chrome 下的透明占位（`pointer-events:none`，已被 CSS L95 覆盖），不再承担远端页面投影；远端投影在 native stage。
- **UI 消费修正（P1 #2）**：`main.tsx` 现在用 `data-ready={String(connected)}`，`probe.css` 在 `data-ready="true"` 时隐藏 `.viewer-surface`。后续最小修改必须把 ready/empty 投影绑定到 native presentation（有效 displayed frame + geometry match），而不是 `connected` 或 `inputReady`；`native-video` 状态时容器保持透明。按现有源码拆分，`connected` 仅控制网络相关 chrome，`canNavigate`/`canManageTabs` 可继续使用 `inputReady` 作为写权限，但不承载 viewer 可见性。

### 5.2 具不具备原生呈现状态的 typed 判定

`visibleContent` 由原生 owner 依已完成的 typed display identity 给出，禁止从首帧到达、日志或控件反推 control 权：

```
visibleContent = (displayedFrame != null
                  && !host.viewport_pending
                  && displayedFrame.generation == currentGeneration
                  && displayedFrame.sessionId == host.sessionId
                  && currentTabId != null
                  && displayedFrame.tabId == currentTabId
                  && displayedFrame.documentRevision == host.documentRevision
                  && displayedFrame.viewportRevision == host.viewportRevision
                  && presentationViewport == presentedContentRect) ? 'native-video' : 'webview'
```

- 数据来自 native 在“实际显示 frame 已确认”时写入的 identity：Android 既有 `NetworkSession.displayed`（L31/L305）及其 `displayedDocumentRevision`/`displayedViewportRevision`/coded/visible 尺寸（L216-219），Mac 既有 `session.displayed`（main.rs L997-1007）与 frame `generation/session_id/document_revision/viewport_revision`。`presentationViewport` 是同一显示确认路径实际使用的 native 本地矩形。
- `currentTabId`/`displayedFrame.tabId` 必须来自同一 Host/display owner 的 typed identity；当前只读状态边界尚未暴露独立 tab 字段，因此实现前必须先由该 owner 补上真实读取字段，或在无法取得时明确报告该能力缺口。不得用 `session_id`、DOM、URL 或 UI 标签页名称替代。
- 匹配边界排除 `requestedViewport`：不能拿本地刚发出的申报矩形当已生效值。Android 用 `submittedViewport`（Host ACK 后写入）对应的本地几何与 `host` document/viewport revision；Mac 用 `session.viewport`（declare 成功后写入）与 host document/viewport revision。
- `inputReady`/`control_mode`/`commandPending`/`operation_running` 只决定写入或 IME 激活，不进入上述可见性公式。observe、waiting、交还控制、command pending、Agent operation running 有有效 displayed frame 时仍必须是 `native-video`。
- `displayedFrame` 缺失、世代/session/document/viewport/几何任一不匹配，或 `host.viewport_pending` → `visibleContent='webview'`，WebView 覆盖全应用，空态可见。
- 转入：native 完成一次与当前身份和 `presentedContentRect` 匹配的显示后 → `webRect` 收窄到 `chromeRect`，原生 stage 显示。退出：连接 lifecycle 变 `stopping`/`stopped`/`error`、effective viewport 变化、当前 document/tab 变化或几何失配 → `webRect` 回 `drawableWindowRect`。
- 网络中断：`state != 'connected'` → `visibleContent='webview'`，WebView 回全应用态，空态 + 错误提示可见；不清空 profile/session。

### 5.3 组合与冲突规则

| connection state | displayed frame 有效性 | presentation | `webRect` | 内容区可见 |
| --- | --- | --- | --- | --- |
| disconnected / idle / starting | 无 displayed frame | browsing | drawableWindowRect | WebView 空态 |
| connected | 无有效 displayed frame | browsing | drawableWindowRect | WebView（等待最新画面/空态） |
| connected | displayed frame 有效且几何匹配 | browsing | chromeRect | 原生视频 |
| connected | displayed frame 有效 | overlay | drawableWindowRect | WebView overlay |
| connected | displayed frame 无效 | overlay | drawableWindowRect | WebView overlay |
| stopping / error | - | overlay | drawableWindowRect | WebView overlay（先关 overlay 再回落） |

规则：overlay 优先于 `visibleContent`；overlay 关闭后回到 `visibleContent` 决定的行。不得用首帧到达、DOM `getBoundingClientRect`、日志或 `inputReady` 判定视频可见性。

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

- `set_presentation` 与本地抽屉是本地 presentation 操作，**不要求远端 writer/control 权限**；observe 用户也能打开设置。`NO_AUTHORITY` 只用于 native presentation 自身无权限执行，不表示没有远端 `control_mode`。Host writer authority 只在真正发送远端写 input（pointer/gesture/edit/command）边界校验。
- 显示判定唯一 owner 是各平台 native display owner；UI/domain 只消费 `ProbeSnapshot.layout` 投影，不自行计算 displayed-frame 匹配，也不从 `connected`/`inputReady` 推导 ready 状态。

闭环路径（全部落到既有 SESE 图唯一出口 `layout-resource-terminal-result`）：

| 事件 | 生产者 | 校验 | 结果 | consumer |
| --- | --- | --- | --- | --- |
| UI 打开 drawer | UI | - | 发 `set_presentation({id, state:'overlay'})` | native |
| native 应用成功 | AB-04 | `webRect == drawableWindowRect` 且主线程 | `applied` + 新 `revision` | UI 显示有效 overlay 状态 |
| native 拒绝 | AB-04 | 无 authority / Activity 未 resumed / 几何不可用 | `rejected` + reason | UI 不显示假成功；drawer 视觉回退；不误点远端 |
| 系统 Back | UI | overlay 打开 | 先 `set_presentation(browsing)`，再关 drawer | drawer 关闭，app 留前台 |
| drawer close / outside | UI | - | `set_presentation(browsing)` | WebView 回 `chromeRect` |
| 旋转 | OS | drawer 打开 | 保留 overlay，`webRect` 重算为新 `drawableWindowRect` | 输入恢复，caret 用新 transform |
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

以 Android 1080×2400 稳定证据为示例，所有数值都必须在实际设备上重新测量：

- `drawableWindowRect` 是实际 app window 的可绘制范围（示例 display bounds `[0,0][1080,2400]`，若 window offset 非 `(0,0)` 必须换算到本地 origin/dp）。
- root（FrameLayout）bounds 等于 `drawableWindowRect`，没有任何 `systemBars` padding；`safeContentRect` 从它显式扣 systemBars 一次（示例顶部约 63px/对应 dp）；浏览态 chrome 为薄栏，`advertisedViewportRect` 从 `safeContentRect` 再扣 chrome 高度，底部不再留 370dp 空面板。
- Android root 直接子 View 才使用 root-local typed Rect：`stage` 接收
  `presentedContentRect`，`webView` 接收 `chromeRect`/`drawableWindowRect`。
  `videoClip` 和 `SurfaceView` 分别使用 stage-local 与 clip-local 派生几何；
  所有 px 转换使用同一 density，不能因 padding/offset 各做一次补偿。
- 以 `drawableWindowRect=[0,0][360,800]`、top/bottom bars 各 21dp、chrome
  72dp 为例，`safeContentRect=[0,21][360,779]`、
  `presentedContentRect=[0,93][360,779]` 时，stage 的 root-local top 是 93dp；
  stage-local centered clip 若 top=23dp，则有效可见图像 root top 是
  `93+23=116dp` 一次相加，绝不是 `93+93=186dp`。此数值仅示例，实际验证必须
  读取测量值与实际 child bounds。
- 抽屉态 backdrop + WebView overlay 覆盖实际 `drawableWindowRect`；drawer 自身内容单独避开 system bars，右宽最大约 `min(92vw,390dp)`；不通过 root 或 WebView 的 padding 二重实现。
- 未连接或没有有效 displayed frame 时：`webRect = drawableWindowRect`，顶栏 + 内容区空态在同一 WebView，不能把 `empty-state` 当成第二页面输入框。

此数字是设计前的期望形态，实际产品验证以 `blackbox-plan.md` 的矩形断言为准。

## 8. 与既有图关系

沿用既有 `dagpipe/graphs/m1-native-chrome-layout.graph.json`（单一业务对象流：布局意图 → 内容矩形 + presentation → 输入路由 → 资源释放），只补节点语义与末端结果类型，不新增第二套图。它复用已有 m1-chrome-request、m1-chrome-lifecycle、m1-navigation、m1-viewport、m1-pointer、m1-editing、m1-focus。图由 `dagpipe graph validate` 校验通过。

## 9. 禁止与边界

- 不重写 Cordis 组件、样式主题、浏览器 wire。
- 不把视频、IME、gesture 转成 WebView 事件模拟。
- 不为 drawer 或 local chrome 修改 Host viewport；不因抽屉开关 reload。
- 不从 stage 当前尺寸反推申报矩形；不从首帧到达/日志推断 control 权；视频可见性不消费 `inputReady`/`control_mode`/`commandPending`/`operation_running`。
- 不改 `NetworkSession` 的 typed command 和输入 lease。
- 不在本次触碰 Profile、首次连接、Media、launcher 作者的产品文件；`MainActivity`/`NetworkSession` 后续由主控串行交接。

## 10. 后继产品范围（不属本次）

本节只声明能力归属，不属本设计 run 的实施范围：

- AB-04 Android `MainActivity.java` / `RemoteImeView.java`：
  `drawableWindowRect`/`safeContentRect`/矩形派生、root 无 padding、root
  直接子 View 的 root-local 应用、stage-local fit clip、clip-local coded
  surface、有效可见图像 transform、`adjustResize`、`set_presentation` 接收、
  资源释放。
- AB-04 Mac `main.swift` / `connection-bridge`：单 content view、points 申报、`set_presentation` 绑定。
- AB-02/03 UI/domain：`set_presentation` intent、`NativeLayoutProjection`、`.viewer` 容器来源、`PresentationResult` 消费。

## 11. 剩余能力

- 未安装/未启动浏览器或 app。
- 未跑 Rust/V8/Media 构建；只读源码和稳定 launcher XML/PNG。
- 真机/runtime window 黑盒留给控制器后续授权。
- 本 run 只产出设计和独立 consumer 设计计划，不代替产品编码或架构 review。

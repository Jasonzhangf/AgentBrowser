# Implementation Contracts

Status: `DESIGN_CONTRACT`，编码前需独立 design PASS。产品 source 零 diff。

## Ownership and allowed files

| 层 | 允许文件 | 禁止 |
| --- | --- | --- |
| AB-02/03 UI/domain | `packages/client-domain/probe.ts`、`packages/ui-plugins/main.tsx`、`packages/ui-plugins/probe.css`、`packages/ui-plugins/consumer-frame.ts`、`packages/ui-plugins/browser-consumer.tsx`、相关 tests | 新增第二网页输入框，改动 remote input/operation/command wire |
| AB-04 Android | `apps/android/app/src/main/java/com/agentbrowser/probe/MainActivity.java`、`apps/android/app/src/main/AndroidManifest.xml`、必要时 `RemoteImeView.java`（layout/IME 资源释放） | 并发修改 `NetworkSession.java` 输入文件；不改 Android-native lib/OB protocol |
| AB-04 Mac | `apps/macos/Sources/main.swift`、必要时 `connection-bridge/src/main.rs`（仅 presentation 绑定） | 绕过 bridge；更改现有 editing/gesture command 字段 |
| 后续 First-connection / Profile / Media | `MainActivity.java`、`NetworkSession.java`、`RemoteInput.java`、`media` 相关 | 本设计不覆盖这些文件 |

## Exact typed UI control

`ProbeCommand` 增加（带 caller 稳定 `id`）：

```ts
| { id: string; op: 'set_presentation'; state: 'browsing' | 'overlay' }
```

`ProbeSnapshot` 增加只读投影（不改 `NetworkSession.declareViewport` 签名）：

```ts
layout?: {
  drawableWindowRect: Rect;
  safeContentRect: Rect;
  advertisedViewportRect: Rect;
  presentedContentRect: Rect;
  chromeRect: Rect;
  webRect: Rect;
  visibleContent: 'webview' | 'native-video';
  displayedFrame?: { generation: number; sessionId: string; tabId: string; documentRevision: number; viewportRevision: number };
  currentTabId: string | null;
  presentationViewport: Rect;
  geometryMatches: boolean;
  presentation: 'browsing' | 'overlay';
  revision: number;
}
```

`Rect` 为 `{x:number;y:number;width:number;height:number}`，全部非负、尺寸 >= 0。
typed projection 中的 Rect 都是 Android root-local dp 或 Mac content points；
Android 的 origin 是 edge-to-edge window/root，root 不设置任何 padding。
`drawableWindowRect` 是该 root 的完整本地坐标，其余 typed Rect 从同一 origin
派生。应用时按直接父级解释：root 直接子 View（`stage`、`webView`）使用
root-local bounds；`stage` 直接子 `videoClip` 使用 stage-local fit/center；
`videoClip` 直接子 `SurfaceView` 使用 clip-local coded/source geometry。
`RemoteImeView` 的实际父容器是 stage，因此它的 LayoutParams 也是 stage-local。
screenshot/display origin 只用于证据换算；CSS px 只用于
`ViewportDeclaration`。

- `set_presentation` 是同步 typed 命令；成功返回 `{status:'applied', geometry:{webRect, revision}}`，失败以 `{rejection}`/`{status:'rejected', reason}` 返回；UI 不自行回滚几何。
- `layout.presentation.visibleContent` 只能来自本地显示 owner 在 frame 实际显示确认后写入的 displayed identity + geometry match（§5.2），不来自 DOM/首帧到达/日志，也不来自 `inputReady`。
- `set_presentation` 与本地抽屉不要求远端 writer/control 权限；`NO_AUTHORITY` 只表示 native presentation 自身无权限。Host writer authority 只在真正发送远端写 input 的边界校验。
- Android/Mac 必须在主线程响应；UI `request` 已通过 Promise/JS bridge 同步。
- 冻结 `rejection.reason` 取值：`NO_AUTHORITY`、`ACTIVITY_NOT_RESUMED`、`GEOMETRY_UNAVAILABLE`。

## viewport / IME 真源

- `reportViewport()` 只读 `advertisedViewportRect`，禁止读 `stage.getWidth()/getHeight()`。
- 触发集：旋转 / 物理窗口变化、system bars 变化、薄 chrome 高度变化。明确不触发：local 地址栏 IME、remote 编辑 IME、drawer、backdrop、`presentation` 切换、连接态切换。
- `presentedContentRect` 才随 IME 收缩；它是 stage 的 root-local 可用容器。
  原生等比 fit 在 stage-local 计算 `videoClip`，SurfaceView 在 clip-local 计算
  coded/source geometry；caret、选区、composition 共用这一条 native parent
  transform，而不是把 `presentedContentRect` 再套到 `videoClip`。
- Android：采用单一 edge-to-edge root 且 root 不设置任何 padding；Manifest 显式
  `android:windowSoftInputMode="adjustResize"`；root insets listener 只读取
  `systemBars()`/`ime()` 供派生，不调用 `setPadding`；IME inset 单独只喂
  `presentedContentRect`；`WindowMetrics` 物理 px 先减 window/root 坐标偏移并按
  density 转 dp，`drawableWindowRect` 即 root 本地坐标，`safeContentRect` 显式
  扣 bars 一次，`advertisedViewportRect`/`chromeRect`/`presentedContentRect` 从
  `safeContentRect` 派生。`stage` 是 root 直接子 View，只有它接收
  `presentedContentRect` 的 root-local frame；`videoClip` 是 stage 直接子 View，
  只接收 stage-local fit/center；`SurfaceView` 是 clip 直接子 View，只接收
  clip-local coded/source geometry。
- Mac：`advertisedViewportRect == presentedContentRect`（points）；`updateViewport()` 不再乘 `backingScaleFactor`，断连且无矩形时不申报。

## Android contract

1. 移除 `layoutChrome()`（L136-142）的 370/136 面板语义。
2. Manifest 增加 `android:windowSoftInputMode="adjustResize"`（当前缺失，否则 IME 走 adjustPan）。
3. 根容器改 edge-to-edge `FrameLayout` 且无 padding；root insets listener（L54-58）只读取 systemBars/IME 供矩形派生，不调用 `setPadding`；IME inset 单独读、只喂 `presentedContentRect`；保持 `stage.addView(videoClip)` 与 `videoClip.addView(video)`。`stage` 以 `presentedContentRect` 的 root-local frame 布局；`videoClip` 在 stage 内按 `min(stageWidth/visibleWidth, stageHeight/visibleHeight)` fit 并居中，使用 stage-local x/y；`video` 在 clip 内按同一个 scale 使用 coded 尺寸和 clip-local visible-source origin。浏览态时 `chromeRect` 置顶且是 WebView 的 root-local 容器。
4. `WebView` 容器 `webRect`：无有效 displayed frame、几何不匹配或 `visibleContent='webview'` 时为 `drawableWindowRect`（全应用，含顶部 chrome，空态可见）；`visibleContent='native-video'` 浏览态时为 `chromeRect`；overlay 态为 `drawableWindowRect`。
5. `reportViewport()`（L307-313）改从 `WindowMetrics + system bars + 薄 chrome` 派生 `advertisedViewportRect`；overlay 开关、backdrop、IME 不触发。
6. Overlay 打开时 `webView.bringToFront()`；关闭后 `stage.bringToFront()`；不发送 pointer 事件到 RemoteIme 或页面。
7. 触摸在浏览态只走 `video`/`stage`；全应用态/overlay 只走 WebView；`main[data-remote=true] .viewer` 保持 `pointer-events:none`（CSS L95）。
8. 旋转：唯一 resize 触发，重测 `advertisedViewportRect` 并申报；drawer 打开时保留 overlay，`webRect` 重算为新 `drawableWindowRect`；drawer 关闭时恢复浏览态几何。IME 变化只重算 `presentedContentRect`，不申报。
9. Close/back/cancel 真实焦点：系统 Back 先关 overlay 再关 drawer，app 留前台；地址栏 focus 关闭时保留到调用控件；远端编辑 focus 由 `RemoteImeView.sync()`（L45-63）决定开关，不因 drawer 改变而误发。
10. 非零四边 insets 回归：root 无 padding 时，`webRect` 与 stage 的 root-local
    bounds 分别等于对应 typed Rect 的 px 界；`videoClip` 的实际 bounds 必须等于
    stage-local fit/center 推导值，其 root-local 界再由 stage origin 一次换算；
    `SurfaceView` 的实际 bounds 必须等于 clip-local coded/source 推导值。不得把
    stage 的 root-local x/y 再应用到 clip，也不得出现 root padding 二次偏移。
    有效可见图像 root rect 与 pointer/caret/selection/composition 使用同一个
    native transform；letterbox bands 与 coded padding 不可点击。

## Mac contract

1. 移除 `NSSplitViewController` 双最小 320 panel（main.swift L1465-1475）；换成单 content view（`NSView` 容器）。
2. 同一 `WKWebView`：无有效 displayed frame、几何不匹配或 `visibleContent='webview'` 时为 content bounds（全应用）；`visibleContent='native-video'` 浏览态时以 `chromeRect` 置顶；overlay 态扩展为 content bounds。overlay 的 backdrop 覆盖 content bounds；drawer 内容单独避开窗口 safe 边。`VideoSurfaceView` 是 content view 的直接子视图，填 `presentedContentRect` 的 AppKit points；其 content-local aspect fit 与 `pageCoordinates`/`viewPoint` 共用 bounds，不引入第二层 stage/clip 语义。
3. `updateViewport()`（L1523-1530）改从 `advertisedViewportRect`（AppKit points）计算 CSS px，不再乘 `backingScaleFactor`；断连且无矩形时不申报。
4. 鼠标：浏览态且 `visibleContent='native-video'` 时才走现有 `mouseDown/Dragged/Up/scrollWheel`（L574-673）；全应用态/overlay 态 WebView 消费 mouse，不转发 DOM。
5. `firstRect(forCharacterRange:)`（L1404）继续用同一 `viewPoint()`；overlay 时 caret 不可见，关闭后再由远端 context 恢复。

## Overlay 生命周期与资源释放（三态闭环）

- UI 发 `set_presentation({id, state})`；native 主线程应用成功后返回 `applied` + 新 `revision`，UI 才显示有效状态。overlay 应用后的 `webRect` 必须等于 `drawableWindowRect`，但该 full-window 覆盖不解锁 drawer 内容越过 system bars；drawer 内容单独在 `safeContentRect` 区域内排版。
- 拒绝：返回 `{status:'rejected', reason}`；无假成功、不误点远端；drawer 若已视觉打开需回退。
- 取消（系统 Back）：`set_presentation(browsing)` 成功后再关 drawer；app 留前台。
- 释放：见 §6.2 资源清单；唯一 owner 是 AB-04 原生；终点 `layout-resource-terminal-result` 带 `status ∈ {applied, rejected, released}`。
- **永久资源**（insets/layout listener、WebView、RemoteImeView、Mac viewport Timer）只在其生命周期终点释放；drawer close **不**重建或移除任何永久资源。

## Cross-module handoff

后续 Android + first-connection 作者共享 `MainActivity.java`、
`NetworkSession.java`。本设计只锁定顺序：

1. UI/domain 先做（独立可测）；它只消费 root-local typed projection，不计算
   stage/clip/source geometry。
2. Android 布局作者独占 `MainActivity.java`、`AndroidManifest.xml`、必要时
   `RemoteImeView.java`，完成 root/stage/videoClip/SurfaceView/RemoteIme 的
   parent-space 变更并独立提交；在提交完成前，first-connection 作者不得写
   `MainActivity.java`。
3. Android 布局作者让出 `MainActivity.java` 后，first-connection 作者才可写
   `NetworkSession.java`、`RemoteInput.java` 及其 setup 文件并接线；不得复制
   一套 geometry、改变 `declareViewport` 语义或把控制真相放进 payload。

同一 commit 中不得两作者同时修改上述共享文件；主控串行交接，冲突回退到最先
失败的 owner。

### Successor ownership and exact APIs

| Owner | Exact files | Owned contracts / APIs | Forbidden geometry side-paths |
| --- | --- | --- | --- |
| AB-02/03 UI/domain | `packages/client-domain/probe.ts`、`packages/ui-plugins/main.tsx`、`packages/ui-plugins/probe.css`、`packages/ui-plugins/consumer-frame.ts`、`packages/ui-plugins/browser-consumer.tsx`、相关 tests | `ProbeCommand` 增加 `set_presentation({id,state})`；`ProbeSnapshot.layout` 只读投影字段与 `parseSnapshot` 校验；`main.tsx` 的 `data-ready` 绑定 `visibleContent === 'native-video'`；`probe.css` 仅在正式 ready 时隐藏 `.viewer-surface`，drawer 内容单独避开 bars | 不自行计算 displayed-frame 匹配；不从 `connected`/`inputReady` 隐藏空态；不为 overlay 制造第二输入框；不计算 stage/clip/source geometry |
| AB-04 Android | `MainActivity.java`、`AndroidManifest.xml`、必要时 `RemoteImeView.java` | root = edge-to-edge `FrameLayout` 且无 padding；insets listener 只读取 `systemBars()`/`ime()` 供 rect 派生；`drawableWindowRect`/`safeContentRect`/`advertisedViewportRect`/`presentedContentRect`/`chromeRect`/`webRect` 派生；stage root-local、videoClip stage-local、SurfaceView clip-local；RemoteImeView 保持 stage parent；`set_presentation` 主线程 applied/rejected；`android:windowSoftInputMode="adjustResize"` | 不调用 root `setPadding`；不为 root padding 做补偿；不把 stage 的 root-local x/y 再应用到 videoClip；不从 stage 当前尺寸申报；不改 Host wire |
| AB-04 Mac | `main.swift`、必要时 `connection-bridge/src/main.rs`（仅 presentation 绑定） | content view 替代 split；WKWebView/video frame 按 content points 应用；`updateViewport()` 从 `advertisedViewportRect` points 计算；pointer/caret/selection 共用 `pageCoordinates`/`viewPoint` | 不再乘 `backingScaleFactor`；不从 `videoView.bounds` 反推申报矩形；不绕过 bridge；不新增 stage/clip 第二几何层 |

## UI consumer binding

- `main.tsx` 的 `.viewer` 不得再以 `connected` 作为 `data-ready`；改为消费 native layout projection 中“有效 displayed frame + `geometryMatches`”的正式可见性。
- `probe.css` 的 `.viewer[data-ready="true"] .viewer-surface { visibility: hidden; opacity: 0; }` 只在 `visibleContent === 'native-video'` 时命中；`data-ready` 为 false 时空态可见。`native-video` 时 `.viewer` 继续保持透明（`main[data-remote="true"] .viewer`）。
- overlay 时 backdrop 横跨 `drawableWindowRect`；drawer 容器/内容使用 `safeContentRect` 对应的独立 insets，不得要求原生 root padding。
- 复用既有 `.viewer-empty`，不新增 renderer/DOM/第二输入框。`inputReady` 只可继续用于写权限判断，不得决定空态或视频可见性。

## Tests

- 单元：`packages/client-domain/probe.test.ts` 覆盖 `set_presentation`（含 `id`、`applied`/`rejected` 与三个 reason）和 `layout` 投影校验（`drawableWindowRect`/`safeContentRect`/`advertisedViewportRect`/`presentedContentRect`/`chromeRect`/`webRect`/`presentationViewport`/`revision` 及 `displayedFrame`/`geometryMatches`）。typed projection 不得新增 `stageRect`/`videoClipRect`/`surfaceViewRect` 等第二套 layout 控制真源。
- 白盒：不新增 JS hit-test 断言；现有 `ui-contract.test.ts` 仍保留。
- 公开 consumer：`w4-ui-consumer` 增加 `presentation` 场景，覆盖 connected 但无有效 displayed frame → 空态可见；有效 displayed frame → native-video 透明；observe/control 交还/operation pending 不改变可见性；证明 overlay intent 只在 drawer 事件出现一次、关闭后再发一次 `browsing`、全程不产生 viewport 命令。
- 生命周期：consumer 覆盖 rejected（无假成功）、Back（先 overlay 后 drawer）、卸载（资源释放一次）。
- geometry 回归（后续安装产品执行）：非零四边 insets、odd coded padding、
  aspect-fit letterbox、portrait/landscape 与 IME 下断言 root 无 padding；stage
  root-local frame 等于 `presentedContentRect`；videoClip 实际 bounds 等于
  stage-local fit/center；SurfaceView 实际 bounds 等于 clip-local coded/source；
  有效可见图像 root rect 与 pointer/caret/selection/composition mapping 一致；
  letterbox/coded padding 不产生点击；overlay 覆盖 `drawableWindowRect` 且 drawer
  内容不越过 bars；IME/rotation/Back 不改变 source viewport/state 并释放资源。
- 本 run 不执行 product test；只作为后续编码验收的合同输入。

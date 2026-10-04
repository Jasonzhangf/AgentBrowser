# Implementation Contracts

Status: `DESIGN_CONTRACT`，编码前需独立 design PASS。产品 source 零 diff。

## Ownership and allowed files

| 层 | 允许文件 | 禁止 |
| --- | --- | --- |
| AB-02/03 UI/domain | `packages/client-domain/probe.ts`、`packages/ui-plugins/main.tsx`、`packages/ui-plugins/probe.css`、`packages/ui-plugins/consumer-frame.ts`、`packages/ui-plugins/browser-consumer.tsx`、相关 tests | 新增第二网页输入框，改动 remote input/operation/command wire |
| AB-04 Android | `apps/android/app/src/main/java/com/agentbrowser/probe/MainActivity.java`、必要时 `RemoteImeView.java`（layout/IME 资源释放） | 并发修改 `NetworkSession.java` 输入文件；不改 Android-native lib/OB protocol |
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
  windowFrameRect: Rect;
  advertisedViewportRect: Rect;
  presentedContentRect: Rect;
  chromeRect: Rect;
  webRect: Rect;
  visibleContent: 'webview' | 'native-video';
  presentation: 'browsing' | 'overlay';
  revision: number;
}
```

`Rect` 为 `{x:number;y:number;width:number;height:number}`，全部非负、尺寸 >= 0，单位为 dp（Android）/ AppKit point（Mac）；CSS px 只用于 `ViewportDeclaration`。

- `set_presentation` 是同步 typed 命令；成功返回 `{status:'applied', geometry:{webRect, revision}}`，失败以 `{rejection}`/`{status:'rejected', reason}` 返回；UI 不自行回滚几何。
- `layout.presentation.visibleContent` 只能来自本地几何 owner 的 typed 判定（§5.2），不来自 DOM/firstframe/日志。
- Android/Mac 必须在主线程响应；UI `request` 已通过 Promise/JS bridge 同步。
- 冻结 `rejection.reason` 取值：`NO_AUTHORITY`、`ACTIVITY_NOT_RESUMED`、`GEOMETRY_UNAVAILABLE`。

## viewport / IME 真源

- `reportViewport()` 只读 `advertisedViewportRect`，禁止读 `stage.getWidth()/getHeight()`。
- 触发集：旋转 / 物理窗口变化、system bars 变化、薄 chrome 高度变化。明确不触发：local 地址栏 IME、remote 编辑 IME、drawer、backdrop、`presentation` 切换、连接态切换。
- `presentedContentRect` 才随 IME 收缩；原生等比 fit、caret、选区、composition 共用它。
- Android：Manifest 显式 `android:windowSoftInputMode="adjustResize"`；root insets listener 只扣 `systemBars()`，IME inset 单独只喂 `presentedContentRect`；申报用 `WindowMetrics`（API 30+）+ system bars + 薄 chrome。
- Mac：`advertisedViewportRect == presentedContentRect`（points）；`updateViewport()` 不再乘 `backingScaleFactor`，断连且无矩形时不申报。

## Android contract

1. 移除 `layoutChrome()`（L136-142）的 370/136 面板语义。
2. Manifest 增加 `android:windowSoftInputMode="adjustResize"`（当前缺失，否则 IME 走 adjustPan）。
3. 根容器改 `FrameLayout`；root insets listener（L54-58）只扣 `systemBars()`，IME inset 单独读、只喂 `presentedContentRect`；`stage` 位于 `advertisedViewportRect`（浏览态）。
4. `WebView` 容器 `webRect`：未具正式原生呈现状态或 `visibleContent='webview'` 时为 `windowFrameRect`（全应用，含顶部 chrome，空态可见）；`visibleContent='native-video'` 浏览态时为 `chromeRect`；overlay 态为 `windowFrameRect`。
5. `reportViewport()`（L307-313）改从 `WindowMetrics + system bars + 薄 chrome` 派生 `advertisedViewportRect`；overlay 开关、backdrop、IME 不触发。
6. Overlay 打开时 `webView.bringToFront()`；关闭后 `stage.bringToFront()`；不发送 pointer 事件到 RemoteIme 或页面。
7. 触摸在浏览态只走 `video`/`stage`；全应用态/overlay 只走 WebView；`main[data-remote=true] .viewer` 保持 `pointer-events:none`（CSS L95）。
8. 旋转：唯一 resize 触发，重测 `advertisedViewportRect` 并申报；drawer 打开时保留 overlay，`webRect` 重算为新 `windowFrameRect`；drawer 关闭时恢复浏览态几何。IME 变化只重算 `presentedContentRect`，不申报。
9. Close/back/cancel 真实焦点：系统 Back 先关 overlay 再关 drawer，app 留前台；地址栏 focus 关闭时保留到调用控件；远端编辑 focus 由 `RemoteImeView.sync()`（L45-63）决定开关，不因 drawer 改变而误发。

## Mac contract

1. 移除 `NSSplitViewController` 双最小 320 panel（main.swift L1465-1475）；换成单 content view（`NSView` 容器）。
2. 同一 `WKWebView`：未具正式原生呈现状态或 `visibleContent='webview'` 时为 content bounds（全应用）；`visibleContent='native-video'` 浏览态时以 `chromeRect` 置顶；overlay 态扩展为 content bounds。`VideoSurfaceView` 填 `presentedContentRect`。
3. `updateViewport()`（L1523-1530）改从 `advertisedViewportRect`（AppKit points）计算 CSS px，不再乘 `backingScaleFactor`；断连且无矩形时不申报。
4. 鼠标：浏览态且 `visibleContent='native-video'` 时才走现有 `mouseDown/Dragged/Up/scrollWheel`（L574-673）；全应用态/overlay 态 WebView 消费 mouse，不转发 DOM。
5. `firstRect(forCharacterRange:)`（L1404）继续用同一 `viewPoint()`；overlay 时 caret 不可见，关闭后再由远端 context 恢复。

## Overlay 生命周期与资源释放（三态闭环）

- UI 发 `set_presentation({id, state})`；native 主线程应用成功后返回 `applied` + 新 `revision`，UI 才显示有效状态。
- 拒绝：返回 `{status:'rejected', reason}`；无假成功、不误点远端；drawer 若已视觉打开需回退。
- 取消（系统 Back）：`set_presentation(browsing)` 成功后再关 drawer；app 留前台。
- 释放：见 §6.2 资源清单；唯一 owner 是 AB-04 原生；终点 `layout-resource-terminal-result` 带 `status ∈ {applied, rejected, released}`。
- **永久资源**（insets/layout listener、WebView、RemoteImeView、Mac viewport Timer）只在其生命周期终点释放；drawer close **不**重建或移除任何永久资源。

## Cross-module handoff

后续 Android + first-connection 作者共享 `MainActivity.java`、`NetworkSession.java`。本设计只锁定顺序：

1. UI/domain 先做（独立可测）。
2. Android `MainActivity.java` 布局变更独立提交并让出写区。
3. First-connection 作者在拿到 `MainActivity/NetworkSession` 写释放后再接线。

同一 commit 中不得两作者同时修改上述共享文件；主控串行交接，冲突回退到最先失败的 owner。

## Tests

- 单元：`packages/client-domain/probe.test.ts` 覆盖 `set_presentation`（含 `id`、`applied`/`rejected` 与三个 reason）和 `layout` 投影校验（六矩形 + `revision`）。
- 白盒：不新增 JS hit-test 断言；现有 `ui-contract.test.ts` 仍保留。
- 公开 consumer：`w4-ui-consumer` 增加 `presentation` 场景，证明 overlay intent 只在 drawer 事件出现一次、关闭后再发一次 `browsing`、全程不产生 viewport 命令。
- 生命周期：consumer 覆盖 rejected（无假成功）、Back（先 overlay 后 drawer）、卸载（资源释放一次）。
- 本 run 不执行 product test；只作为后续编码验收的合同输入。

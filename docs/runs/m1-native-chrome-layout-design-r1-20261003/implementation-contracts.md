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

`ProbeCommand` 增加：

```ts
| { op: 'set_presentation'; state: 'browsing' | 'overlay' }
```

`ProbeSnapshot` 增加只读投影：

```ts
layout?: {
  windowRect: Rect;
  contentRect: Rect;
  chromeRect: Rect;
  presentation: 'browsing' | 'overlay';
}
```

`Rect` 为 `{x:number;y:number;width:number;height:number}`，全部非负、尺寸 >= 0。

- `set_presentation` 是同步 typed 命令，失败以 `{rejection}` 返回；UI 不自行回滚几何。
- `layout.presentation` 只能来自本地几何 owner 的返回结果，不来自 DOM/日志。
- Android/Mac 必须在主线程响应；UI `request` 已通过 Promise/JS bridge 同步。

## Android contract

1. 移除 `layoutChrome()` 的 370/136 面板语义。
2. 根容器改 `FrameLayout`，`stage` 使用 `layout_margin`/padding 位于内容区，`WebView` 使用 `ChromeRect`（浏览态）或 `WindowRect`（overlay 态）。
3. `reportViewport()` 只由 `StageRect` 变化触发；overlay 打开、关闭、backdrop、address IME 不触发。
4. Overlay 打开时 `webView.bringToFront()`；关闭后 `stage.bringToFront()` 仅把 Surface 保持在 WebView 之下的同一个 z-order；不发送 pointer 事件到 RemoteIme 或页面。
5. 触摸在浏览态只走 `video`/`stage`；overlay 态只走 WebView; WebView 内 `main[data-remote=true] .viewer` 保持 `pointer-events:none` 或由原生布局 no-input。
6. On rotation/insets/IME change: 重测 `WindowRect/ContentRect/ChromeRect`，若 drawer 关闭则恢复浏览态几何；若 drawer 打开则保留 overlay，但 overlay 以新 window 矩形成比例覆盖。
7. Close/back/cancel 真实焦点：
   - 地址栏 focus 关闭时保留到调用控件；远端编辑 focus 由 `RemoteImeView.sync()` 决定开关，不因 drawer 改变而误发。

## Mac contract

1. 移除 `NSSplitViewController` 双最小 320 panel；换成单 content view（`NSView` 容器）。
2. `VideoSurfaceView` 填满 content area，`WKWebView` 在浏览态以 `ChromeRect` 置顶，drawer 开具扩展为 content bounds。
3. `updateViewport()` 从 `videoView.bounds`（AppKit points）计算 CSS px，不再乘以 `backingScaleFactor`。
4. 鼠标：浏览态进入 video view 时才走现有 `mouseDown/Dragged/Up/scrollWheel`；overlay 态 WebView 消费 mouse，不转发 DOM。
5. `firstRect(forCharacterRange:)` 继续用同一 `viewPoint()`；当 drawer overlay 时 caret 不可见，关闭后再由远端 context 恢复。

## Cross-module handoff

后续 Android + first-connection 作者共享 `MainActivity.java`、`NetworkSession.java`。本设计只锁定顺序：

1. UI/domain 先做（独立可测）。
2. Android `MainActivity.java` 布局变更独立提交并让出写区。
3. First-connection 作者在拿到 `MainActivity/NetworkSession` 写释放后再接线。

同一 commit 中不得两作者同时修改上述共享文件；主控串行交接，冲突回退到最先失败的 owner。

## Tests

- 单元：`packages/client-domain/probe.test.ts` 覆盖 `set_presentation` 和 `layout` 校验。
- 白盒：不新增 JS hit-test 断言；现有 `ui-contract.test.ts` 仍保留。
- 公开 consumer：`w4-ui-consumer` 增加 `presentation` 场景，证明 UI 发出的 overlay intent 只在 drawer 事件出现一次，关闭后不再发送 viewport 命令。
- 本 run 不执行 product test；只作为后续编码验收的合同输入。

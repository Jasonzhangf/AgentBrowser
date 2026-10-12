# Black-box Plan（R5）

Status: `PLAN_READY` / `NOT_EXECUTED`。下列命令和断言留给实现后、独立 design PASS 后的作者执行；本 run 无权安装、启动、构建 product。三组 P1 各有独立用例与副作用断言。

## Target evidence

所有用例必须绑定：

- 候选 commit/tree + APK/app hash、安装版本、`ANDROID_SERIAL` / Mac 启动方式
- 截图 + 同帧 `adb -s "$ANDROID_SERIAL" shell uiautomator dump` 的 Accessibility XML（Mac 用 `osascript`/Accessibility 查询）
- 每次操作前后同帧读取 `layout` 投影
  （`drawableWindowRect/safeContentRect/advertisedViewportRect/presentedContentRect/chromeRect/webRect/visibleContent/displayedFrame/currentTabId/geometryMatches/presentation/revision`）
  与 Host `viewportRevision`；另从 Accessibility/View 层级记录
  `stageRectRoot`、`videoClipRectStage`、`surfaceViewRectClip` 与换算后的
  `visibleImageRectRoot`。这些实际 bounds 是证据字段，不是新增 typed 控制真源。
- 成功/失败/副作用写作 JSON `evidence/*.json` + PNG + XML；`rejection.reason` 原样保留
- 截图/XML 像素断言必须先实测 app window offset、density、systemBars insets
  与实际 child bounds；Android 断言分三层：root 直接子 View 的 XML bounds 换算
  为 root-local dp，`videoClip` 的 XML bounds 换算为 stage-local dp，`SurfaceView`
  的 XML bounds 换算为 clip-local dp。有效可见图像 root rect 再由 native
  parent transform 一次换算并与 pointer/caret/selection 证据一致。不得把未测量
  的 `[0,0][1080,2400]` 或“stage/videoClip 同 root bounds”当固定 PASS。

## 真实安装与执行步骤（不属本 run）

```sh
export ANDROID_SERIAL=<verified device>
npm run build:android && npm run install:android   # scripts/android.sh + scripts/device.sh install
bash scripts/device.sh restart && bash scripts/device.sh prepare
# Mac 客户端：
bash scripts/build-macos.sh   # install 按项目批准入口，不属本 run
```

证据目录 `evidence/`；每条用例记录执行命令、退出码、截图/XML 路径。

## L1 空态 / 薄 chrome / full-window drawer（P1 #2）

前置：未连接（`connectionState ∈ {idle, stopped}`）。

1. 冷启动，等待 UI。
2. 断言：
   - `visibleContent == 'webview'`，root bounds == `drawableWindowRect` 且 root 无 padding，`webRect == drawableWindowRect`，`.viewer-empty` 在内容区居中，顶栏与内容区同一 WebView。
   - 无 370dp 底部空面板：`webRect` 高度等于实际 drawable window 高度（本地 dp），不是 `136/370dp` 面板。
   - 无第二网页输入框：DOM/XML 只有 `#address` 一个输入框。
3. 点 `#drawer-toggle`：断言 WebView/backdrop 实际 `webRect` == 实际 `drawableWindowRect`（按实测 offset/density 换算截图像素），底部不被截断；drawer 内容避开 systemBars；`presentation == 'overlay'`；系统 bars 被包含在抽屉覆盖内，但 drawer 内内容以 `safeContentRect` 单独留边。
4. 副作用：全程 `viewportRevision` 前后不变；`webView` 未 reload（页面 JS 计数保持）。

## L2 已连接内容与远端 viewport（P1 #1/#2）

需要独立下发 Host/runtime；本 run 不执行。

1. 连接真实或开发 Host，等 native 实际显示 frame 并满足 typed session/document/effective viewport/geometry 匹配后：断言 `visibleContent == 'native-video'`，`webRect == chromeRect`，原生视频填 `presentedContentRect`。
2. **可见性与 input 分离**：在 observe、waiting、交还控制、`commandPending`/`operation running` 各阶段保持有效 displayed frame，断言 `visibleContent` 仍为 `native-video`，不因 `inputReady=false` 切空态。
3. 制造 connected 但无有效 displayed frame（首帧前、viewport pending 或几何失配）：断言 `visibleContent == 'webview'` 且 `.viewer-empty` 可见；随后 frame 实际显示，断言转为 `native-video` 且 `.viewer-surface` 隐藏。
4. 记录 `viewportRevision`（RevBefore）。非零 top/bottom bars 下断言
   `stage` 实际 root-local bounds 等于 `presentedContentRect`，
   `videoClip` 实际 stage-local bounds 等于 fit/center 推导值，`SurfaceView`
   实际 clip-local bounds 等于 coded/source 推导值；有效可见图像 root rect 为
   `stage origin + clip fit origin + visible-source origin×scale` 一次换算。
   打开 drawer：断言 video 被 overlay 遮蔽（截图），
   `presentation == 'overlay'`，`webRect == drawableWindowRect`；关闭 drawer 后
   回到同一 stage/clip transform、同一 viewpoint，`viewportRevision == RevBefore`。

## L3 local / remote IME 前后 viewport 不变（P1 #1）

1. **local 地址栏 IME**：`presentation == 'browsing'`、`visibleContent == 'native-video'`，点 `#address` 弹系统 IME。
   - 断言：`advertisedViewportRect` 与 `viewportRevision` 前后不变；
     `presentedContentRect` 收缩，stage 实际 root-local bounds 等于该新 rect；
     videoClip 在 stage-local 重新 fit/center，SurfaceView 在 clip-local 保持
     coded/source geometry；有效可见图像 root rect 与 pointer/caret 同步更新；
     `revision` 自增。
   - 输入命中：地址栏输入只进 chrome，远端无输入事件（Host operation 计数不变）。
2. **remote 编辑 IME**：在视频上点远端 input，弹 IME。
   - 断言：`viewportRevision` 不变；远端
     `EditorContext`/selection/composition 不因 IME 开闭重发；caret 候选窗口位置
     随 stage/fit/source parent chain 更新，使用与 pointer 相同的 native
     transform，而不是单独套用 `presentedContentRect`。
   - composition 安全：先 composition 再关 IME，composition 状态按远端 receipts 结束，不写新字段。
3. 关闭 IME：`presentedContentRect` 恢复，`advertisedViewportRect`/`viewportRevision` 仍不变。

## L4 旋转唯一 resize（P1 #1）

1. 记录 RevBefore、session/document/control。
2. 旋转设备。
3. 断言：`advertisedViewportRect` 变为新 orientation 尺寸，
   `viewportRevision != RevBefore`（**必须变化一次**）；session/document/control
   不变；root 无 padding，`chromeRect`/`presentedContentRect`/`webRect` 按新
   root-local rect 重算，stage/videoClip/SurfaceView 按新的 stage-local 与
   clip-local transform 重算；drawer 关闭时恢复浏览态几何，drawer 打开时保留
   overlay 且 `webRect` == 新 `drawableWindowRect`。
4. 旋转后输入：touch/caret 用新 transform 命中，恢复可编辑。

## L5 rejected intent / no authority（P1 #3）

1. 制造 native presentation 自身无法应用的 `NO_AUTHORITY`、未 resumed 或 `GEOMETRY_UNAVAILABLE` 状态（方法由作者声明）；不能拿远端 observe 当 `NO_AUTHORITY`。
2. UI 发 `set_presentation({id, state:'overlay'})`。
3. 断言：native 返回 `{status:'rejected', reason}`（reason 为 `NO_AUTHORITY`/`ACTIVITY_NOT_RESUMED`/`GEOMETRY_UNAVAILABLE` 之一）；UI 不显示有效 overlay 状态；drawer 不误拦截远端点击（远端 operation 计数不变）；`webRect` 不改。另用 observe 用户重复 `set_presentation`，断言本地抽屉仍可打开且不要求远端 writer 权限。

## L6 Back / overlay dispose / 重连 / 资源恢复（P1 #3）

1. **系统 Back**：overlay 打开时按 Back → 断言先关 overlay（`presentation` 经 `applied` 回 `browsing`）再关 drawer，app 留前台，无远端导航；source viewport/state 不因 Back 重载或改变。
2. **overlay dispose**：overlay 打开时旋转/切后台再回，断言 overlay 保留或按 §5.3 规则回落，`revision` 自增，输入恢复。
3. **重连**：drawer overlay 中断网 → 断言 `visibleContent == 'webview'`、`webRect == drawableWindowRect`、空态+错误可见；重连成功且 native 实际显示新 frame 后回 `native-video`。
4. **资源恢复**：`onStop`/`onDestroy` 后断言永久资源（insets listener、layout listener、webView、RemoteImeView、Mac Timer）按 §6.2 释放一次；drawer 反复开关**不**重建任何永久资源（listener 计数不变）；IME/rotation/Back 后 source viewport/state 保持。

## L7 Edge target and caret

1. Android `touch(30,30)`、`touch(width-1,height-1)` 与 caret 输入继续使用既有
   native video frame/source transform：pointer 在 `SurfaceView` 本地坐标减去
   visible-source origin×scale 后除以同一 scale；有效可见图像 root rect 是
   `stage origin + videoClip fit origin + visible-source origin×scale` 一次换算。
   非零四边 insets、odd coded padding、aspect-fit letterbox、portrait/landscape
   与 IME 下，pointer/caret/selection/composition 命中同一实际 bounds；letterbox
   bands 与 coded padding 不产生点击，overlay 关闭后无误触、误 focus。
2. Mac mouse 在 video bounds 内命中与 caret 候选窗口使用同一 `viewPoint`；overlay 打开时不产生页面点击；root-local points 与 CSS px 映射保持 1:1，不含 backing factor。

## L1–L7 nonzero top/bottom bars regression matrix

这些是未来 installed-product 黑盒，不是本 docs run 的 executed PASS：

| Reference | Nonzero bars assertion | Side effects |
| --- | --- | --- |
| L1 | root 无 padding；overlay `webRect` 覆盖 `drawableWindowRect`，drawer 内容停在 system-bars 边界内 | 不改变 `viewportRevision`，不 reload |
| L2 | `stage` root-local == `presentedContentRect`；`videoClip` stage-local == fit/center；`SurfaceView` clip-local == coded/source；native-video 可见性仍由 displayed frame match 决定 | observe/waiting/operation pending 不切空态；UI 只消费 native projection |
| L3 | IME 只收缩 `presentedContentRect`，stage root-local 跟随，videoClip/SurfaceView 在各自父空间重新派生；`advertisedViewportRect`/Host `viewportRevision` 不变 | caret/pointer 同一 native parent/source transform |
| L4 | 旋转后 typed Rects 与 stage/videoClip/SurfaceView 各自 space 的 bounds 同步；drawer overlay 保留并与新 `drawableWindowRect` 一致 | Host viewport 唯一 resize 一次 |
| L6 | Back/close/cancel 先 overlay 后 drawer；source viewport/state 不重载；资源释放一次 | drawer 反复开关不重建永久资源 |
| L7 | pointer/caret 在非零四边 insets、odd coded padding、letterbox 下按 stage+clip+source 的有效可见 bounds 命中 | 不产生 overlay 误触，coded padding 不可点击 |

## Command format

```
{id: "UI.FOCUS", tool: "adb/uiautomator", input:"drawer-toggle click", expected:"drawer bounds == window", sideEffect:"no viewport command"}
```

证据格式：

```json
{
  "case": "L2_open_drawer",
  "result": "PASS",
  "candidate": {"commit": "<sha>", "tree": "<tree>", "apk_sha256": "<hash>"},
  "device": {"serial": "<ANDROID_SERIAL>", "os": "<ver>", "ime": "<id>"},
  "artifact": "evidence/L2-open.png",
  "xml": "evidence/L2-open-window.xml",
  "measurement": {
    "windowOffsetPx": "[0,0]",
    "density": 3.0,
    "systemBarsInsetsPx": "[0,63,0,63]"
  },
  "layout": {
    "drawableWindowRect": "[0,0][360,800]",
    "safeContentRect": "[0,21][360,779]",
    "advertisedViewportRect": "[0,93][360,779]",
    "presentedContentRect": "[0,93][360,779]",
    "chromeRect": "[0,21][360,93]",
    "webRect": "[0,0][360,800]",
    "stageRectRoot": "[0,93][360,779]",
    "videoClipRectStage": "[0,23][360,663]",
    "surfaceViewRectClip": "[0,0][360,640]",
    "visibleImageRectRoot": "[0,116][360,756]",
    "visibleContent": "webview",
    "displayedFrame": null,
    "currentTabId": null,
    "presentationViewport": "[0,93][360,779]",
    "geometryMatches": false,
    "presentation": "overlay",
    "revision": 7
  },
  "viewportRevBefore": 2,
  "viewportRevAfter": 2,
  "sideEffects": {"remoteOperations": 0, "hostDocumentChanged": false}
}
```

## Not executed by this run

- 15T/真实设备、Mac install、Host/runtime 注入不执行；视频未连接是合法状态。
- 不把 emulator 结果冒充 15T；不把设计断言当 PASS。

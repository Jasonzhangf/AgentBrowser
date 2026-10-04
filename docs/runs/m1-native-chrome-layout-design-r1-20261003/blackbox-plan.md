# Black-box Plan（R2）

Status: `PLAN_READY` / `NOT_EXECUTED`。下列命令和断言留给实现后、独立 design PASS 后的作者执行；本 run 无权安装、启动、构建 product。三组 P1 各有独立用例与副作用断言。

## Target evidence

所有用例必须绑定：

- 候选 commit/tree + APK/app hash、安装版本、`ANDROID_SERIAL` / Mac 启动方式
- 截图 + 同帧 `adb -s "$ANDROID_SERIAL" shell uiautomator dump` 的 Accessibility XML（Mac 用 `osascript`/Accessibility 查询）
- 每次操作前后同帧读取 `layout` 投影（`windowFrameRect/advertisedViewportRect/presentedContentRect/chromeRect/webRect/visibleContent/presentation/revision`）与 Host `viewportRevision`
- 成功/失败/副作用写作 JSON `evidence/*.json` + PNG + XML；`rejection.reason` 原样保留

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
   - `visibleContent == 'webview'`，`webRect == windowFrameRect`，`.viewer-empty` 在内容区居中，顶栏与内容区同一 WebView。
   - 无 370dp 底部空面板：`webRect` 高度 ≈ 窗口高度（减 system bars），非 `136/370dp` 面板。
   - 无第二网页输入框：DOM/XML 只有 `#address` 一个输入框。
3. 点 `#drawer-toggle`：断言 backdrop + drawer bounds == `[0,0][1080,2400]`，底部不被截断；`presentation == 'overlay'`，`webRect == windowFrameRect`。
4. 副作用：全程 `viewportRevision` 前后不变；`webView` 未 reload（页面 JS 计数保持）。

## L2 已连接内容与远端 viewport（P1 #1/#2）

需要独立下发 Host/runtime；本 run 不执行。

1. 连接真实或开发 Host，`inputReady == true` 后：断言 `visibleContent == 'native-video'`，`webRect == chromeRect`，原生视频填 `presentedContentRect`。
2. 记录 `viewportRevision`（RevBefore）。
3. 打开 drawer：断言 video 被 overlay 遮蔽（截图），`presentation == 'overlay'`，`webRect == windowFrameRect`。
4. 关闭 drawer：断言 video 回到同一 `presentedContentRect`、同一 viewpoint；`viewportRevision == RevBefore`（**not** 允许变化）。

## L3 local / remote IME 前后 viewport 不变（P1 #1）

1. **local 地址栏 IME**：`presentation == 'browsing'`、`visibleContent == 'native-video'`，点 `#address` 弹系统 IME。
   - 断言：`advertisedViewportRect` 与 `viewportRevision` 前后不变；`presentedContentRect` 收缩；`revision` 自增。
   - 输入命中：地址栏输入只进 chrome，远端无输入事件（Host operation 计数不变）。
2. **remote 编辑 IME**：在视频上点远端 input，弹 IME。
   - 断言：`viewportRevision` 不变；远端 `EditorContext`/selection/composition 不因 IME 开闭重发；caret 候选窗口位置随 `presentedContentRect` 更新。
   - composition 安全：先 composition 再关 IME，composition 状态按远端 receipts 结束，不写新字段。
3. 关闭 IME：`presentedContentRect` 恢复，`advertisedViewportRect`/`viewportRevision` 仍不变。

## L4 旋转唯一 resize（P1 #1）

1. 记录 RevBefore、session/document/control。
2. 旋转设备。
3. 断言：`advertisedViewportRect` 变为新 orientation 尺寸，`viewportRevision != RevBefore`（**必须变化一次**）；session/document/control 不变；drawer 关闭时恢复浏览态几何，drawer 打开时保留 overlay 且 `webRect` == 新 `windowFrameRect`。
4. 旋转后输入：touch/caret 用新 transform 命中，恢复可编辑。

## L5 rejected intent / no authority（P1 #3）

1. 制造无 authority 或未 resumed 状态（例如 Activity 后台/`GEOMETRY_UNAVAILABLE` 注入，方法由作者声明）。
2. UI 发 `set_presentation({id, state:'overlay'})`。
3. 断言：native 返回 `{status:'rejected', reason}`（reason 为 `NO_AUTHORITY`/`ACTIVITY_NOT_RESUMED`/`GEOMETRY_UNAVAILABLE` 之一）；UI 不显示有效 overlay 状态；drawer 不误拦截远端点击（远端 operation 计数不变）；`webRect` 不改。

## L6 Back / overlay dispose / 重连 / 资源恢复（P1 #3）

1. **系统 Back**：overlay 打开时按 Back → 断言先关 overlay（`presentation` 经 `applied` 回 `browsing`）再关 drawer，app 留前台，无远端导航。
2. **overlay dispose**：overlay 打开时旋转/切后台再回，断言 overlay 保留或按 §5.3 规则回落，`revision` 自增，输入恢复。
3. **重连**：drawer overlay 中断网 → 断言 `visibleContent == 'webview'`、`webRect == windowFrameRect`、空态+错误可见；重连成功且 `inputReady` 后回 `native-video`。
4. **资源恢复**：`onStop`/`onDestroy` 后断言永久资源（insets listener、layout listener、webView、RemoteImeView、Mac Timer）按 §6.2 释放一次；drawer 反复开关**不**重建任何永久资源（listener 计数不变）。

## L7 Edge target and caret

1. Android `touch(30,30)`、`touch(width-1,height-1)` 与 caret 输入继续使用既有 videoClip 逆变换；overlay 关闭后无误触、误 focus。
2. Mac mouse 在 video bounds 内命中与 caret 候选窗口使用同一 `viewPoint`；overlay 打开时不产生页面点击。

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
  "layout": {
    "windowFrameRect": "[0,0][1080,2400]",
    "advertisedViewportRect": "[0,63][1080,2271]",
    "presentedContentRect": "[0,63][1080,2271]",
    "chromeRect": "[0,63][1080,279]",
    "webRect": "[0,0][1080,2400]",
    "visibleContent": "webview",
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

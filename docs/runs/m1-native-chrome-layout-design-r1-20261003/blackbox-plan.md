# Black-box Plan

Status: `PLAN_READY` / `NOT_EXECUTED`。下列命令和断言留给实现后、独立 design PASS 后的作者执行；本 run 无权安装、启动、构建 product。

## Target evidence

所有用例必须绑定：

- APK/app hash、安装版本、`ANDROID_SERIAL` / Mac 启动方式
- 截图 + 同帧 `adb shell uiautomator dump` / Accessibility XML
- 矩形期望：以截图/XML 的绝对 bounds 或对比空白区判断
- 成功/失败/副作用写作 JSON `evidence/*.json` + PNG 文件

## L1 Launcher baseline（无连接）

1. 安装候选 APK（launcher 作者或其他授权 owner 的经批准设备）。
2. 冷启动，等待 UI。
3. 期望：
   - 浏览器工具栏顶部，内容区为空态居中，无 370dp 底部空面板。
   - drawer 默认关闭；打开后 backdrop 覆盖整个窗口 `[0,0][1080,2400]`，drawer 覆盖到窗口右侧，且底部不被 370dp 面板截断。
4. 输入：ADB bounds 通过 `uiautomator` XML 拿实际 Drawer bounds 与 toolbar bounds；失败记录具体节点。

## L2 Connected content and remote viewport

需要独立下发 Host/runtime；本 run 不执行。

1. 连接真实或开发 Host，视频出现。
2. 内容区与视频 stage rect 一致；远端没有受 drawer 打开影响的 viewport revision。
3. 打开 drawer：video 被 overlay 遮蔽（截图证明），但 drawer 关闭后视频回到同一内容区、viewpoint 不变化。

## L3 Rotation / IME

1. 旋转前后 session/document/control 不变；stage rect 新尺寸，viewport 只在 stage rect 变化时声明。
2. 系统 IME 弹起：本地内容区缩短，但远端 viewport 不变（若同意方案）或走 Host 声明；drawer 打开时 IME 不改变远端。
3. 关闭 IME：内容区恢复。

## L4 Edge target and caret

1. Android `touch(30,30)`、`touch(width-1, height-1)` 与 caret 输入继续使用既有 videoClip 逆变换；overlay 关闭后不存在误触、误 focus。
2. Mac mouse 在 video bounds 内命中与 caret 候选窗口使用同一 `viewPoint`；overlay 打开时不产生页面点击。

## L5 Command format

```
{id: "UI.FOCUS", tool: "adb/uiautomator", input:"drawer-toggle click", expected:"drawer bounds == window", sideEffect:"no viewport command"}
```

证据格式：

```json
{
  "case": "L2_open_drawer",
  "result": "PASS",
  "artifact": "evidence/L2-open.png",
  "xml": "evidence/L2-open-window.xml",
  "rects": {"drawer": "[0,0][1080,2400]", "webview": "[0,0][1080,2400]"},
  "viewportRevBefore": "2",
  "viewportRevAfter": "2"
}
```

## Not executed by this run

- 15T/真实设备、Mac install、Media FAIL 不报；视频未连接是合法状态。
- 不把 emulator 结果冒充 15T。

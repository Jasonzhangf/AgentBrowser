# R5 交付摘要：parent-local fit 与单 root offset

状态：`DESIGN_READY` / `NOT_IMPLEMENTED` / `BB_NOT_EXECUTED`。只完成设计与
graph 返修，未编码、未安装、未启动、未连网，未跑产品黑盒。

## 范围与身份

- 任务：修 r4 独立 review 的唯一 code_failure P1；保留实际
  `root -> stage -> videoClip -> SurfaceView` 层级，冻结 parent-space 契约。
- 分支：`codex/m1-native-chrome-layout-design-r5-20261004`。
- 基线：exact r4 `0e387b36af08edacb97ad1b8ed0d28b0a0eaf5a6` / tree
  `0deb5dca5369c1339867960300b1bef1c362e2c6`；输入 origin/main
  `bc9d67ad037be2c7119bee692f9ad362417ffd05`。
- r5 design candidate：`b4d63afdb5c32e112e64619d4780e291a0c72e2b` / tree
  `64e042d744b0ad87c072ed7194994ad426aa1c35`，normal hook `verify:local`
  PASS（typecheck、21/21 tests、`build:ui`）。最终 receipt commit 仅写入本
  摘要与 notes，SHA/tree 由 author handoff 报告。
- 产品 `apps/ packages/ scripts/ protocol/ .appsdk/ Cargo.toml Cargo.lock`
  相对 r4 零 diff；未触碰共享 target、设备、runtime、profile、其他树或
  canonical main。

## P1 对应修改（唯一一项）

r4 review：`m1-native-chrome-layout-design-20261004-r4`、profile `oauth` /
`gpt-6.1-sol`、`failed` / `code_failure` / `blocking_findings`、P1×1。

| P1（file:line） | 唯一偏离点/后果 | R5 修复 | 落点 |
| --- | --- | --- | --- |
| `repair-design.md:135`（r4 修改行） | 新增指令要求 stage 与嵌套 videoClip 都使用 `presentedContentRect` 的 root-local left/top；实际 `MainActivity.java:67` 是 `stage.addView(videoClip)`，Android child left/top 相对直接父容器。非零 top 下 clip root 位置变成 `93+93=186dp`，视频错位、被裁且 pointer/caret 不一致 | 保留实际层级并冻结 parent-local fit：stage 是 root 直接子 View，只接收 root-local `presentedContentRect`；videoClip 是 stage 直接子 View，只接收 stage-local aspect-fit/center；SurfaceView 是 clip 直接子 View，只接收 clip-local coded/source geometry；有效可见图像 root rect 由 native parent transform 一次换算 | `repair-design.md` §1.1/§3/§3.1/§4.2/§4.3/§7/§10；`implementation-contracts.md` Rect 语义/Android contract/Successor/Tests；`blackbox-plan.md` Target evidence/L2/L3/L4/L7/matrix/evidence JSON；graph node 2/node 4 descriptions |

## Parent geometry diagram

```text
root (edge-to-edge FrameLayout, drawableWindowRect, root-local dp, no padding)
├─ stage (presentedContentRect, root-local px)
│  ├─ videoClip (visible-source aspect fit + center, stage-local px)
│  │  └─ video/SurfaceView (coded frame + visible-source origin, clip-local px)
│  └─ RemoteImeView (focus anchor 1×1, stage-local px)
└─ WebView (chromeRect | drawableWindowRect, root-local px)
```

有效可见图像 root rect = `stage origin + videoClip fit origin +
visible-source origin × scale`；它不是 typed 控制真源，而是 native owner 从
root-local typed Rect 与当前 frame geometry 一次派生的证据/transform 输入。
`presentedContentRect` 是 stage 的可用容器，aspect fit 可留 letterbox；coded
padding 超出 clip 的部分不可点击。

## 保留语义（未重开）

- root 无 padding、full-window overlay + safe drawer、AdvertisedViewport 不受
  IME/drawer 影响、native displayed-frame 可见性独立于
  inputReady/control/operation、空态/waiting 规则、rotation/Back/cancel/dispose
  状态保持均保留。
- Mac 保持 content points、不乘 backingScaleFactor、`pageCoordinates`/
  `viewPoint` 同一 fit transform；仅明确 VideoSurfaceView 是 content view 直接
  子视图，不新增 Android 式 stage/clip 第二几何层。

## 后继文件/API 契约（冻结给 successor）

- AB-02/03 UI/domain：`packages/client-domain/probe.ts`、
  `packages/ui-plugins/main.tsx`、`probe.css`、`consumer-frame.ts`、
  `browser-consumer.tsx`、相关 tests。只消费 root-local typed projection，不计算
  stage/clip/source geometry；不新增第二输入框。
- AB-04 Android：`MainActivity.java`、`AndroidManifest.xml`、必要时
  `RemoteImeView.java`。唯一 geometry owner：stage root-local、
  videoClip stage-local、SurfaceView clip-local、RemoteImeView stage parent。
  禁止 root `setPadding`、padding compensation、把 stage root-local x/y 再应用到
  clip、从 stage 尺寸申报。
- AB-04 Mac：`main.swift`，必要时 `connection-bridge/src/main.rs`（仅 presentation
  绑定）。单 content view；`updateViewport()` 从 `advertisedViewportRect`
  points 计算；不乘 backingScaleFactor、不从 `videoView.bounds` 反推申报。
- Serial handoff：UI/domain 先做；Android 布局作者独占
  `MainActivity/Manifest/RemoteImeView` 并让出写区；first-connection 作者随后
  才能写 `NetworkSession/RemoteInput/setup`，不得复制 geometry owner。

## 验证与状态

- `dagpipe graph validate <sole graph>` raw exit 0；graph SHA-256
  `ce4973a12a860ad13b6664795357dec968e5919ac38e34a6cceedd0913ff9959`。
- `git diff --check 0e387b3 HEAD`（候选后复核）目标 raw exit 0；产品路径
  `apps/ packages/ scripts/ protocol/ .appsdk/ Cargo.toml Cargo.lock` diff 空。
- L1–L7 BB IDs 保留且全部 `BB_NOT_EXECUTED`；无 executed product PASS。
- 独立 design review 由 controller 后续另起，本 run 不宣称 PASS。

## 保留与清理

- 保留本外置 tree、r1 active docs/graph、r4 review 证据与 r5 notes 供独立
  design review 消费。
- 未删除、未 force 清理；hook 所需 locked `npm ci`/`node_modules` 与 `build:ui`
  生成物若出现，属于 docs hook 的常规依赖/构建产物，保留并如实报告，不触碰
  他人资源。

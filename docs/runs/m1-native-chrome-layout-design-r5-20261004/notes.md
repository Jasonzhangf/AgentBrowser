# Per-node Notes（R5）

任务：只修 r4 独立 review 的一项 P1。保留实际 Android 层级，冻结
`root -> stage -> videoClip -> SurfaceView` 的 parent-local 坐标契约；产品
source、设备、runtime、install、merge、push 均不在本 run 范围。

## Node 0 - revision identity

时间：2026-10-03
节点：Git/base identity
结论：当前 worktree HEAD `0e387b36af08edacb97ad1b8ed0d28b0a0eaf5a6`，tree
`0deb5dca5369c1339867960300b1bef1c362e2c6`，branch
`codex/m1-native-chrome-layout-design-r5-20261004`，clean；与任务给定的 exact r4
candidate 一致。
证据：`git status --short --branch`、`git rev-parse HEAD`、
`git rev-parse HEAD^{tree}`、`git log --oneline --decorate -20`。
输入：fresh origin/main `bc9d67ad037be2c7119bee692f9ad362417ffd05`；r4 exact
candidate `0e387b36af08edacb97ad1b8ed0d28b0a0eaf5a6` / tree
`0deb5dca5369c1339867960300b1bef1c362e2c6`。
环境：外置 Intel playground；产品源码只读；无 Cargo/V8/native/device build。
下一步：读 r4 review 和实际 Android/Mac/UI source trace。

## Node 1 - r4 P1 evidence

时间：2026-10-03
节点：P1 定位
结论：r4 review 为 `failed` / `code_failure` / `blocking_findings`，exit0、
valid JSON、一项 P1。P1 位于
`docs/runs/m1-native-chrome-layout-design-r1-20261003/repair-design.md:135`：
要求 `stage` 与嵌套 `videoClip` 都使用 `presentedContentRect` 的 root-local
left/top；但实际 `MainActivity.java:67` 是 `stage.addView(videoClip)`，Android
child left/top 相对直接父容器。非零 top 偏移会把 stage 偏移重复到 clip，造成
视频错位、裁剪和 pointer/caret 不一致。
证据：`.../m1-native-chrome-layout-design-r4-.../.agent-collab/review/.../status.json`；
`review.final.md` P1×1；`MainActivity.java` L54-58/L60-67/L90-92/L151-155。
输入：r4 candidate `0e387b3` / tree `0deb5dc`；r1 设计/合同/黑盒/图。
环境：只读 review 证据与源码，无运行时复现。
下一步：冻结保留层级与 parent-local 坐标语义。

## Node 2 - source trace

时间：2026-10-03
节点：真实层级、fit 与输入 trace
结论：Android 实际层级为
`layout(root) -> stage(root child) -> videoClip(stage child) -> video(SurfaceView,
clip child)`，且 `remoteIme` 的实际父容器是 `stage`；`webView` 与 `stage` 都是
root 直接子 View。现有 `layoutVideo()` 以 stage 尺寸和 visible source 尺寸算
`scale`，把 `videoClip` 居中放到 stage-local 坐标，把 coded `SurfaceView` 放到
clip-local 左上；`networkTouch()` 使用 `videoClip` 尺寸与 visible source 尺寸
做逆变换，`RemoteImeView` 的输入连接仍由 native stage 父级承载。Mac
`VideoSurfaceView` 是独立 content view，`pageCoordinates()` 与
`viewPoint()` 已共用同一 image/bounds fit；UI 只通过 bridge 发命令和消费
snapshot，不持有 geometry 真源。
证据：`MainActivity.java` L60-92/L123-126/L151-160/L178-192；
`RemoteImeView.java` L1-70；`apps/macos/Sources/main.swift` L574-704/L1404-1414；
`packages/client-domain/probe.ts` L70-120；`packages/ui-plugins/main.tsx` L145-175/L396-403。
输入：exact r4 candidate source tree；r1 活动设计。
环境：只读源码；未构建、未安装、未连网。
下一步：写 parent-space/transform 契约并修正所有旧 root-local 指令。

## Node 3 - parent-space contract freeze

时间：2026-10-03
节点：geometry/parent ownership
结论：冻结保留实际层级。typed projection 中的 Rect 是 Android root-local dp /
Mac content points；stage 是 root 直接子 View，只接收 `presentedContentRect`
root-local frame；videoClip 是 stage 直接子 View，只接收 stage-local
aspect-fit/center；SurfaceView 是 clip 直接子 View，只接收 clip-local
coded/source geometry；RemoteImeView 的实际父容器是 stage。有效可见图像 root
rect = stage origin + clip fit origin + visible-source origin×scale，一次 native
parent-chain 换算；letterbox/coded padding 不可点击。UI 不计算 geometry，也不
在 typed projection 增加 stage/clip 第二套控制真源。
证据：`repair-design.md` §3.1、§4.2、§7；`implementation-contracts.md` Rect
语义/Android contract/Successor；`blackbox-plan.md` Target evidence/L2/L3/L7。
输入：r4 P1 定位、Android/Mac source trace。
环境：只读 product source；无 runtime/device。
下一步：修正 graph 与 docs 后做 graph validate、diff check、readback。

## Node 4 - docs/graph edits

时间：2026-10-03
节点：implementation edit pass
结论：已修正 active r1 repair-design、implementation-contracts、blackbox-plan
与 sole graph 的所有要求 stage/videoClip 同 root-local frame 的语句；新增
parent-space 表、Android/Mac/UI 后续 owner 契约、示例 93+23=116（非 186）与
stage/clip/surface 实际 bounds 黑盒断言。未改产品源码、历史 notes、r4 review
证据或其他 owner 文件。
证据：`git diff`；`rg` 检查不再命中“给 stage/videoClip 设置同一 root-local
bounds”类指令。
输入：Node 3 冻结契约。
环境：docs-only working tree；尚未 commit/hook。
下一步：graph validate、diff check、产品 scope diff、readback。

## Node 5 - docs verification

时间：2026-10-03
节点：graph validate / diff check / scope
结论：`dagpipe graph validate` raw exit0（`valid DAG: m1-native-chrome-layout@2
(5 nodes, 4 edges, 5 waves)`）；graph SHA-256
`ce4973a12a860ad13b6664795357dec968e5919ac38e34a6cceedd0913ff9959`；
`git diff --check` raw exit0；产品 scope diff（apps/packages/scripts/protocol/
.appsdk/Cargo）为空；活动 r1 docs/graph 的 old root-local double-apply 指令已
消除。r5 notes/delivery-summary 已创建。
证据：上述命令输出；`git status --short`；`git diff --name-status`。
输入：R5 docs/graph working tree from 0e387b3。
环境：docs-only；未 build/install/device。
下一步：normal hook commit（docs allowlist）。

## Node 6 - commit / dependency gate

时间：提交时记录
节点：commit
结论：将按 normal hook 提交，不 `--no-verify`、不 `git add .`；fresh worktree
首错预期为缺 locked deps 后按仓库文档执行 `npm ci`。exact commit SHA/tree 在
提交回执中记录，不写回本 commit 以避免自引用。
证据：commit stdout / `git log -1` 回执。
输入：Node 5 验证通过的 docs/graph。
环境：docs allowlist；locked npm ci if needed。
下一步：post-commit identity/scope 复核并收口。

## Node 7 - final deliverable identity

时间：commit 回执后
节点：final identity/readback
结论：工作树 clean、`git diff --check 0e387b3 HEAD` raw exit0、产品 scope diff
空、graph validate/hash 同上、r5 notes 与 delivery-summary 保留。状态
`DESIGN_READY` / `NOT_IMPLEMENTED` / `BB_NOT_EXECUTED`；独立 design review 由
controller 后续执行。
证据：`git rev-parse HEAD`/`HEAD^{tree}`、`git status --short`、
`git diff --check 0e387b3 HEAD`、`git diff --name-only ... product`、graph
validate/hash。
输入：R5 commit。
环境：docs-only tree 保留。
下一步：交付摘要收口；等待 controller。

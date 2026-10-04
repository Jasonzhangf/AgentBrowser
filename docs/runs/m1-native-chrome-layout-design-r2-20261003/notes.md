# Per-node Notes（R2）

任务：修 r1 review 的三项 P1；只改设计文档与既有 graph，产品 source 零 diff。

## Node 0 - input/base identity

时间：2026-10-03
节点：git+input identity
结论：确认 worktree/branch/tree 与任务输入一致；从 fresh origin/main 组合冻结树。
证据：`git status --short --branch`、`git rev-parse HEAD`、`git rev-parse HEAD^{tree}`、`git rev-parse origin/main`、`git ls-remote origin refs/heads/main`
输入：HEAD `d11150fdad7b39daab895b69f9b269abb0b70144`；tree `9cba13e4c4d16c22cea2d5f2afc970a24f26f472`；origin/main `bc9d67ad037be2c7119bee692f9ad362417ffd05`
下一步：读 review final 与三份 controller 合同

## Node 1 - review and source inspection

时间：2026-10-03
节点：P1 定位
结论：三条 P1 各有唯一偏离点。P1#1 `reportViewport()` 从含 IME 的 stage 尺寸申报；P1#2 空态在 `main.tsx .viewer`，`MainActivity.stage` 无渲染器；P1#3 图末端只有泛化 release，无触发/资源/consumer。
证据：`review.final.md`（P1×3）、`MainActivity.java` L54-58/L90-93/L136-142/L307-313、`main.tsx` L396-403、`main.swift` L1465-1475/L1523-1530、`m1-native-chrome-layout.graph.json`
输入：r1 设计 doc + graph、controller `interaction-model.md`/`w1-bound-contract.md`/`blackbox-acceptance.md`
下一步：冻结 R2 语义并改设计

## Node 2 - P1#1 viewport 与 IME 唯一策略

时间：2026-10-03
节点：四矩形分离
结论：引入 `AdvertisedViewportRect`（排除 IME，唯申报）与 `PresentedContentRect`（含 IME，唯呈现）；申报触发=旋转/物理窗口/system bars/chrome 高度；Android `adjustResize` + root padding 只扣 system bars + WindowMetrics 申报；Mac points 不乘 backingScaleFactor。
证据：`repair-design.md` §3/§4、`implementation-contracts.md` viewport/IME 真源 + Android/Mac contract、`blackbox-plan.md` L3/L4
输入：`MainActivity.java` L54-58（`systemBars()|ime()` padding）、Mac L1525（`backingScaleFactor`）
下一步：P1#2

## Node 3 - P1#2 空态渲染 owner

时间：2026-10-03
节点：单 WebView 双容器
结论：复用同一 Cordis viewer；未具正式 native 呈现状态时 `webRect=windowFrameRect`（含顶部 chrome，空态可见），具状态后 `webRect=chromeRect`、原生视频填 `ContentRect`；`visibleContent` 由既有 `inputReady`+session/viewport 一致判定，不从 firstframe/日志推断；不新增 native 空态或第二输入框。
证据：`repair-design.md` §5、`implementation-contracts.md` Exact typed UI control、`blackbox-plan.md` L1/L2
输入：`main.tsx` L396-403、`NetworkSession.inputReady()` L42-45、main.rs L1269-1282
下一步：P1#3

## Node 4 - P1#3 生命周期与释放

时间：2026-10-03
节点：overlay/失败/取消/释放闭环
结论：冻结 `set_presentation({id,state})`、`applied+revision` 与 `rejected` 的 `NO_AUTHORITY`/`ACTIVITY_NOT_RESUMED`/`GEOMETRY_UNAVAILABLE`；列真实资源与唯一释放 owner（insets listener/layout listener/WebView/RemoteImeView/Mac Timer）；drawer close 不重建永久资源；全部结果落既有 SESE 图唯一出口 `layout-resource-terminal-result`，不新增平行图。
证据：`repair-design.md` §6、`implementation-contracts.md` Overlay 生命周期、`blackbox-plan.md` L5/L6、graph node 5/edge 4
输入：`RemoteImeView.deactivate()` L65-70、main.swift Timer L1502、`MainActivity.onDestroy` L354-361
下一步：改 graph 并校验

## Node 5 - graph and validation

时间：2026-10-03
节点：`m1-native-chrome-layout` graph v2
结论：沿用既有 SESE 图，节点改名为四矩形/呈现状态语义，末端改 `layout-resource-terminal-result`，补 description；仍 5 节点 4 边 5 wave。
证据：`dagpipe graph validate ...` 原始 stdout `valid DAG: m1-native-chrome-layout@2 (5 nodes, 4 edges, 5 waves)`，exit 0；graph sha256 `145ef5886fb379b979a8dbd66fb4c804b9d43d3be867c7a42ccb8e6d8e7d53ac`
输入：`docs/runs/m1-native-chrome-layout-design-r1-20261003/dagpipe/graphs/m1-native-chrome-layout.graph.json`
下一步：docs-only 检查 + hook 提交

## Node 6 - verification and deliverable

时间：2026-10-03
节点：docs-only verification
结论：`git diff --check` exit 0；产品 source 零 diff（只改 docs/runs r1 四份+r2 新增）。未运行产品构建/测试冒充实现。
证据：`git diff --check`、`git diff --name-only`、`git status --short`
输入：HEAD `d11150f` 冻结树
下一步：正常 hook commit，输出设计 SHA/tree 供独立 review

## Node 7 - commit hook evidence

时间：2026-10-03
节点：原 hook
结论：pre-commit `npm run verify:local` 结果见交付摘要；以明确 docs allowlist 提交，不 `git add .`，不绕 hook。
证据：commit stdout / hook log
输入：本 run docs-only diff
下一步：交付摘要收口


# R2 交付摘要：修 r1 三项 P1

状态：`DESIGN_READY` / `NOT_IMPLEMENTED` / `UNVERIFIED`（设计交付，未实现、未运行产品黑盒）。

## 范围与身份

- 任务：只修 r1 review 的三项 P1；允许改本树 r1 设计目录四份文档与既有 graph；新增本 r2 notes/摘要。
- 产品 `apps/ packages/ scripts/ SDK/ wire` 全只读。
- 输入：`origin/main bc9d67ad037be2c7119bee692f9ad362417ffd05`；冻结树 `d11150fdad7b39daab895b69f9b269abb0b70144` / tree `9cba13e4c4d16c22cea2d5f2afc970a24f26f472`。
- 分支：`codex/m1-native-chrome-layout-design-r2-20261003`。

## P1 消除对照

| R1 P1 | 唯一偏离点 | R2 改动 | 落点 |
| --- | --- | --- | --- |
| IME 呈现/申报矛盾 | `MainActivity.reportViewport()` 从含 IME 的 stage 尺寸申报 | 拆 `AdvertisedViewportRect`（排除 IME，唯申报）与 `PresentedContentRect`（含 IME，唯呈现）；申报触发只留旋转/物理窗口/system bars/chrome 高度；Android `adjustResize`+root padding 只扣 system bars+`WindowMetrics`；Mac points 不乘 `backingScaleFactor` | `repair-design.md` §3/§4；`implementation-contracts.md` viewport/IME + Android/Mac contract；`blackbox-plan.md` L3/L4 |
| 空态缺渲染 owner | 空态在 `main.tsx .viewer`，`MainActivity.stage` 无渲染器 | 同一 WebView 双容器：无正式 native 呈现状态 `webRect=windowFrameRect`（含顶部 chrome，空态可见）；具状态后 `webRect=chromeRect`、原生视频填内容区；`visibleContent` 由既有 `inputReady`+session/viewport 一致判定 | `repair-design.md` §5；`implementation-contracts.md` Exact typed UI control；`blackbox-plan.md` L1/L2 |
| 失败取消释放不可验 | 只有泛化 release，无触发/资源/consumer | 冻结 `set_presentation({id,state})`、`applied+revision`、`rejected` 的 3 个 reason；列真实资源 + 唯一释放 owner；drawer close 不重建永久资源；全落既有 SESE 末端 `layout-resource-terminal-result` | `repair-design.md` §6；`implementation-contracts.md` Overlay 生命周期；`blackbox-plan.md` L5/L6；graph node 5 |

## 已验证证据（设计适用）

- graph：`dagpipe graph validate docs/runs/m1-native-chrome-layout-design-r1-20261003/dagpipe/graphs/m1-native-chrome-layout.graph.json` 原始 exit 0，stdout `valid DAG: m1-native-chrome-layout@2 (5 nodes, 4 edges, 5 waves)`；graph sha256 `145ef5886fb379b979a8dbd66fb4c804b9d43d3be867c7a42ccb8e6d8e7d53ac`。
- `git diff --check` exit 0。
- pre-commit hook `npm run verify:local` exit 0：typecheck + 21 tests + build:ui 全通过。
- 本 commit 对父 `d11150f` 的产品路径 diff 空（`git diff --name-only HEAD~1 HEAD -- ':!docs/**'` 为空）。

## 明确未做（不冒充）

- 未改任何产品 source；未实现四矩形、`set_presentation`、空态容器或资源释放。
- 未运行产品构建/测试、未安装、未连 Host、未跑真机/模拟器黑盒。
- 未启动 child/Collab/goal/订阅/review，未 resume/fork。

## 清理与保留

- 本 run 仅新增/修改文档与 graph；无临时文件、进程、forward 需回收。
- tree 保留供新独立 review；独立 PASS 后才写产品。

## 下一步

独立 review 校验（新 candidtate SHA/tree 由提交回执给出）。PASS 后主控按序列交接 AB-04 Android/Mac + AB-02/03 UI/domain 实施。

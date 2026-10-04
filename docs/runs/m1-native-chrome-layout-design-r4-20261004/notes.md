# Per-node Notes（R4）

任务：只修 r3 独立 review 的一项 code_failure P1；产品 source 只读，不审核他人工作，不执行安装/设备/Host/Media。

## Node 0 - revision identity

时间：2026-10-03
节点：Git/base identity
结论：当前 worktree HEAD `d9d27236e69bc5bf01dcc50d62cf5569c838ca42`，tree `752c53524aff4df13ec3e1fde0b0d05bd09b0ce5`，branch `codex/m1-native-chrome-layout-design-r4-20261004`，clean；与任务给定 r3 exact candidate 一致。
证据：`git status --short --branch`、`git rev-parse HEAD`、`git rev-parse HEAD^{tree}`、`git log -3 --oneline --decorate`
输入：fresh origin/main `bc9d67ad037be2c7119bee692f9ad362417ffd05`；冻结树 `752c53524aff4df13ec3e1fde0b0d05bd09b0ce5`
下一步：读 exact r3 review 与 r1 设计/合同/黑盒/图

## Node 1 - P1 evidence and source boundary

时间：2026-10-03
节点：P1 定位
结论：r3 review 为 `failed` / `code_failure` / `blocking_findings`，exit0/valid JSON/one P1。唯一 P1 在 `implementation-contracts.md` L55：edge-to-edge root + root `systemBars` padding + 已扣 bars 的 typed Rect 直接布局，导致非零 bars 下 child bounds 二次偏移；repair-design 也保留同一矛盾。
证据：`.../r3/.agent-collab/review/.../status.json`、`review.final.md` P1×1；`MainActivity.java` L51-58/L123-126/L136-142；v1 Android baseline XML `01-launch-window.xml`/`02-drawer-window.xml`
输入：r3 candidate d9d2723 / tree 752c535；r1 design/contract/blackbox/graph
下一步：冻结唯一修复方向并写 r4 notes

## Node 2 - exact geometry contract

时间：2026-10-03
节点：root-local one application
结论：冻结 `drawableWindowRect` 为 edge-to-edge `FrameLayout` root 的完整本地坐标，root 不设置任何 `systemBars` padding；`safeContentRect` 只扣 bars 一次；`chromeRect`/`advertisedViewportRect`/`presentedContentRect`/`webRect` 从同一 root-local origin 和显式单位派生并直接赋给 child bounds；禁止任何 root-padding compensation。全窗 overlay/backdrop 覆盖 `drawableWindowRect`；drawer 内容单独在 safe 区域排版。
证据：`repair-design.md` §2-§4/§7/§10、`implementation-contracts.md` Exact typed UI control/viewport+IME 真源/Android contract/UI consumer binding/Tests、`blackbox-plan.md` Target evidence/L1-L7
输入：Android source L51-58/L123-126、Mac source L1465-1477/L1523-1530、UI source `main.tsx` L396-403/`probe.css` L95-100
下一步：更新 graph 描述并校验

## Node 3 - preserved repairs and scope

时间：2026-10-03
节点：non-goals and preserved semantics
结论：不重开 unrelated design。保留 native displayed-frame/geometry match 决定 visibleContent、inputReady/control/commandPending 不决定可见性、observe/waiting/Agent operation 保持有效视频、connected-without-frame 为空态、UI 只消费 native projection、本地 drawer 不要求远端写权限、AdvertisedViewport 排除 IME、PresentedRect 随 IME 变化且 pointer/caret 同逆变换、drawer/keyboard 不 reload/state loss/viewport resize、Back/close/cancel/dispose 资源终点不变。
证据：`repair-design.md` §3/§4/§5/§6、`implementation-contracts.md` Exact typed UI control/Overlay 生命周期、`blackbox-plan.md` L2/L3/L5/L6
输入：r3 candidate 全部已有 repaired semantics
下一步：docs/graph verification

## Node 4 - graph and docs verification

时间：2026-10-03
节点：graph validate / diff check
结论：docs/graph working tree 通过全部适用检查：`dagpipe graph validate` 原始 exit0（`valid DAG: m1-native-chrome-layout@2 (5 nodes, 4 edges, 5 waves)`）；graph SHA256 `951138282d1fb50f0e89ed41952da94ab3a15e588535a63bed03f5500b843e2a`；`git diff --check d9d2723` 原始 exit0；产品 scope diff（apps/packages/scripts/protocol/.appsdk/Cargo.toml/Cargo.lock）为空。活动契约中无残留的 root `systemBars` padding 指令。
证据：`dagpipe graph validate docs/runs/m1-native-chrome-layout-design-r1-20261003/dagpipe/graphs/m1-native-chrome-layout.graph.json`（exit0）、`shasum -a 256 <graph>`、`git diff --check d9d27236e69bc5bf01dcc50d62cf5569c838ca42`（exit0）、`git diff --name-only d9d2723 -- apps packages scripts protocol .appsdk Cargo.toml Cargo.lock`（空）、`git diff --name-status d9d2723`（仅 4 个 r1 docs/graph + r4 dir）
输入：R4 docs/graph working tree from d9d2723
下一步：normal hook commit（docs allowlist）

## Node 5 - normal hook commit

时间：2026-10-03
节点：commit / dependency gate
结论：docs/graph allowlist 提交成功，hooks 未绕过。首次 `git commit` 在 fresh worktree 因缺依赖失败，首错为 `Cannot find module 'node:test'` / `'react'` / `'@cordisjs/core'`；按仓库文档执行 locked `npm ci`（added 17 packages、audited 18、0 vulnerabilities）后，同一正常 hook `npm run verify:local` exit0：typecheck + 21 tests（`# pass 21 # fail 0`）+ `build:ui` 全通过。设计提交为单一 docs/graph commit，6 files changed，188 insertions(+)、34 deletions(-)；exact SHA/tree 见提交回执与交付摘要。
证据：首次 `git commit` 原始 TS2307 `node:test`/`react`/`@cordisjs/core`（保留）；`npm ci` stdout；`npm run verify:local` TAP `# pass 21 # fail 0`、`build:ui` exit0；`git log -1 --format='%H %T %s'`。
输入：R4 docs-only allowlist
下一步：post-commit identity and scope 复核

## Node 6 - final deliverable identity

时间：2026-10-03
节点：commit/tree/product scope
结论：r4 设计提交 base `d9d27236e69bc5bf01dcc50d62cf5569c838ca42`。工作树 clean；`git diff --check d9d2723 HEAD` raw exit0；产品 scope diff（apps/packages/scripts/protocol/.appsdk/Cargo）为空；改动仅 6 个 docs/graph 文件。graph validate raw exit0，graph SHA256 `951138282d1fb50f0e89ed41952da94ab3a15e588535a63bed03f5500b843e2a`。状态 `DESIGN_READY` / `NOT_IMPLEMENTED` / `BB_NOT_EXECUTED`；保留本 tree 供独立 design review。自建且 ignored 的 `node_modules/`、`apps/android/app/build/` 保留在本 tree（hooks 需要，且沙箱拒绝 `rm -rf` 删除命令），未触碰共享 base/他人资源/controller 证据。exact commit SHA/tree 由提交回执给出（不写回本 commit 以避免自引用）。
证据：`git rev-parse HEAD`/`HEAD^{tree}`（提交后）、`git status --short`（空）、`git diff --check d9d2723 HEAD`（exit0）、`git diff --name-only d9d2723 HEAD -- apps packages scripts protocol .appsdk Cargo.toml Cargo.lock`（空）、`dagpipe graph validate`（exit0）、`shasum -a 256 <graph>`
输入：R4 commit（单一 docs/graph allowlist）
下一步：交付摘要收口；等待 controller 独立 design review

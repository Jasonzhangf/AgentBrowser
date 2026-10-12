# Per-node Notes（R3）

任务：只修 r2 独立 review 三项 P1；产品 source 只读，不重复全量规划。

## Node 0 - revision identity

时间：2026-10-03
节点：Git/base identity
结论：当前 HEAD `e3139981ff7fba98e72081049993e81a58b0f6f6`，tree `b543ff086455c8c40d1e8df70392daf72aad695a`，等于任务给入的 r2 候选；branch `codex/m1-native-chrome-layout-design-r3-20261004` 从 fresh origin/main 直接续接，不重复组合。
证据：`git rev-parse HEAD`、`git rev-parse HEAD^{tree}`、`git merge-base --is-ancestor e3139981 HEAD` exit 0、`git status --short --branch`
输入：origin/main `bc9d67ad037be2c7119bee692f9ad362417ffd05`；冻结树 `d11150fdad7b39daab895b69f9b269abb0b70144` / `9cba13e4c4d16c22cea2d5f2afc970a24f26f472`
下一步：读 r2 final 与三处受影响源码边界

## Node 1 - P1 evidence and source boundary

时间：2026-10-03
节点：P1 定位
结论：r2 final 的三项 P1 均由实际 source 复核：Android/Mac `inputReady`/`input_ready` 含 control/operation 资格，不能决定视频可见；`main.tsx` 的 `data-ready` 是 `connected`，`probe.css` 在该值下隐藏 viewer surface；Android 用 `stage` 申报且 r2 混用 bars 裁过窗口与全显示 bounds，Mac `updateViewport` 乘 `backingScaleFactor`。
证据：`.../r2/.agent-collab/review/.../review.final.md` P1×3；`NetworkSession.java` L42-45、`MainActivity.java` L54-58/L307-313、`main.rs` L1269-1282、`main.swift` L1523-1530、`main.tsx` L396、`probe.css` L97
输入：本树 r1 设计真源 + graph、controller `native-layout-r2-design-acceptance.md` 冻结方向
下一步：只改设计/图，不再扩展其它历史合同

## Node 2 - P1#1 presentation visibility

时间：2026-10-03
节点：presentation 与 input eligibility 分离
结论：冻结 native displayed-frame identity + `presentationViewport` + effective session/document/viewport match 作为唯一视频可见源；`inputReady`/`control_mode`/`commandPending`/`operation_running` 只决定写/IME。observe、waiting、交还控制、Agent operation pending 仍有有效画面时保持 `native-video`；无有效显示/几何失配才 `webview`。没有引入远端 writer 控制新状态，也不为历史像素加缓存。
证据：`repair-design.md` §3/§5.1/§5.2/§5.3/§9、`implementation-contracts.md` Exact typed UI control/UI consumer binding、`blackbox-plan.md` L2
输入：`NetworkSession.displayed` L31/L305/L216-219、Mac `session.displayed`/frame identity main.rs L997-1007/L922-932
下一步：P1#2 UI binding

## Node 3 - P1#2 UI downstream binding

时间：2026-10-03
节点：viewer ready/empty owner
结论：同一 `.viewer`/`.viewer-empty` 复用；`main.tsx` 后需把 `data-ready` 从 `connected` 改为 native layout projection 的 `visibleContent==='native-video'`（displayed identity + geometry match），`probe.css` 仍只在 ready 时隐藏 surface；native-video 容器保持透明。`inputReady` 继续只服务写权限，不承载可见性；不新增 renderer/DOM/第二输入框。
证据：`repair-design.md` §5.1/§5.4、`implementation-contracts.md` UI consumer binding、`blackbox-plan.md` L1/L2
输入：`main.tsx` L396-403、`probe.css` L95-100
下一步：P1#3 geometry

## Node 4 - P1#3 geometry units and origin

时间：2026-10-03
节点：drawable window 与 safe content 分离
结论：冻结 Android 单一 edge-to-edge root 逻辑 dp；`drawableWindowRect` 是实际 app drawable bounds 且 root 不重复扣 bars；`safeContentRect` 扣 bars 一次；`advertisedViewportRect`/`chromeRect` 从 safe rect 派生，`presentedContentRect` 另扣 IME 且不重复；pointer/caret/selection/composition 共用同一矩形逆变换。截图断言必须实测 window offset/density/insets 后换算，不能固定断言未量测全显示 `[0,0][1080,2400]`。Mac 保持 points，不乘 `backingScaleFactor`。
证据：`repair-design.md` §3/§4.3/§4.4/§7、`implementation-contracts.md` viewport/IME 真源 + Mac contract、`blackbox-plan.md` Target evidence/L1/L4
输入：`MainActivity.java` L54-58/L130/L139、`main.swift` L1523-1530、Android launcher baseline XML
下一步：P1#3 lifecycle/permission + graph

## Node 5 - P1#3 lifecycle and local permission

时间：2026-10-03
节点：overlay 生命周期闭环
结论：本地 `set_presentation`/抽屉不要求远端 writer/control 权限；`NO_AUTHORITY` 仅指 native presentation 自身无权限。保留 `id`/applied/rejected/released 唯一出口、三个 reason、Back 先 overlay 后 drawer、cancel/dispose/listener/timer/input 恢复；drawer close 不重建永久资源；Host writer authority 只在远端写 input 边界。
证据：`repair-design.md` §6、`implementation-contracts.md` Overlay 生命周期、`blackbox-plan.md` L5/L6、graph terminal result
下一步：graph validate + docs checks

## Node 6 - graph and docs verification

时间：2026-10-03
节点：graph validate / diff check
结论：`dagpipe graph validate` raw exit 0；graph SHA-256 `90aecdfc0fd27b3be52a13fe48ef9fb89f65e3fe7626e81481eb7ede214a4367`；`git diff --check e313998` exit 0；相对 e313998 的非 `docs/**` diff 为空。
证据：`valid DAG: m1-native-chrome-layout@2 (5 nodes, 4 edges, 5 waves)` + `graph_validate_exit=0`；`shasum -a 256 ...`；`git diff --check e3139981...` exit 0；`git diff --name-only e3139981... -- . ':(exclude)docs/**'` 空
输入：R3 docs/graph 工作树相对 e313998
下一步：正常 docs allowlist commit

## Node 7 - normal hook commit

时间：2026-10-03
节点：pre-commit / dependency gate
结论：首次 `npm run verify:local` 在 fresh worktree 失败，首错为缺少 `node_modules` 导致的 `Cannot find module 'node:test'`、`react`、`@cordisjs/core`；按仓库文档执行 tracked lockfile `npm ci` 后，同一 hook exit 0：typecheck、21 tests、`build:ui` 全通过。hooks 未禁用，未使用 `--no-verify` 或任何绕过。
证据：`npm ci` added 17 packages、audited 18、0 vulnerabilities；`npm run verify:local` TAP `# pass 21 # fail 0`、`build:ui` exit 0；`.githooks/pre-commit` 仅执行 branch guard 与 `npm run verify:local`。
输入：`package-lock.json` unchanged；Node v22.22.2、npm 10.9.7；staged docs-only candidate at `e3139981ff7fba98e72081049993e81a58b0f6f6`。
下一步：显式 allowlist commit，输出直接 Git SHA/tree；清理本任务生成的 `node_modules` 与 ignored generated UI assets，保留本 tree 供独立 review。

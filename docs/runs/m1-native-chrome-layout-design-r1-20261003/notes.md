# Per-node Notes

## Node 0 - input/base identity

时间：2026-10-03
节点：git+input identity
结论：确认工作 tree/branch/tree 与任务输入一致
证据：`git status --short --branch`, `git rev-parse HEAD`, `git rev-parse HEAD^{tree}`
输入：HEAD `d0a88eada5748bbb22f20e7ff48b67a282b3abb0`；tree `57fda4c681ce91b08784679f3c54bc1b024e137b`
下一步：读项目 AGENTS 与 input docs

## Node 1 - source inspection

时间：2026-10-03
节点：source/XML inspection
结论：首次偏离在 `MainActivity.layoutChrome`；WebView 被当 370/136dp 面板；Mac 仍 split 双 pane
证据：`apps/android/.../MainActivity.java`, `apps/macos/Sources/main.swift`, launcher evidence XML
下一步：决定唯一布局方案

## Node 2 - design decision

时间：2026-10-03
节点：minimal unique layout
结论：薄 chrome + full-window overlay drawer；typed `set_presentation` intent；原生 owner 拥有 rects
证据：见 `repair-design.md`
下一步：落 graph + contracts + blackbox

## Node 3 - graph and validation

时间：2026-10-03
节点：`m1-native-chrome-layout` draft graph
结论：DAG SESE；`dagpipe graph validate` 已通过
证据：`dagpipe/graphs/m1-native-chrome-layout.graph.json`
下一步：docs files commit

## Node 4 - verification and deliverable

时间：2026-10-03
节点：docs-only verification
结论：docs diff only；product source untouched；graph validate exit 0；`git diff --check` exit 0
证据：run `git diff --check`、`dagpipe graph validate docs/runs/m1-native-chrome-layout-design-r1-20261003/dagpipe/graphs/m1-native-chrome-layout.graph.json`
输入：HEAD `d0a88eada5748bbb22f20e7ff48b67a282b3abb0` 设计前基线
下一步：提交 design SHA for review

## Node 5 - commit hook evidence

时间：2026-10-03
节点：原 hook
结论：pre-commit `npm run verify:local` PASS；typecheck / 21 tests / build:ui 全部 exit 0
证据：首次 commit 原始错误保留：fresh worktree 缺少依赖导致 TS2307；随后 `npm ci` 后重试同一 hook 通过
输入：设计提交 `1cfcbb5c120f329cd6b95dbcd039c348b3def4b5`
下一步：独立 design review

## Node 6 - exact design SHA

时间：2026-10-03
节点：commit + tree + source diff
结论：设计提交 `1cfcbb5c120f329cd6b95dbcd039c348b3def4b5`，tree `dfa783f6699393130d05bd51c276accfce45df90`；产品 source diff 0
证据：`git show --stat HEAD`、`git diff --name-only HEAD~1..HEAD`、`git diff --name-only -- ':!docs/runs/.../**'`
下一步：主控独立 design PASS 后进入产品编码

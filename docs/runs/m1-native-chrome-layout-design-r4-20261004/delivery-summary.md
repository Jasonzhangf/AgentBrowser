# R4 交付摘要：定向修 R3 一项 P1

状态：`DESIGN_READY` / `NOT_IMPLEMENTED` / `BB_NOT_EXECUTED`。只完成设计与 graph 返修，未编码、未安装、未连网、未跑设备/Host/Media 黑盒。

## 范围与身份

- 任务：只修 r3 独立 review 的唯一一项 code_failure P1；产品 source 只读，不重开无关设计。
- 分支：`codex/m1-native-chrome-layout-design-r4-20261004`。
- 基线：exact r3 `d9d27236e69bc5bf01dcc50d62cf5569c838ca42` / tree `752c53524aff4df13ec3e1fde0b0d05bd09b0ce5`，从 r3 直接继续，不重复组合；继承冻结树 `d11150fdad7b39daab895b69f9b269abb0b70144` / `9cba13e4c4d16c22cea2d5f2afc970a24f26f472`。
- 输入 origin/main：`bc9d67ad037be2c7119bee692f9ad362417ffd05`。
- r4 候选：单一 docs/graph commit（exact SHA/tree 见提交回执；不写回自身以避免自引用）。
- 产品 `apps/ packages/ scripts/ protocol/ .appsdk/ Cargo.toml Cargo.lock` 相对 `d9d2723` 零 diff；未触碰共享 target、设备、runtime、profile、其他树或 canonical main。

## P1 对应修改（唯一一项）

r3 review：profile `oauth` / `gpt-6.1-sol`、exit0、valid JSON、`code_failure` / `blocking_findings`、P1×1。

| r3 P1（file:line） | 唯一偏离点/后果 | R4 修复 | 落点 |
| --- | --- | --- | --- |
| `implementation-contracts.md:55`（并引 `repair-design.md` 根 padding 行） | 同时要求 edge-to-edge root + root `systemBars` padding，又把已扣 bars 的 `safeContentRect`/`chromeRect`/`drawableWindowRect` 直接当 FrameLayout 子 View 矩形应用；非零 inset 下 child bounds 二次偏移，全窗 overlay 也无法覆盖 `drawableWindowRect` | 统一为无 padding 的 edge-to-edge `FrameLayout`：root 只读取 `systemBars()`/`ime()` 供派生，`drawableWindowRect` 即 root 完整本地坐标；`safeContentRect` 只扣 bars 一次；`chromeRect`/`advertisedViewportRect`/`presentedContentRect`/`webRect` 从同一 root-local origin 与显式单位派生并直接赋给 child bounds；删除所有 root padding / padding-compensation 指令 | `repair-design.md` §2-§4/§7/§10、`implementation-contracts.md` Exact typed viewport+IME / Android contract / UI consumer binding / Tests、`blackbox-plan.md` Target evidence/L1-L7、graph node 2 description |

契约要点（同一几何应用链）：`drawableWindowRect` = root 完整本地坐标 → `safeContentRect` = 扣 systemBars 一次 → `advertisedViewportRect`/`chromeRect`/`presentedContentRect`/`webRect` 由 `safeContentRect` 派生 → 全部以同一 root-local origin/显式单位直接赋给 child bounds。全窗 overlay/backdrop 覆盖 `drawableWindowRect`；drawer 内容单独避开系统栏，不经 root padding 二次实现。Android dp 与截图 px/density/offset 换算、Mac AppKit points 均显式声明。

## 保留语义（未重开）

- 可见性由 native 实际 displayed-frame 身份 + geometry match 决定，不由 `inputReady`/`control_mode`/`commandPending`/`operation_running` 决定；observe/waiting/Agent operation 保持有效视频可见；connected 但无有效 displayed frame 显示空态。
- UI 只消费 native projection；本地 drawer 不要求远端 writer 权限；`AdvertisedViewportRect` 排除 IME，`PresentedContentRect` 随 IME 收缩，pointer/caret/selection/composition 共用同一逆变换；开 drawer/键盘不 reload、不丢 state、不 resize source viewport。
- Back/close/cancel/dispose 资源终点与唯一释放 owner 不变。

## 后继文件/API 契约（冻结给 successor）

精确文件与 API owner 表见 `implementation-contracts.md` §「Successor ownership and exact APIs」：

- AB-02/03 UI/domain：`packages/client-domain/probe.ts`（`set_presentation({id,state})`、只读 `layout` 投影与 snapshot 校验）、`packages/ui-plugins/main.tsx`、`probe.css`。禁止自算 displayed-frame 匹配、从 `connected`/`inputReady` 决定空态、制造第二输入框。
- AB-04 Android：`apps/android/app/src/main/java/com/agentbrowser/probe/MainActivity.java`、`apps/android/app/src/main/AndroidManifest.xml`、必要时 `RemoteImeView.java`。owned：root = 无 padding edge-to-edge `FrameLayout`、insets 只读取、六矩形派生与 root-local bounds 应用、`set_presentation` 主线程 applied/rejected、`adjustResize`。禁止：root `setPadding`、padding compensation、从 stage 尺寸申报、改 Host wire。
- AB-04 Mac：`apps/macos/Sources/main.swift`、必要时 `connection-bridge/src/main.rs`（仅 presentation 绑定）。owned：单 content view、按 root-local points 应用 frame、`updateViewport()` 从 `advertisedViewportRect` points 计算。禁止：乘 `backingScaleFactor`、从 `videoView.bounds` 反推、绕过 bridge。

## 实现回归期望（未来 installed-product 黑盒，本 run 未执行）

非零 top/bottom bars 下：实际 `chromeRect`/`presentedContentRect`/`webRect` child bounds 等于 typed root-local Rect；video/pointer/caret 映射匹配；overlay 覆盖完整 `drawableWindowRect` 而 drawer 内容停在安全区内；IME/rotation/Back 保持 source viewport/state 并释放资源。复用既有 L1–L7 BB reference（`blackbox-plan.md` 的 L1–L7 nonzero bars matrix），全部 `BB_NOT_EXECUTED`。

## 已执行验证（docs 适用）

- `dagpipe graph validate docs/runs/m1-native-chrome-layout-design-r1-20261003/dagpipe/graphs/m1-native-chrome-layout.graph.json`：raw exit 0，stdout `valid DAG: m1-native-chrome-layout@2 (5 nodes, 4 edges, 5 waves)`。
- graph SHA-256：`951138282d1fb50f0e89ed41952da94ab3a15e588535a63bed03f5500b843e2a`。
- `git diff --check d9d27236e69bc5bf01dcc50d62cf5569c838ca42 HEAD`：raw exit 0。
- `git diff --name-only d9d27236e69bc5bf01dcc50d62cf5569c838ca42 HEAD -- apps packages scripts protocol .appsdk Cargo.toml Cargo.lock`：空。
- 活动契约无残留 root `systemBars` padding 指令（仅保留禁止/删除说明）。
- 提交走正常 hook（docs/graph allowlist，不 `git add .`、不 `--no-verify`）：fresh worktree 首错为 `Cannot find module 'node:test'` / `'react'` / `'@cordisjs/core'`（保留），按仓库文档执行 locked `npm ci`（17 packages、0 vulnerabilities）后，同一 `npm run verify:local` exit0：typecheck、21 tests（`# pass 21 # fail 0`）、`build:ui`。

## 未执行 / 未证明

- 未实现任何 geometry 派生、displayed identity、`set_presentation`、UI ready 绑定或资源释放。
- 未运行产品 build/install/restart、未连 Host/runtime、未跑 emulator/真机/Media 黑盒；L1–L7 及 §「实现回归期望」全部 `BB_NOT_EXECUTED`。
- 本 docs 检查与 graph 校验不等于独立 design review PASS；controller 另起独立 design review。

## 保留与清理

- 保留本外置 tree 与全部 source/review 证据供独立 design review/consumer 消费；未改历史 r1/r2/r3 notes 或 controller 证据。
- 本 run 仅产生文档与 graph 修改；提交的 tree 不含任何生成物。自建且 ignored 的 `node_modules/`（fresh `npm ci`）与 `apps/android/app/build/`（`build:ui`）保留在本 tree 中：hooks 需要它们，且沙箱拒绝 `rm -rf` 删除命令，故未强删。未触碰共享 target、设备、runtime、controller 证据、他人缓存或其他树。

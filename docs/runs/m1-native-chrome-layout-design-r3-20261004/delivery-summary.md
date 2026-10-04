# R3 交付摘要：定向修 R2 三项 P1

状态：`DESIGN_FIX_READY` / `NOT_IMPLEMENTED` / `UNVERIFIED`。只完成设计与图返修，未编码、未安装、未连网、未跑设备黑盒。

## 范围与身份

- 任务：只修 r2 独立 review 三项 P1；从 `e3139981ff7fba98e72081049993e81a58b0f6f6` / tree `b543ff086455c8c40d1e8df70392daf72aad695a` 续接。
- 输入：origin/main `bc9d67ad037be2c7119bee692f9ad362417ffd05`；冻结继承树 `d11150fdad7b39daab895b69f9b269abb0b70144` / `9cba13e4c4d16c22cea2d5f2afc970a24f26f472`。
- 产品 `apps/ packages/ scripts/ protocol/ .appsdk/` 相对 e313998 零 diff；未触碰共享 target、设备、runtime 或其他树。

## 三 P1 对应修改

| P1 | R3 修改 | 落点 |
| --- | --- | --- |
| 可见性错误依赖 input/control | 冻结 native `displayedFrame` + `presentationViewport` + effective session/document/viewport/geometry 匹配；`inputReady`/`control_mode`/`commandPending`/`operation_running` 只决定写入/IME。observe、waiting、交还控制、operation pending 有有效画面仍 `native-video` | `repair-design.md` §3/§5.1-§5.3；`implementation-contracts.md` typed snapshot/UI binding；`blackbox-plan.md` L2；graph node 3/4 |
| 下游 UI 仍由 connected 隐藏空态 | `main.tsx` 后继把 `data-ready` 改为消费 native `visibleContent==='native-video'`；`probe.css` 只在该正式 ready 时隐藏 `.viewer-surface`；复用 `.viewer-empty`，native-video 保持透明，不复制 renderer/DOM/第二输入框 | `repair-design.md` §5.1/§5.4；`implementation-contracts.md` UI consumer binding；`blackbox-plan.md` L1/L2 |
| 几何 origin/单位混用 | 分离 `drawableWindowRect` 与 `safeContentRect`：Android 单一 edge-to-edge root 逻辑 dp，root 不重复扣 bars，safe rect 扣一次，advertised/chrome 从 safe rect 派生，presented 另扣 IME；截图断言带实际 offset/density/insets 换算；Mac 保持 points，不乘 `backingScaleFactor` | `repair-design.md` §3/§4.3/§4.4/§7；`implementation-contracts.md` viewport/IME；`blackbox-plan.md` Target evidence/L1/L4；graph node 2 |

附：本地 `set_presentation`/抽屉不要求远端 writer/control 权限；`NO_AUTHORITY` 仅指 native presentation 自身。`id`/applied/rejected/released 唯一出口、Back 先 overlay 后 drawer、cancel/dispose/listener/timer/input 恢复与唯一释放 owner 均保留。

## 已执行验证

- `dagpipe graph validate docs/runs/m1-native-chrome-layout-design-r1-20261003/dagpipe/graphs/m1-native-chrome-layout.graph.json`：raw exit 0，输出 `valid DAG: m1-native-chrome-layout@2 (5 nodes, 4 edges, 5 waves)`。
- graph SHA-256：`90aecdfc0fd27b3be52a13fe48ef9fb89f65e3fe7626e81481eb7ede214a4367`。
- `git diff --check e3139981ff7fba98e72081049993e81a58b0f6f6`：exit 0。
- 相对 e313998 的产品路径 diff（`git diff --name-only e3139981... -- . ':(exclude)docs/**'`）为空。
- fresh worktree 首次 hook 因缺少 locked dependencies 失败；按仓库文档执行 `npm ci` 后，正常 `npm run verify:local` exit 0：typecheck、21 tests、`build:ui`。hooks 保持启用，未使用 `--no-verify` 或任何绕过。

## 未执行 / 未证明

- 未实现任何 geometry 投影、displayed identity、`set_presentation`、UI ready 绑定、IME/geometry 或释放逻辑。
- 未运行产品 build/test、未安装、未启动 Host、未跑 emulator/真机黑盒；`blackbox-plan.md` 中 BB/L 用例全部 `NOT_EXECUTED`。
- 设计与图校验不等于运行时 PASS，也不代表独立设计 review 通过。

## 保留与清理

- 保留本外置树供独立设计 review/消费；旧 r2 source/review 证据未改动。
- 本 run 仅产生文档与 graph 修改；提交后清理本任务生成的 `node_modules` 与 ignored `apps/android/app/build/generated/probeAssets/ui` assets，保留本 tree 供独立 review，不触碰共享 target、设备、runtime 或其他树。

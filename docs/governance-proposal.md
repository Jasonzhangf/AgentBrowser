# GuidanceSetupProposal：AgentBrowser 初始治理

状态：用户“可以，继续”后应用项目配置、事实绑定及 ignore 项；verify/compile 的真实结果见 plan.md。无运行时代码、基线提交或发布授权由此自动产生。

## 应用前基线与保留项

- `AGENTS.md`：AppSDK 新项目标准模板，项目事实仍为空。
- `.appsdk/project.json`：SDK 0.1.6，draft/advisory，示例 `change-me`、`app-core` 与占位构建。
- `.appsdk/skills/appsdk-project-governance/`：复用 SDK 提供的流程，不复制新流程。
- 保留质量/安全/证据边界、控制面分离、唯一 owner、review、干净工作树原则。标准模板只作参考，不成为第二规则源。

## 已采用的配置方案

1. `project_id` 改为 `agentbrowser`，保持 `draft`。
2. 删除生成的示例 `app-core` 绑定与占位构建；设计期 `modules: []`。真实代码目录、build/test/入口存在后才注册模块，不宣称 `source_implemented`。
3. AGENTS 项目事实引用 `docs/design.md` 的确认需求与 owner，明确当前设计阶段、两个仓库及禁止跨仓写入的本轮边界。重复产品细节留在设计文档，不复制规则。
4. Guidance 保持 advisory，复用 SDK 标准 skill。当前任务计划以 `docs/plan.md` 讨论；实现阶段绑定实际模块后再用官方 `guide plan` 写 PlanRecord，避免为讨论创建假的运行时模块。
5. `.gitignore` 增加 `playground/`，以支持基线建立后的独占工作树。正式代码修改之前配置并验证真实 commit/push hooks，不伪称本轮已保护。
6. `appsdk verify` 校验真实项目绑定；`appsdk guide compile` 编译已批准的规则。文档检查不要求安装/重启/freeze；未来运行时模块逐一声明实际部署操作与验证。

## 不采用的模板内容

不采用示例 app-core、placeholder 产物和不存在的 cargo test 作为产品证据。不为本轮文档建立完整函数/资源映射体系，不启动多 worker、不创建发布快照，不将 SDK 初始化等同完成治理。

## 生效顺序及权限

配置与事实绑定已应用；verify 与 Guidance compile 的结果独立报告，随后只读 review。首个基线 commit/push 仍需单独确认。远端为空，本轮只做治理/契约与能力预检；有真实 origin/main 后，所有代码开发在项目 playground 下的独立 clean worktree 进行。

没有授权发布、修改已有 Obscura remote 或发送 GitHub Issue/评论。本地讨论继续；需同步到 GitHub 时以待审阅文档为具体交付对象。

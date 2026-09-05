# AgentBrowser

手机优先、UI 与 browser core 分离、可 attach/detach 的浏览器。当前阶段：设计与 AppSDK 初始化；尚无运行时代码。

- 客户端仓库：https://github.com/Jasonzhangf/AgentBrowser
- Core/Host fork：https://github.com/Jasonzhangf/obscura
- [设计与决策](docs/design.md)：确认需求、边界、提案和待讨论项的唯一入口。
- [模块与连接架构](docs/architecture.md)：Cordis UI、Obscura daemon、Relay、协议 owner 与选路策略。
- [v0 协议与预检](docs/protocol-v0.md)：身份、operation、接管、传输草案及当前真实能力证据。
- [实施与验收计划](docs/plan.md)：按证据推进，不将计划状态当实现完成。
- [治理配置提案](docs/governance-proposal.md)：AppSDK 初始配置的待审阅调整。

AppSDK 已绑定 `agentbrowser`，draft `verify` 通过；设计期无运行时模块，示例 app-core 已移除。Guidance 编译因当前 main 无开发工作树而明确拒绝；模块为空时不能建立模块计划。当前仓库没有首个提交，尚不能从 `origin/main` 创建开发工作树。产品代码、安装和运行时验收尚未完成。

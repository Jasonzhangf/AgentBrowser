# AgentBrowser

手机优先、UI 与 browser core 分离、可 attach/detach 的浏览器。当前已有 Android 本地 H.264/Cordis 兼容性探针；浏览器 Host 和网络链路待接入。

- 客户端仓库：https://github.com/Jasonzhangf/AgentBrowser
- Core/Host fork：https://github.com/Jasonzhangf/obscura
- [设计与决策](docs/design.md)：确认需求、边界、提案和待讨论项的唯一入口。
- [模块与连接架构](docs/architecture.md)：Cordis UI、Obscura daemon、Relay、协议 owner 与选路策略。
- [v0 协议与预检](docs/protocol-v0.md)：身份、operation、接管、传输草案及当前真实能力证据。
- [实施与验收计划](docs/plan.md)：按证据推进，不将计划状态当实现完成。
- [治理配置提案](docs/governance-proposal.md)：AppSDK 初始配置的待审阅调整。

[Android 探针与运行说明](docs/android-probe.md) 包含构建、独立 APK 安装、真机重放入口，以及 AB-01/02/03/04 的实现边界。测试样本直接通过 Android MediaCodec 显示，不经过 JS 视频字节通道。探针成功不代表正式 BrowserSession、WebRTC/WSS 或 Relay 完成。

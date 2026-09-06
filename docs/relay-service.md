# Relay service 首个实现切片

Owner：主任务，工作树 `playground/relay-service`，基线 `eed395c`。Android 任务独占其工作树，不能改 `services/relay/**`、`protocol/relay/**`。本切片允许上述路径、本文件和本工作树 AppSDK 模块绑定，不改 UI/Android、Obscura、共享 root package.json 或 Git config。

Collab 经 `appsdk init` 报告无 live tmux pane；task register 因当前根无 `.agent-collab` 失败。未伪造登记。通过任务消息明确隔离工作树和路径，共享集成等待后续授权。

范围：单实例 HTTPS/WSS Relay，SQLite 账号/设备/Host 持久化，密码登录和短期 token，Ed25519 设备连接证明，Host 全量目录发布、同账号信令、分离 control/media 的单次授权二进制隧道。绑定端口默认 loopback，必须提供 TLS 证书；无明文公网模式。

非目标：Obscura 执行、浏览器控制权、真实客户端媒体/端到端加密握手、UDP/WebRTC、账号注册页面、互联网部署。Relay 仅转发不解析的字节，不能证明客户端实际使用密文；端点加密和设备信任 UX 完成之前不能宣称产品远程安全闭环。

首版账号通过本机 CLI provisioning，网络只提供登录，无开放注册。设备密钥注册需要账号 token；WS 再证明设备私钥持有。账号 token 过期会关闭依附的控制连接和数据隧道。固定连接/帧/目录上限，失败显式返回。当前切片不实现账号设置同步或密钥轮转。

验收：先红测账号隔离、过期、目录替换/过期、设备签名、信令和真实 TLS/WSS 二进制转发，再实现；CLI TLS 入口黑盒、SQLite 重开、重复票据、跨账号/Host 接入拒绝、control/media 分离、背压与断连释放。测试证书/数据库/端口只属于当前测试，退出释放，不干扰其他任务。

## 运行与验证

依赖 Node >=22.13、npm、openssl（测试证书）、tar（产物打包）。仓库根执行：

```sh
npm --prefix services/relay ci
npm --prefix services/relay run build
npm --prefix services/relay test
appsdk compile
appsdk verify
```

产物为 `generated/modules/relay-service/lib/relay.tar`，包含编译后 JS 和锁定的 ws 运行依赖。解包到独立目录后，可运行 `node dist/services/relay/src/main.js account-add <db> <username>`，密码经 stdin 输入；服务入口为 `node dist/services/relay/src/main.js serve <db> <cert.pem> <key.pem>`。`RELAY_BIND` 默认 `127.0.0.1`，`RELAY_PORT` 默认 8443；证书必须覆盖客户端使用的主机名/IP。数据库及私钥目录权限由部署操作者管理；本切片不部署常驻服务。

协议入口：HTTPS `/v1/login`、`/v1/devices`、`/v1/hosts`、`/v1/directory`、`DELETE /v1/token`；WSS `/v1/control/client` 和 `/v1/control/host/<hostId>`。连接先收到 challenge，再提交设备签名的 auth.prove；认证后使用 host.publish、signal.send、tunnel.open。隧道路径和单次票据由 tunnel.offer 返回；双方同一通道收到 channel.ready 后发送 opaque binary。每次断开重建需重新获取票据，无自动重试或隐式降级。

当前 6 组测试覆盖真实 HTTPS/WSS 和解包后 CLI 的账号、双通道转发及撤销授权；control 帧上限负例已验证，慢接收方队列压力尚未做负载验收。当前证据不代表手机到浏览器端到端联调。

AppSDK 模块已绑定真实 build、6 项 regression、唯一 owner 和 `relay.tar`。`deployment_operations: []` 对应临时 CLI 消费者，无安装/重启常驻服务的交付范围。正式 admission 适配器为 `node services/relay/validate-admission.mjs`，要求干净 owner 候选提交；它实际编译、测试并再次解包调用 HTTPS/WSS 入口后生成记录，任何失败不产出 PASS。现阶段候选提交和 admission/AGY 尚未完成，不能将普通 verify 等同于 review PASS。

# Relay service (wire ABI v0)

Owner：主任务，工作树 `playground/relay-service`，基线 `eed395c`。Android 任务独占其工作树，不能改 `services/relay/**`、`protocol/relay/**`。本切片允许上述路径、本文件和本工作树 AppSDK 模块绑定，不改 UI/Android、Obscura、共享 root package.json 或 Git config。

Collab 经 `appsdk init` 报告无 live tmux pane；task register 因当前根无 `.agent-collab` 失败。未伪造登记。通过任务消息明确隔离工作树和路径，共享集成等待后续授权。

范围：单实例 HTTPS/WSS Relay，SQLite 账号/设备/Host 持久化，密码登录和短期 token，Ed25519 设备连接证明，Host 全量目录发布、同账号信令、分离 control/media 的单次授权二进制隧道，以及 Host 对 pending tunnel offer 的显式 `UNKNOWN_PEER` / `CAPACITY` 拒绝。绑定端口默认 loopback，必须提供 TLS 证书；无明文公网模式。HTTP 路径保留 `/v2`，JSON wire 使用 breaking ABI `agentbrowser-relay-v0`；每个控制 envelope 都必须带 `abi`，未知字段和 ABI 不匹配显式拒绝，不接受无 ABI 的旧草案。

非目标：Obscura 执行、浏览器控制权、UDP/WebRTC、账号注册页面、互联网部署。Relay 仅转发不解析的字节，也不持有 transport certificate、不做 peer key TOFU、不验证 `TunnelHello`。端点之间的 inner mTLS、设备 pinning 和 `TunnelHello` 由 `packages/client-connection` 的本地 `RelayPeerBinding` 与 Host adapter 完成；Relay 外层 TLS 不能替代该端到端证明。

首版账号通过本机 CLI provisioning，网络只提供登录，无开放注册。设备密钥注册需要账号 token；WS 再证明设备私钥持有。账号 token 过期会关闭依附的控制连接和数据隧道。固定连接/帧/目录上限，失败显式返回。当前切片不实现账号设置同步或密钥轮转。

验收：先红测账号隔离、过期、目录替换/过期、设备签名、信令和真实 TLS/WSS 二进制转发，再实现；CLI TLS 入口黑盒、SQLite 重开、重复票据、跨账号/Host 接入拒绝、control/media 分离、背压与断连释放；Host reject 必须校验所属 Host、pending/active 状态、过期 offer 和 typed requester closure，且只关闭目标 tunnel。测试证书/数据库/端口只属于当前测试，退出释放，不干扰其他任务。

## 运行与验证

依赖 Node >=22.13、npm、openssl（测试证书）、tar（产物打包）。仓库根执行：

```sh
npm --prefix protocol/relay test
npm --prefix services/relay ci
npm --prefix services/relay run build
npm --prefix services/relay test
appsdk compile-module --module relay-service
appsdk verify
```

产物为 `generated/modules/relay-service/lib/relay.tar`，包含编译后 JS 和锁定的 ws 运行依赖。解包到独立目录后，可运行 `node dist/services/relay/src/main.js account-add <db> <username>`，密码经 stdin 输入；服务入口为 `node dist/services/relay/src/main.js serve <db> <cert.pem> <key.pem>`。`RELAY_BIND` 默认 `127.0.0.1`，`RELAY_PORT` 默认 8443；证书必须覆盖客户端使用的主机名/IP。数据库及私钥目录权限由部署操作者管理；本切片不部署常驻服务。

协议入口：HTTPS `/v2/login`、`/v2/devices`、`/v2/hosts`、`/v2/directory`、`DELETE /v2/token`；WSS `/v2/control/client` 和 `/v2/control/host/<hostId>`，隧道路径为 `/v2/tunnel/<tunnelId>/<channel>/<side>`。连接先收到 `{type: "auth.challenge", abi: "agentbrowser-relay-v0", nonce, path}`，再提交相同 ABI 的设备签名 `auth.prove`；认证成功事件为 `auth.ok`。签名 transcript 绑定 ABI、nonce、精确 path、device ID 和 token digest。认证后使用带 ABI 的 `host.publish`（必须带与连接路径一致的 `hostId`）和 `tunnel.open(hostId, sessionId)`。Host 必须先发布包含目标 session 的完整快照；Relay 为 client side `0` 和 Host side `1` 各发一次性、过期的 control/media tickets。Host 可在 offer pending 且仍属当前认证连接时发送严格的 `tunnel.reject {tunnelId, reason: UNKNOWN_PEER|CAPACITY}`；Relay 以 `tunnel.closed` 向双方确认，并向 requester 保留 `HOST_REJECTED_UNKNOWN_PEER|HOST_REJECTED_CAPACITY` typed reason。双方同一通道收到 `channel.ready` 后只发送 opaque binary；应用层密文由 inner TLS 承载。每次断开重建需重新获取票据，无自动重试或隐式降级。wire 真源是 `protocol/relay/relay-abi.schema.json`，`ABI.lock.json` 锁定其 hash，`validate.mjs --check` 在产物构建前 fail closed。

当前测试覆盖真实 HTTPS/WSS 和解包后 CLI 的账号、双通道转发、撤销授权及 Host typed rejection；ABI envelope、旧 wire 拒绝、Host identity binding、control 帧上限负例已验证，慢接收方队列压力尚未做负载验收。当前证据不代表手机到浏览器端到端联调，也不单独证明 inner mTLS、TunnelHello、Obscura endpoint replay 或安装后的产品入口。`packages/client-connection` 与 Android fixture 仍需单独迁移到该 ABI，Relay service 的绿测不替代下游适配证据。

AppSDK 模块已绑定真实 build、6 项 regression、唯一 owner 和 `relay.tar`。`deployment_operations: []` 对应临时 CLI 消费者，无安装/重启常驻服务的交付范围。正式 admission 适配器为 `node services/relay/validate-admission.mjs`，要求干净 owner 候选提交；它实际编译、测试并再次解包调用 HTTPS/WSS 入口后生成记录，任何失败不产出 PASS。现阶段候选提交和 admission/AGY 尚未完成，不能将普通 verify 等同于 review PASS。

## M1 network evidence projection

`scripts/relay/network-matrix-contract.mjs` owns the evidence-only projection
used by the M1 network matrix. It keeps `network_path` separate from
`transport` and accepts only these path identities: `local-direct` (`local`
with `IPC` or `WSS`), `udp-webrtc` (`lan`/`public` with `WebRTC`),
`tailscale-direct` (`tailscale` with `WebRTC` or direct `WSS`), and `relay`
(`relay` with `WSS`). Tailscale evidence must retain an explicit `underlay`
status; an unavailable underlay remains `unknown`.

A path is `PASS` only when its owner evidence contains a positive decoded and
displayed frame plus successful operation receipts carrying a unique operation
ID, session ID, and current connection generation. Signaling, channel readiness,
transport acknowledgements, and Relay opaque byte forwarding remain
`UNPROVEN` for media or Browser operations. The validator rejects duplicate
operation IDs, cross-account identities, stale generation updates, replayed
operations, and path/transport mismatches. External records can be supplied to
the Relay runner with `--path-evidence-root <directory>`; unrelated JSON files
are ignored, while records carrying the evidence schema are validated before
they are merged into the four matrix rows.

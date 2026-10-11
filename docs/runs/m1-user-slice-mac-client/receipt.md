# Task-17 Mac client connection

Status: **INCOMPLETE — connection handshake failure after setup fix**.

## Source diagnosis

The visible `连接` action is mapped by `packages/ui-plugins/main.tsx:542` to `request({op:'connect'})`. `main.swift:2320-2336` receives the ProbeNative prompt and forwards it to the bridge; `main.swift:301-336` wraps it as `{id,command}`. Native dispatch `connection-bridge/src/main.rs:500-515` receives `connect`; `819-861` loads pairing and calls `Connector.connect`.

The prior required/unconfigured state was caused by the native snapshot omitting connection setup and capability projection. The minimal fix adds these projections from the already-loaded pairing truth: configured setup, `wss` transport, direct path, and capabilities. It does not add another pairing format or modify credentials.

## Runtime result

With `/tmp/abn-task16-pairing` containing the existing task6 pairing files:

- Native bridge status: `connectionSetup.state=configured`, `networkConfigured=true`, `transport=wss`, `path=direct`.
- Native bridge connect: `connectionState=connecting`, generation `1`.
- User-visible drawer: `连接失败`.
- Raw UI error: `Protocol: Expected Host protocol version 11`.
- Host remained running PID `29244`, endpoint PID `67508`, generation `24`.
- No new client session was observed from the retry.

The first failing gate after this fix is the Host protocol handshake. HTTPS browsing, profile state, restart, reconnect, and read-back were not reached.

## Artifact hashes

The built Mac executable hash before and after the fix build remained:
`053adf8ad875a384605181dac38217dc30c2bb4e371af6d7448cae57209cd00c`.

## Scope

Only task6 was used. The LAN Host remained running and untouched. No bundle, transfer code, credential, or identifying cookie value is in evidence.

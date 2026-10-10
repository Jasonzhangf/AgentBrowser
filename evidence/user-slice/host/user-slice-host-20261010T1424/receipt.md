# Task-6 lifecycle retry receipt

This fresh run verifies the lifecycle restart, endpoint refresh, and trust export after the prior successful retry.

Commands:

```text
scripts/host-service/host-service start --root "$HOME/Library/Application Support/AgentBrowser/host-service"
scripts/host-service/host-service status --root "$HOME/Library/Application Support/AgentBrowser/host-service"
scripts/host-service/host-service trust endpoint-refresh --root "$HOME/Library/Application Support/AgentBrowser/host-service"
scripts/host-service/host-service trust export --root "$HOME/Library/Application Support/AgentBrowser/host-service" --device-id e22b76b6-31ac-487b-9953-13e0d78daec8 --output evidence/user-slice/host/user-slice-host-20261010T1424/task6-mac.abconnection
```

Results:

- Lifecycle `start`: **PASS**, dedicated Host PID `66963`, protocol `11`, session `125d0a82-2965-4ace-8e6b-64c66bbe56cc`, socket `/tmp/abn-task6-runtime/host-b7657dc7`.
- `status` before refresh: **PASS**, expected `authorization_state=not_refreshed` after restart; Host SHA-256 `d8e956a22809629b50366a51298d94552284ac7519a06e4ab17c000bde62dd21`.
- `trust endpoint-refresh`: **PASS**, `authorization_state=refreshed`, endpoint generation `3`, endpoint PID `67014`, bound address `127.0.0.1:51880`.
- `trust export`: **PASS**, state `exported`, projection SHA-256 `2e87226e0f95f346ed7f19a1ab928299f5194fb8bdf1e94c36eb0d07cc824a0b`, registry SHA-256 `eddaf8ffc4c6eaa4773392b546fa8ae1cc66de18537d9144406d44e4355d1089`.
- Exported bundle: `task6-mac.abconnection`, SHA-256 `72d2434daaa59a00901499a2f36d0f6e7d1f51ff04ac9a2f821aeb120917ab17`, mode `0600`.
- Lifecycle `stop`: **PASS**, dedicated PID `66963` stopped through the project entrypoint.

The previous `ENDPOINT_PID_MISMATCH` evidence remains preserved under `user-slice-host-20261010T1405`. Shared Host processes were not touched.

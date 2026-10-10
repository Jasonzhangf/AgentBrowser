# Secure transfer-code handoff receipt

Run: `user-slice-host-handoff-20261010T143236`

The integrated Mac owner now requests an explicit native-only transfer-code field from the lifecycle exporter using `trust export --include-transfer-code`. The default CLI path remains redacted. The code is not written to the bundle JSON, logs, argv, or this receipt.

## Build

Command:

```text
PATH="$PWD/node_modules/.bin:$PATH" OBSCURA_PROTOCOL_ROOT=/Volumes/Intel/playground/obscura/m1-media-encoder-ownership-fix-r8-20261009/protocol/browser scripts/build-macos.sh build
```

Mac executable SHA-256: `9ff186d19d17d1966af4fcfe24ade5f38827c979dfd90351e2fa64f6eb53d204`

## Export and handoff

The dedicated Host was started and endpoint refreshed through the lifecycle owner. Export with the explicit native handoff option returned `state=exported` and `handoff_present=true` in the redacted local receipt. The protected bundle was produced successfully. Its SHA-256 is retained only in the filesystem artifact metadata and is intentionally omitted from this receipt.

The ordinary Mac menu item `配置手机连接…` under `连接` was selected with AppleScript System Events (exit 0). The resulting native provisioning sheet was captured at `handoff-sheet.png`, SHA-256 `c4cfee5d8f9e85448898face81a3175a0229629c1fb903de2c14288cf87d4351`. The screenshot is the only handoff proof and is not accompanied by the secret text.

The user action for Android is: select the exported bundle in DocumentsUI; when the protected-bundle dialog appears, enter the one-time code shown in the Mac provisioning handoff.

## Security / scope

- Default export remains code-redacted.
- `--include-transfer-code` is used only by the native Mac owner boundary.
- No transfer code appears in logs, argv, bundle JSON, or this receipt.
- Existing task-7 failure receipt `8261ba4` and earlier task-6 receipts remain intact.
- No Android parser or admission code was changed.

# Android account-directory slice

Status: candidate implementation on `codex/m1-desktop-account-directory`, based
on主控候选 `092ad056b468255acea818586e778e3018eaec39`.

## Owner and allowed paths

| Boundary | Owner | Allowed paths | Forbidden paths |
| --- | --- | --- | --- |
| Relay session, device identity, directory fetch | Rust `AccountRegistry` | `packages/android-bridge/src/account.rs` | WebView, `metadata`, logs, browser payload |
| Android lifecycle and callback fencing | `AccountSession` | `apps/android/app/src/main/java/com/agentbrowser/probe/{AccountSession,NativeAccount}.java`, `MainActivity.java` | `packages/client-connection` protocol reimplementation |
| Typed UI command/projection | account-directory Cordis service/plugin | `packages/client-domain/account-directory.ts`, `packages/ui-kernel/kernel.ts`, `packages/ui-plugins/account-directory.tsx`, `main.tsx`, `probe.css` | token, password, private key, browser connection success |

`RelayClient::login`, `register_device`, `list_directory`, and `revoke` remain
the only Relay operation owner. No second HTTP client or Relay schema copy is
introduced.

## Control and data flow

```text
WebView typed account command
  -> MainActivity -> AccountSession generation fence
  -> NativeAccount JNI -> Rust AccountRegistry -> RelayClient
  <- secret-free account/directory projection
```

The native registry holds the active token and generated device identity. The
password is passed only for the login call and zeroized in the Rust bridge;
the Android layer does not persist it. The active token and private key are
process-memory state and are dropped by revoke/close. They never cross into
JS, snapshots, logs, or the browser connection ABI.

Relay origin and CA are explicit app-private files:
`files/relay/origin.txt` and `files/relay/ca.der`. Missing files fail closed.
`RelayConfig` accepts only HTTPS plus the supplied CA; no plaintext, system
trust fallback, TOFU, or directory certificate trust is added.

Directory refresh replaces the online projection and retains previously seen
hosts only as derived `offline`/`expired` display state. It does not turn a
cached host into a confirmed browser connection. Login, device registration,
directory refresh, and logout are separate UI operations; this slice does not
open an opaque tunnel or select a browser route.

Each Android operation increments a local generation. A delayed login,
registration, refresh, or revoke callback must match the current generation
and handle before changing the projection. Logout fences the old handle before
starting remote revoke; an unconfirmed revoke is reported as a warning after
local secret cleanup, never as remote revoke success.

## Verification gates

- TypeScript: account projection parser rejects unknown fields, malformed
  directory states, and credential leakage; command serialization is tested.
- Rust: `agentbrowser-connection` Relay fixture covers valid TLS login, device
  registration, directory, cross-account isolation, wrong CA, and revoke
  negative paths. The Android bridge must compile against the same public API.
- Android: Java unit compilation and `assembleDebug` are required for the
  changed JNI symbols. A real fixture/device replay is separate evidence from
  source, unit, or APK build and must be scheduled by the main task.

## Non-goals

No browser tunnel, direct/UDP/WSS route selection, session attach, account
password recovery, token refresh, account creation, or persistent credential
vault is part of this slice.

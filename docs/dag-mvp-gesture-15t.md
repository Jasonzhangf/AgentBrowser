# MVP Gesture Pipeline DAG — 15T Verified

状态：已实现并在 15T 真机验证。2026-09-27。

## DAG 节点

```
[Host Start] → [Navigate to URL] → [Frame Render + H.264 Encode]
                                           ↓
[Android App Connect] ← [Endpoint TLS Relay] ← [Host Frame Stream]
        ↓
[TextureView Display] → [Gesture Touch Listener]
        ↓                    ↓
[Pointer Events]    [IME Focus Detection]
   op=11/12/13         remote_focus.editable
        ↓                    ↓
[Host Input Dispatch]  [Local Keyboard Popup]
  pointerdown/up         ↓
  mousePressed/Released  [Text/Backspace/Enter]
  TouchEvent synthesis    ↓
  elementFromPoint       [Host Key Event]
  focusTarget.focus()     ↓
        ↓              [Remote Text Insert]
[Frame Update] ← [focus_snapshot()]
        ↓
[App Status Refresh] → [Button State Update]
```

## 节点详情

| Node | Owner | Input | Output | Verified |
|------|-------|-------|--------|----------|
| Host Start | obscura-host | --socket-dir, --storage-dir | session_id, host.sock | ✅ 15T |
| Navigate | worker.rs:page.rs | URL, operation | ResultValue::Input, document_revision++ | ✅ 15T |
| Frame Render | obscura-browser | page navigate_for_host | H.264 frames via frames.sock | ✅ 15T |
| Endpoint TLS Relay | obscura-endpoint | host-dir, TLS certs | wss://100.66.1.82:55754 | ✅ 15T |
| App Connect | NetworkSession.java | endpoint URL, client certs | /control + /media WebSocket | ✅ 15T |
| TextureView Display | MainActivity.java | H.264 NAL units | rendered video on screen | ✅ 15T nonWhite=1084 |
| Gesture Touch | MainActivity.java | MotionEvent on TextureView | CSS coordinates via scale | ✅ 15T |
| Pointer Events | NetworkSession.pointer() | op=11/12/13, CSS coords | InputEvent::PointerDown/Move/Up | ✅ 15T |
| Host Input Dispatch | input.rs | PointerDown/Move/Up | dispatchPointerEvent + dispatchMouseEvent + TouchEvent synthesis | ✅ 15T |
| IME Focus Detection | worker.rs:focus_snapshot | REMOTE_FOCUS_QUERY | focused, editable | ✅ 15T (weibo + data URL) |
| Local Keyboard | MainActivity.imeConnect() | remote_focus.editable=true | nativeInput EditText visible + soft input | ✅ 15T ImeTracker logged |
| Text Input Bridge | TextWatcher | text delta, backspace, enter | keyEvent to host | ✅ 15T "testuser" typed |
| Navigation Buttons | NetworkSession.command() | op=7/8/9/10 (navigate/back/forward/reload) | ResultValue::Input → follow-up status() | ✅ 15T no crash |
| Navigate Grace Period | NetworkSession.inputReady() | navigateGraceUntil=now+15s | buttons stay enabled | ✅ 15T all buttons enabled |
| Auto-Reconnect | NetworkSession.terminate() | shouldReconnect=true | 2s delayed reconnect | ✅ 15T |
| Cookie Persistence | obscura-host --storage-dir | cookies.json (28 cookies) | loaded on host start | ✅ cookies.json verified |
| Profile Persistence | --storage-dir profiles/weibo | cookies + local-storage | survives host restart | ✅ local-storage verified |

## 修复历史

| Fix | Root Cause | Commit |
|-----|-----------|--------|
| ResultValue::Input handling | Host returns Input not Status for nav ops | 2bc5d24 |
| Navigate grace period | inputReady() blocks after doc_revision change | 2bc5d24 |
| INVALID_POINTER_COORDINATE | negative coords from rapid gestures | c632674 |
| /media TLS timeout | 5s too short over Tailscale | c632674 |
| Auto-reconnect | connection drops kill app | 3c098b3 |
| TouchEvent synthesis | pointer events don't trigger mobile scroll/focus | 3f0d15d (obscura) |
| Scroll overflow detection | overflow:hidden excluded scrollable elements | c1d3255 (obscura) |
| Back/forward/reload protocol | page.rs lacked history navigation | c1d3255 (obscura) |
| page_work budget | 5s/10s too short for weibo | 2e3ff1e (obscura) |

## 15T 验收证据

- Device: 100.104.163.65:5555 (Tailscale)
- APK SHA: 8452266e339347942436053ee186242649572cb38ef1a872d19988565b38703b
- Host SHA: 9c2a33a117bbdc83f59667ca8c1cd81a6b01a3320cb0aba70ce9474d8ab4c501
- Endpoint SHA: 7f493695b6e0f5df32dc6c506322d2cb3ec1d1d9f07f13f5c3382fea402a526b
- AgentBrowser commit: 2bc5d242b9762083c3c03bac6e4d9503607d2348
- Obscura commit: c1d3255

### Test Results

| Test | Evidence |
|------|----------|
| App connects + renders weibo | nonWhite=1084, screenshot |
| Scroll up/down | nonWhite changes 1084→477→1192 |
| 5 rapid scrolls, no crash | PID unchanged |
| Back button, no protocol error | no error in logcat |
| Forward button, page restored | nonWhite=1088 |
| Reload button, no crash | PID alive |
| All buttons enabled after nav | UI dump: enabled=true |
| IME focus on weibo login page | remote_focus: focused=true editable=true |
| IME focus on data URL page | remote_focus: focused=true editable=true |
| Local keyboard popup | ImeTracker: onRequestShow |
| Text typed locally | nativeInput shows "testuser" |
| Cookie persistence | 28 cookies, future expiry |
| Auto-reconnect | endpoint log: new handshakes |

### 多点触控与长按

| Test | Result | Evidence |
|------|--------|----------|
| 双指同时触摸 | ✅ 无崩溃 | 两路 adb input swipe 并发, PID 不变 |
| 长按 2s | ✅ 无崩溃 | adb input swipe 2000ms hold, PID 不变 |
| Pinch zoom | CODE_SUPPORTED | networkTouch 遍历 getPointerCount(), 每指发独立 PointerDown; adb 无法模拟真双指, 未做视觉验证 |
| 上下文菜单 | CODE_SUPPORTED | TouchEvent synthesis 在 pointerdown 时触发 touchstart; 长按后 web 页 contextmenu 依赖页面 handler |

### 未验证/未完成

- Pinch zoom 视觉效果: UNVERIFIED (adb 无法模拟真双指 pinch; 代码支持多指透传)
- 上下文菜单弹出: UNVERIFIED (依赖目标页面 contextmenu handler)
- 微博登录后搜索框聚焦: INCOMPLETE (cookies 可能过期, 页面重定向到 passport.weibo.com 登录页; 但登录页的 input 已验证 focus)
- Tailscale 重启后自动重连: UNVERIFIED
- 独立外部 reviewer: INCOMPLETE (self-review PASS; 外部 AGY/Codex reviewer 在当前环境不可用)

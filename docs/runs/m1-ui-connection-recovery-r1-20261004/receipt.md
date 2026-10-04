# UI connection setup and explicit recovery r1 - receipt

Status: **UI_MODULE_AUTHOR_VERIFIED**
Next-layer status: **NATIVE_IMPORTER_PRODUCT_E2E_WAIT**

Layer: UI_MODULE. This is the public `NativePort` consumer harness plus the
built chrome bundle. It is **not** installed Android/Mac BB01 and does **not**
prove native provisioning, pairing, picker, crypto, or Host behavior.

## Candidate

- Verified candidate source commit: `85b4abe096c791c00a8f95550c7264576079bdea`
- Candidate tree: `151c4cfc7755f62ea429d10e748df66c2b677594`
- Branch: `codex/m1-ui-connection-recovery-r1-20261004`
- Input base: AB origin/main `bc9d67ad037be2c7119bee692f9ad362417ffd05`,
  frozen accepted UI r3 `07cabd11e36dc79a36eea920fcd388fe83a8dccf` / tree
  `09ab27395f443b3cd574733a5868d8645387ea9b`.
- Product commits in this delta: `5f3e61b` (parser/type + UI admission),
  `de8a000` (consumer presentation reset), `85b4abe` (harness verification +
  evidence). This receipt is the docs-closing commit.

## Delivered delta (exact allowlist)

- `packages/client-domain/probe.ts`, `probe.test.ts`: unique
  `ConnectionSetupProjection` owner admits
  `required | configured | opening | importing | unresolved | unavailable |
  error`, plus optional safe `label` (null or printable <=64), `repair_import_allowed`,
  `active_revision`; rejects unknown keys and invalid optional values. No
  sensitive endpoint/certificate/path/transfer_code is admitted into the
  WebView projection. Commands added: `retry_connection_setup`,
  `repair_connection_import`.
- `packages/ui-plugins/main.tsx`: when `connectionSetup` exists it alone owns
  configuration/admission gates. `unresolved` disallows new connect, ordinary
  setup/import, and repair, and offers exactly one explicit retry. `unavailable`
  disallows ordinary connect/import and offers repair only when
  `repair_import_allowed === true`. Missing/false `connectionSetup` capability
  never enables setup/import/repair/retry. Native receipts, not UI clicks,
  control transitions. No timers, no auto-retry, no optimistic success, no
  private connection form/password/textbox.
- `packages/ui-plugins/browser-consumer.tsx`, `consumer-frame.ts`,
  `ui-contract.test.ts`: public consumer inputs and typed receipts for all new
  states; source-contract assertions for the single admission owner.

## API handoff (exact frozen names for later Android/Mac consumer owners)

- Repair action: **`repair_connection_import`** - maps solely to native
  `ConnectionSetupSession.onRepairImportRequested()`.
- Explicit reconcile action: **`retry_connection_setup`** - maps solely to
  native `ConnectionSetupSession.onRetryReconcileRequested()`.
- `open_connection_setup` and `import_connection` are unchanged. Ordinary import
  is never rewritten into repair.

## Commands and actual exits (owned cwd)

| Command | Exit | Evidence |
| --- | --- | --- |
| `npm ci --offline` | 0 | `raw/verify-npm-ci.log` |
| `npm run typecheck` | 0 | `raw/verify-stack.log` |
| `npm test` (34/34 pass, 0 fail) | 0 | `raw/verify-stack.log` |
| `npm run build:ui` | 0 | `raw/verify-stack.log` |
| `node scripts/w4-build-ui-consumer.mjs docs/runs/m1-ui-connection-recovery-r1-20261004/artifacts/consumer` | 0 | `raw/verify-stack.log` |
| `ego-browser nodejs < blackbox/connection-recovery-e2e.mjs` | 0 | `raw/connection-recovery-e2e.stderr.log` (exit=0) |

Bundle hashes (sha256, match the browser evidence JSON):

- app.js `6e9b979845e9122e126f3e428ced5c950ca7b6624940defe5327b567b44c7d75`
- app.css `4a6aae033b5af29b04cdec27f4cf9d10bb9798f78b2121c36b11f295f0d976f7`
- consumer.js `2c4ba78f47b121ed76ae4eff478eb9bcc6aa3ac68ea13192929c0067bf75d3ba`
- consumer-frame.js `aa4b64f213ea46e412b87974419749386619ceb416d05cc8d3464891053f787a`
- consumer.html `760bb2654e621dc390e3dc62729271b23881c523662e23a076d0e557141781c5`
- ui-frame.html `27efac6be2b7286afcd625fc88bed8e9f89191b0a642dacb5ca41d537d5059d2`

## Black-box browser evidence

- Tool: installed ego-browser (`/Users/fanzhang/.local/bin/ego-browser`), one
  owned TaskSpace `139` ("AgentBrowser UI connection recovery r1 20261004"),
  one reused Page `p1`. All user actions are real `page.click` / `page.press`
  (keyboard Escape); `page.evaluate` is used only for read-only DOM inspection
  and to scroll a clipped drawer button into view. No injectJS-driven user
  actions, no other browser, no Playwright, no other task spaces touched.
- Result: 217/217 assertions pass, 30 cases, 28 screenshots, 0 failures,
  exit 0, TaskSpace finished once with `keep: []`.
- Raw: `raw/connection-recovery-e2e.json`, `raw/connection-recovery-e2e.stderr.log`,
  `raw/connection-recovery-e2e.stdout.json`.
- Script/params: `blackbox/connection-recovery-e2e.mjs`, `blackbox/params.json`.
- Screenshots: `screenshots/desktop-*.png` and `screenshots/phone-*.png`
  (28 files; UI_MODULE harness, not installed device).

Covered success, refusal, and side-effect cases:

- `required` -> one ordinary `import_connection` intent; no setup/retry/repair.
- `configured` -> ordinary replace import; native label/revision consumed.
- `unresolved` + legacy `networkConfigured=true` -> connect/ordinary/repair
  blocked; exactly one `retry_connection_setup` -> confirmed native configured
  or still-unresolved visible; no auto-repeat.
- `unavailable` + `repair_import_allowed=true` -> `repair_connection_import`
  intent; cancel keeps unavailable and stays reachable; `repair=false` -> no
  repair action.
- legacy error recoverable via ordinary import; `opening`/`importing` disable
  repeat operations.
- missing/false capability -> all setup actions refused with a useful
  explanation.
- already-published connection with unresolved/unavailable keeps video and
  disconnect, refuses NEW operations, does not replay.
- long valid label (64 chars) renders with no horizontal overflow; no
  password/textarea/contenteditable; only the address input remains.
- no viewport/reload/remote-input/navigation command from setup UI; phone and
  desktop drawer close/reopen preserve r3 receipt geometry.

## Baseline failure sample

- Before repair, `main.tsx` inferred configuration from legacy
  `networkConfigured` and the parser rejected the admitted
  unresolved/unavailable projection. Pre-change `npm test` evidence:
  `raw/red-baseline.log` (28 tests).
- Mac installed shell failure (no-config guidance, disabled setup/import because
  native `connectionSetup` capability is absent) is preserved as
  **NOT_EXECUTED** here. This delta does not fake capability true to clear it;
  native provisioning remains a separate subsequent owner.

## Resources

- Owned server PID 94457 stopped explicitly; loopback port 55730 refuses
  connections after kill. Evidence: `raw/cleanup.log`.
- Owned TaskSpace 139 finished once with `keep: []`; managed Page `p1` closed.
- Retained: this worktree and `docs/runs/m1-ui-connection-recovery-r1-20261004/**`
  for parent intake/review.

## Remaining dependencies / not executed here

- `NATIVE_IMPORTER_PRODUCT_E2E_WAIT`: real Android/Mac native projection must
  implement the two exact actions and emit the admitted typed receipts.
- NOT_EXECUTED in this task: installed Mac failure, Android picker/crypto, Host
  pairing, system IME/profile, and M1.
- Independent review belongs to the parent; this worker did not self-review,
  merge, or push.

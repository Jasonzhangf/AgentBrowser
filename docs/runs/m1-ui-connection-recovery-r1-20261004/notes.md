# UI connection setup and explicit recovery r1 - run notes

Owner: standalone GCM implementation worker (not goal controller, not reviewer).
Layer: UI_MODULE. Not installed Android/Mac BB01.
Input base: AB origin/main `bc9d67ad037be2c7119bee692f9ad362417ffd05`;
frozen accepted UI r3 `07cabd11e36dc79a36eea920fcd388fe83a8dccf` / tree
`09ab27395f443b3cd574733a5868d8645387ea9b`.

Node records use: time / node | conclusion or status | evidence | input / next.

---

## 2026-10-04 ~04:35 PDT / observe RED

- Conclusion: baseline UI rejected the admitted unresolved/unavailable projection
  and inferred configuration from legacy `networkConfigured`. `main.tsx`
  computed `canConnect` with `networkConfigured` even when setup was
  unresolved/invalid, so a real native projection would be gated incorrectly.
- Evidence: `raw/red-baseline.log` (pre-change `npm test`, 28 tests) plus the
  parser's closed state union in `packages/client-domain/probe.ts`.
- Input: frozen r3 tree. Next: extend the single ConnectionSetupProjection
  parser/type owner.

## 2026-10-04 ~05:00 PDT / parser + native projection

- Conclusion: the unique `ConnectionSetupProjection` parser now admits
  `required | configured | opening | importing | unresolved | unavailable |
  error` plus optional safe fields `label` (null or printable <=64),
  `repair_import_allowed`, `active_revision`. Unknown keys and invalid optional
  values are rejected. Commands `retry_connection_setup` and
  `repair_connection_import` added.
- Evidence: `packages/client-domain/probe.ts`,
  `packages/client-domain/probe.test.ts`.
- Next: make the UI consume only typed native truth.

## 2026-10-04 ~05:10 PDT / UI admission owner

- Conclusion: when `connectionSetup` exists it alone owns configuration and
  admission. Missing/false `connectionSetup` capability never enables
  setup/import/repair/retry. `unresolved` disallows new connect/ordinary/repair
  and offers exactly one `retry_connection_setup`. `unavailable` disallows
  ordinary connect/import and offers repair only when
  `repair_import_allowed === true`. Native receipts, not clicks, transition
  state; no timers or optimistic success.
- Evidence: `packages/ui-plugins/main.tsx`,
  `packages/ui-plugins/ui-contract.test.ts`.
- Next: extend the public NativePort consumer harness.

## 2026-10-04 ~05:20 PDT / consumer harness

- Conclusion: added public consumer inputs and typed receipts for all new
  states. Harness is a library verification input producer, not a real importer.
- Evidence: `packages/ui-plugins/browser-consumer.tsx`,
  `packages/ui-plugins/consumer-frame.ts`.
- Next: build the consumer bundle and drive real clicks/keyboard in the browser.

## 2026-10-04 ~05:38-06:16 PDT / browser black-box

- Conclusion: after harness corrections, the full UI_MODULE black-box passed.
  217/217 assertions, 30 cases, 28 screenshots, 0 failures, exit 0.
- Evidence: `raw/connection-recovery-e2e.json`,
  `raw/connection-recovery-e2e.stderr.log` (exit=0), `screenshots/`,
  `blackbox/connection-recovery-e2e.mjs`, `blackbox/params.json` (space 139).
- Harness defects found and fixed (harness only, no product weakening):
  1. scenario switch did not reset inner `presentation`, leaving the drawer
     stuck open across scenarios (commit `de8a000`);
  2. `configured-repair` import returned the unlabeled `configured` snapshot, so
     the periodic status poll overwrote the repaired receipt; added persistent
     `configured-repaired` scenario;
  3. the black-box read the drawer before the slide-in transition settled and
     clicked the hamburger while the closed drawer still intercepted pointer
     events; waits now settle on drawer-in-view and hidden visibility;
  4. the phone harness frame is short, so a clipped drawer button needed a real
     `scrollIntoView` before the click.
- Next: rerun the full verification stack on the frozen candidate.

## 2026-10-04 ~06:16 PDT / verification stack

- Conclusion: all required commands exit 0 on the candidate bundle whose hashes
  match the browser evidence.
- Evidence: `raw/verify-npm-ci.log` (exit 0), `raw/verify-stack.log`
  (typecheck 0, test 34/34 pass 0 fail, build:ui 0, w4-build-ui-consumer 0).
- Bundle hashes (sha256, match raw JSON): app.js
  `6e9b979845e9122e126f3e428ced5c950ca7b6624940defe5327b567b44c7d75`,
  consumer-frame.js
  `aa4b64f213ea46e412b87974419749386619ceb416d05cc8d3464891053f787a`.
- Next: commit exact allowlist and record final SHA/tree.

## 2026-10-04 ~06:18 PDT / resource cleanup

- Conclusion: owned server PID 94457 stopped explicitly; port 55730 refuses
  connections after kill. Own TaskSpace 139 finished once with `keep: []`.
- Evidence: `raw/connection-recovery-e2e.json` `finishReceipt`
  (`closedSpace: true`, `closedManagedLabels: ["p1"]`), `raw/cleanup.log`.
- Not removed: this worktree (retained for parent intake/review per contract).
- Next: final receipt.

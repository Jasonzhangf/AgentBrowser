# M1 UI applied presentation receipt repair r2 - receipt

Status: **MODULE_UI_RECEIPT_VERIFIED** (module evidence only; independent
architecture review and native/installed acceptance are not claimed).

## Identity

- Base source SHA: `a6bec4e302b6113fd16cb78dde2eb05c68b28939`
- Base source tree: `765ea92e4cdf90c240fbf8bcdad784e68897c407`
- Branch: `codex/m1-ui-presentation-receipt-r2-20261004`
- Worktree:
  `/Volumes/Intel/playground/agentbrowser/m1-ui-presentation-receipt-r2-20261004`
- Product commit: `8c4528419c39806b8280218bfb463c21afdd7287`,
  tree `d043aaa85368df5859ea58e5ea79fba9012c748f`.
- Final receipt commit SHA/tree: reported in the author handoff after the last
  docs-only receipt commit.

## Product changes

- `packages/ui-plugins/main.tsx`: retains the last applied receipt revision.
  A status projection older than that revision cannot undo the accepted drawer
  transition. A projection at the accepted revision or newer clears the guard
  and synchronizes normally. No geometry, presentation state, timer, or
  fallback is copied.
- `packages/ui-plugins/consumer-frame.ts` and
  `packages/ui-plugins/browser-consumer.tsx`: add public consumer scenarios for
  applied-open-then-status-error and applied-close-then-status-error, plus a
  public recovery action and a boundary event trace.
- `packages/ui-plugins/ui-contract.test.ts`: adds the narrow source guard for
  the applied receipt revision.

## Red -> green public consumer evidence

The same built public consumer was loaded through its real iframe. Browser
actions were only clicks and Escape; `page.evaluate` was used read-only for
assertions and traces.

Before fix, `raw/receipt-before.json` records:

- desktop and phone applied overlay + `STATUS_FAILED`: `data-drawer-open=false`
  while the applied receipt revision is `8`;
- desktop and phone applied browsing + `STATUS_FAILED`: `data-drawer-open=true`
  while the applied receipt revision is `9`.

After fix, `raw/receipt-after.json` records all assertions true:

- applied overlay remains open through the failed refresh and then synchronizes
  to the current overlay projection;
- applied browsing remains closed through the failed refresh and then
  synchronizes to the current browsing projection;
- rejected presentation remains explicit and retryable;
- observe/wait/control scenarios keep native video visible;
- no viewport/declare/resize command is emitted;
- dispose unmounts once and freezes the status-poll request count.

Raw evidence:

- `raw/receipt-before.json` SHA-256
  `ac0f9bba71e8920a020a1858c38c52d51f2d332dec8f1e67835d8d92886902b1`
- `raw/receipt-after.json` SHA-256
  `afed00f250313b91799b9dc0fbd8760a480dbbc3daf70c4ebd007cb27a6f3739`
- `raw/verification-after.txt` SHA-256
  `3cdd768cf14c3eed1905de143fd201718ec49631f8405ef3ebeda2022aa24713`

## Exact-source commands

`raw/verification-after.txt` contains raw stdout and exit codes:

| Command | Exit |
| --- | --- |
| `npm run typecheck` | 0 |
| `npm test` (28 pass / 0 fail) | 0 |
| `npm run build:ui` | 0 |
| `node scripts/w4-build-ui-consumer.mjs .../artifacts/consumer-after` | 0 |
| `git diff --check` | 0 |

Consumer bundle SHA-256:

- `app.js` `d2e523b00e447c6488d4baa4e8c946754b56bc814d01279931a83a83d56916aa`
- `app.css` `4a6aae033b5af29b04cdec27f4cf9d10bb9798f78b2121c36b11f295f0d976f7`
- `consumer.js` `68b664a867eec41a3eb1b16eb4d902b7fe20c63eb04667d9fc4e7105b862a049`
- `consumer-frame.js` `2b20f3108851172c48b731d59c72deee867e5ba645f4c7e25855032b6465a816`

The rebuilt consumer is the artifact served for the final E2E run; source
hashes are recorded in the same raw verification file.

## Browser and resource closure

- Ego TaskSpaces used for red/final-green runs: 119/122; both finished with
  `keep: []` and no managed pages retained.
- Owned consumer servers: port 60194 PID 80715 (before), port 64750 PID 37312
  (after), port 51166 PID 7336 (final); all terminated.
- Final check: no listeners on those ports and no owned TaskSpace remains.
- No native install, restart, device, network, Host, media, Core, Profile, or
  runtime grant was performed.

## Not claimed

- Independent controller review PASS.
- Native implementation, installed Android/Mac acceptance, or M1 acceptance.
- Production deployment or installed browser behavior.

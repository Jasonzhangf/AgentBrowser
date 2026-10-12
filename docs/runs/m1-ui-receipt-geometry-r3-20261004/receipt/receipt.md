# M1 UI applied receipt geometry repair r3 - receipt

Status: **MODULE_UI_GEOMETRY_VERIFIED / REVIEW_WAIT** (module evidence only;
independent architecture review and native/installed acceptance are not
claimed).

## Identity

- Base source SHA: `ba4472104f1b8e887b61e3a7d68bbb9b4540944a`
- Base source tree: `981514f5ba1dd649f623cf49f2579f674be93ffd`
- Branch: `codex/m1-ui-receipt-geometry-r3-20261004`
- Worktree:
  `/Volumes/Intel/playground/agentbrowser/m1-ui-receipt-geometry-r3-20261004`
- Product commit: `86d1a7ffd2e7b1cd04c202234f0fddae49810a94`,
  tree `8b9f9ad22784effd280fc0a4b3e57387f7b277bf`.
- Final receipt commit SHA/tree: reported in the author handoff after the last
  docs-only receipt commit.

## Root cause (r2 review P1)

The accepted drawer transition guard stored only `result.geometry.revision`. If
the immediate status refresh failed, the retained snapshot layout still
described browsing with `webRect = chromeRect`, so `main.tsx` computed safe
insets from that stale `webRect`: an applied overlay stayed open with top and
bottom insets of `0` instead of the native safe boundary.

## Product changes

- `packages/ui-plugins/main.tsx`: retain the whole applied receipt
  (`{webRect, revision}`). Safe insets project the applied receipt `webRect`
  over older layout revisions while the existing native `safeContentRect`
  still supplies the system-bar boundary. A current or newer layout supersedes
  the projection and clears the receipt; a rejected receipt returns before any
  geometry write. No timer, fallback, fabricated revision, or
  `drawerOpen`-derived geometry was added.
- `packages/ui-plugins/consumer-frame.ts`: keep the non-semantic `webRect`
  field on the public boundary event trace used by the black-box assertion.
- `packages/ui-plugins/ui-contract.test.ts`: update the applied-receipt source
  guard to the new symbols and assert that a rejection precedes any applied
  geometry write.

## Red -> green public consumer evidence

The same built public consumer was loaded through its real iframe. Browser
actions were only clicks and Escape; `page.evaluate` was used read-only for
assertions and traces.

Before fix, `raw/receipt-red.json` records (80 assertions, 15 failed), for both
desktop and phone:

- applied overlay + sustained `STATUS_FAILED`: `data-drawer-open=true` while
  the retained projection is stale `browsing`, but `safeTop=0px`,
  `safeBottom=0px`, `drawerTop=0px`, `drawerBottom=0px`;
- the same case recovers to `24px` only after the current projection arrives;
- the applied overlay receipt carries `revision=8` and
  `webRect={x:0,y:0,width:390,height:844}`, so the missing consumer of the
  receipt geometry, not missing native facts, is the failing edge.

After fix, `raw/receipt-green.json` records 80/80 assertions true,
`failure=null`, for both desktop and phone:

- applied overlay + sustained `STATUS_FAILED`: `data-drawer-open=true`,
  retained projection `browsing`, `safeTop=24px`, `safeBottom=24px`,
  `drawerTop=24px`, `drawerBottom=24px`, measured `drawerRect.top=24` and
  `drawerRect.bottom=viewport.height-24` within half a pixel (desktop 513.5 on
  a 537-tall iframe, phone 293 on 317);
- no drawer-open frame in the per-frame geometry observer shows `0` insets, so
  there is no passing zero-geometry flash;
- the recovery projection supersedes the guard (`presentation=overlay`, `24px`
  kept); close + error stays closed; rejection/retry is explicit; observe/wait
  keep native video visible; no viewport/declare/resize command is emitted;
  dispose unmounts once and freezes the status-poll request count.

Raw evidence:

- `raw/receipt-red.json` SHA-256
  `6dd3b74883e01cb343f8e7dab74b4a87149230a56bc5b9ebf990a4f89abd0d17`
  (immutable r3 red)
- `raw/receipt-green.json` SHA-256
  `4f5cb58fa074ed887b514625015551a7d55726dfeac27590d18a52aea63c3856`
- `raw/verification-green.txt` SHA-256
  `50062cec47092c4d1d574154e881cad350446d45b97ddedbe5a50903165f77f7`
  (see the file for the authoritative command/exit/hash record)

## Exact-source commands

`raw/verification-green.txt` contains the raw record:

| Command | Exit |
| --- | --- |
| `npm run typecheck` | 0 |
| `npm test` (28 pass / 0 fail) | 0 |
| `npm run build:ui` | 0 |
| `node scripts/w4-build-ui-consumer.mjs .../artifacts/consumer-after` | 0 |

Consumer bundle SHA-256 (`artifacts/consumer-after`):

- `app.js` `b8b8ee4d22eb4c10160b6f0f7e2c073d17e12acd9d544e7d78b81f7adb8ce9d0`
- `app.css` `4a6aae033b5af29b04cdec27f4cf9d10bb9798f78b2121c36b11f295f0d976f7`
- `consumer.js` `68b664a867eec41a3eb1b16eb4d902b7fe20c63eb04667d9fc4e7105b862a049`
- `consumer-frame.js` `8b11ce05550cf6392501dab8a876daea5ef144be774558e85e0af3003370dc6d`
- `consumer.html` `760bb2654e621dc390e3dc62729271b23881c523662e23a076d0e557141781c5`
- `ui-frame.html` `27efac6be2b7286afcd625fc88bed8e9f89191b0a642dacb5ca41d537d5059d2`

Code-identical mapping: a public consumer rebuilt from committed product
`86d1a7f` is byte-identical to `artifacts/consumer-after` for all six files, so
the captured evidence maps to exact product source.

## Browser and resource closure

- Owned Ego TaskSpace `124` finished with `keep: []`, closed `p1`,
  `preservedUnmanagedCount: 0`. The red phase used TaskSpace `123` (already
  closed before this author started).
- Owned consumer server `127.0.0.1:63744` PID `30303` terminated; final
  `lsof -nP -iTCP:63744 -sTCP:LISTEN` reports no listener.
- No native install, restart, device, network, Host, media, Core, Profile, or
  runtime grant was performed.

## Evidence note

The drawer open animation is a 220ms CSS `translateX` transition. The instant
`open-status-error` screenshot is sampled at the transition start frame, so the
drawer's horizontal position is not yet settled there; the asserted
safe-boundary property (top/bottom) is not animated and is settled at `24px` in
that same frame. The matching `open-status-error-recovered` screenshot shows
the drawer settled open (white drawer surface over a darkened left backdrop).
The authoritative evidence for the fix is the measured DOM geometry and the
per-frame observer record in `raw/receipt-green.json`.

## Not claimed

- Independent architecture review PASS (this is the REVIEW_WAIT handoff).
- Native implementation, installed Android/Mac acceptance, or M1 acceptance.
- Production deployment, installed browser behavior, or merge/push.

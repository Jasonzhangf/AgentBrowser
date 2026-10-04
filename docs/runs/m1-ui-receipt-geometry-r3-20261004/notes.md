# M1 UI applied receipt geometry repair r3 - run notes

Owner: standalone GCM implementation author.

## Node 0 - identity and scope

Time: 2026-10-03 23:57 PDT

Node: worktree/base identity.

Conclusion: Owned worktree is clean on
`codex/m1-ui-receipt-geometry-r3-20261004` at
`ba4472104f1b8e887b61e3a7d68bbb9b4540944a`, tree
`981514f5ba1dd649f623cf49f2579f674be93ffd`. `origin/main` is
`bc9d67ad037be2c7119bee692f9ad362417ffd05`, and the branch is 19 commits ahead
because the retained r2 source and evidence commits are linear history.

Evidence: `git status --short --branch`, `git rev-parse HEAD`,
`git rev-parse HEAD^{tree}`, `git rev-parse origin/main`, `git log --oneline`.

Input: admitted native layout r5 contract under
`docs/runs/m1-native-chrome-layout-design-r1-20261003/`; retained r2 source,
notes, raw results and parent review at
`/Volumes/Intel/playground/agentbrowser/m1-ui-presentation-receipt-r2-20261004/.agent-collab/review/m1-ui-presentation-layout-impl-20261004-r2/`.

Environment: external Intel playground worktree; product write scope is only
`packages/ui-plugins/main.tsx`, `consumer-frame.ts`, `browser-consumer.tsx`,
and `ui-contract.test.ts`. Native/Core/media/protocol/runtime/device/network
paths are read-only and out of scope. Hooks remain enabled.

Next: reproduce the retained review P1 through the public built consumer before
changing product code.

## Node 1 - contract, owner and root cause

Time: 2026-10-03 23:57 PDT

Node: accepted contract and first failing edge.

Conclusion: The r2 independent review is `failed` / `code_failure` with one
P1 at `packages/ui-plugins/main.tsx:347`. The accepted UI transition guard
stores only `result.geometry.revision`; it does not retain the applied
receipt's `geometry.webRect`. If the immediate status refresh fails, the
retained snapshot layout still describes browsing with `webRect=chromeRect`.
`main.tsx:209-216` then computes safe insets from that stale webRect, so an
applied overlay can keep the drawer open while top/bottom insets are `0`
instead of the native safe boundary `24`.

Evidence: r2 review final P1 and remediation; r2 `raw/receipt-after.json` cases
`desktop-open-status-error` and `phone-open-status-error` record
`drawerOpen=true`, `presentation=browsing`, `drawerTop=0px`,
`drawerBottom=0px`, while the recovered current projection records `24px`.
Accepted contract says only an applied receipt may show valid state and that
drawer content is laid out inside `safeContentRect`, not guessed from drawer
state or project layout truth into native geometry.

Input: r2 product commit `8c4528419c39806b8280218bfb463c21afdd7287`, r2
evidence commit `ba4472104f1b8e887b61e3a7d68bbb9b4540944a`, r5 contract.

Environment: source and retained raw evidence only; no product edit yet.

Next: add red geometry assertions to the existing public consumer scenarios
and capture red screenshots/raw.

## Node 2 - red-build preparation

Time: 2026-10-03 23:57 PDT

Node: red artifact preparation.

Conclusion: Offline `npm ci --offline` installed the locked dependency tree
without changing tracked files. The exact current source passed `npm run
typecheck`, `npm test` (28/28), and `npm run build:ui`. The isolated public
consumer build for the red run is
`docs/runs/m1-ui-receipt-geometry-r3-20261004/artifacts/consumer-red`.
Its `app.js` hash is
`d2e523b00e447c6488d4baa4e8c946754b56bc814d01279931a83a83d56916aa`,
matching the r2 fixed bundle and confirming the red run uses the retained
source behavior.

Evidence: command exit codes and hashes recorded in `raw/verification-red.txt`
(to be written by the red run); current source SHA-256 values are
`f4b48062...4cfe98` (main.tsx), `9bf8e517...c701`
(consumer-frame.ts), `950f8afe...e838` (browser-consumer.tsx), and
`987810fa...f6142` (ui-contract.test.ts).

Input: clean worktree at `ba44721` with only the new r3 run directory
untracked; locked `package-lock.json`.

Environment: local npm dependencies and isolated artifact directory; no
native/device/network operation.

Next: start one owned public consumer server, run the red public E2E in one
owned Ego TaskSpace at desktop and phone, then record geometry failures.

## Node 3 - red public-consumer reproduction

Time: 2026-10-04 00:22 PDT

Node: public red reproduction and evidence bind.

Conclusion: The red public consumer run reproduced the review finding at
desktop and phone through actual UI clicks. In the applied-overlay plus
sustained `STATUS_FAILED` state the drawer stays open, `presentation` remains
the stale `browsing` projection, and both viewports report `safeTop=0px`,
`safeBottom=0px`, `drawerTop=0px`, `drawerBottom=0px`. Measured drawer bounds
reach the viewport edges. The same cases recover to `24px` only after current
layout arrives. The red run's 80 assertions include 15 expected geometry
failures; the trace records the applied overlay receipt revision `8` with
`webRect={x:0,y:0,width:390,height:844}`, so the failure is the missing receipt
geometry consumer rather than missing native facts.

Evidence: `raw/receipt-red.json` SHA-256
`6dd3b74883e01cb343f8e7dab74b4a87149230a56bc5b9ebf990a4f89abd0d17`;
red screenshots under `screenshots/red-*`; `raw/verification-red.txt`; the
red consumer bundle hash map is embedded in `raw/receipt-red.json`.

Input: exact base source `ba44721` / tree `981514f5...3ffd`; red consumer
bundle from that source. The source run also included the non-semantic trace
`webRect` field in the public consumer fixture; `app.js` stayed byte-identical
to the r2 fixed bundle (`d2e523b0...16aa`), and the product `main.tsx` remained
untouched before this reproduction.

Environment: one owned Ego TaskSpace `123` (finished with `keep: []`,
unmanaged count `0`), one owned server `127.0.0.1:60315` PID `4945`, desktop
`1280x800` and phone `390x844` device metrics, read-only `page.evaluate`
assertions/traces, no private UI JS actions.

Next: implement the unique receipt geometry consumer in `main.tsx`, then
extend the auxiliary source contract and add a public recovery synchronization
scenario.

## Node 4 - owned server stop

Time: 2026-10-04 00:22 PDT

Node: red capture resource stop.

Conclusion: The owned consumer server PID `4945` on port `60315` was stopped
after the red capture. The Ego TaskSpace `123` was closed by the script with
`keep: []` and no unmanaged pages retained. No shared browser, cache, runtime,
device, or network operation was performed.

Evidence: server session output and red raw `finishReceipt`.

Input: red run complete.

Environment: owned server stopped; product source still unchanged.

Next: edit only the allowed UI consumer files.

## Node 5 - unique receipt geometry consumer

Time: 2026-10-04 00:30 PDT

Node: minimal fix in the allowed UI consumer.

Conclusion: `main.tsx` now retains the whole applied receipt
(`{webRect, revision}`) instead of only `geometry.revision`. Safe insets
project that receipt `webRect` over older layout revisions and still derive the
bar boundary from the existing native `safeContentRect`; a current or newer
layout supersedes the projection and clears the receipt. The rejected branch
returns before any receipt write, so a rejection never retains geometry. No
timer, fallback, fabricated revision, or `drawerOpen`-derived geometry was
added. `consumer-frame.ts` keeps the non-semantic `webRect` trace field used by
the public black-box assertion; `ui-contract.test.ts` was updated to the new
symbols and to assert the rejected-before-write ordering.

Evidence: product commit `86d1a7ffd2e7b1cd04c202234f0fddae49810a94` / tree
`8b9f9ad22784effd280fc0a4b3e57387f7b277bf`; `git show 86d1a7f`; source
hashes in `raw/verification-green.txt`.

Input: exact base `ba44721`; admitted r5 contract; r2 review P1 remediation.

Environment: external playground worktree; only the three allowed product
files changed; hooks enabled.

Next: exact-source typecheck/test/build and a public green capture bound to the
committed product source.

## Node 6 - exact-source verification

Time: 2026-10-04 00:31 PDT

Node: candidate self-check and code-identical mapping.

Conclusion: From the committed product source `86d1a7f`, `npm run typecheck`,
`npm test` (28/28), `npm run build:ui`, and
`node scripts/w4-build-ui-consumer.mjs` all exited `0`. A rebuild of the public
consumer from the committed tree is byte-identical to the bundle used for the
green E2E (all six files match), so the captured evidence maps to exact product
source. The green `app.js` differs from the red bundle only by the product fix;
`consumer-frame.js` is unchanged between red and green.

Evidence: `raw/verification-green.txt` (exit codes, source/bundle hashes).

Input: product `86d1a7f`; locked dependency tree.

Environment: local npm dependencies; no native/device/network operation.

Next: capture the green public-UI E2E at desktop and phone.

## Node 7 - green public-consumer verification

Time: 2026-10-04 00:36 PDT

Node: applied receipt geometry green capture.

Conclusion: The green public consumer run passed 80/80 assertions with
`failure=null`. At both desktop and phone, the applied overlay under sustained
`STATUS_FAILED` keeps `drawerOpen=true`, retains the stale `browsing`
projection, and now reports `safeTop=24px`, `safeBottom=24px`, `drawerTop=24px`,
`drawerBottom=24px`; measured `drawerRect.top=24` and
`drawerRect.bottom=viewport.height-24` within half a pixel (desktop 513.5 on a
537-tall iframe; phone 293 on 317). No drawer-open frame in the per-frame
geometry observer shows 0 insets, so there is no passing zero-geometry flash.
The recovery projection supersedes the guard (`presentation=overlay`, 24px
kept); close + error stays closed; rejection/retry, video retention,
no-viewport-command, and single dispose assertions all pass.

Evidence: `raw/receipt-green.json` SHA-256
`4f5cb58fa074ed887b514625015551a7d55726dfeac27590d18a52aea63c3856`;
`screenshots/green-*`; `raw/verification-green.txt`.

Input: product `86d1a7f` / tree `8b9f9ad2...7bf`; `artifacts/consumer-after`;
owned server `127.0.0.1:63744` PID `30303`; owned Ego TaskSpace `124`
(finished `keep: []`, closed `p1`, `preservedUnmanagedCount: 0`).

Environment: desktop `1280x800` and phone `390x844` device metrics; read-only
`page.evaluate` assertions/traces; no private UI JS actions; no shared
browser/cache/runtime/device/network operation.

Note: the drawer open animation is a 220ms CSS `translateX` transition, so the
instant `open-status-error` screenshot is sampled at the transition start
frame; the asserted safe-boundary property (top/bottom) is not animated and is
settled at 24px in that same frame. The matching
`open-status-error-recovered` screenshot shows the drawer settled open
(drawer surface white over a darkened left backdrop).

Next: stop the owned server, then commit the retained evidence.

## Node 8 - owned server stop

Time: 2026-10-04 00:38 PDT

Node: green capture resource stop.

Conclusion: The owned consumer server PID `30303` on port `63744` was stopped
after the green capture; `lsof -nP -iTCP:63744 -sTCP:LISTEN` reports no
listener and the PID is gone. The owned Ego TaskSpace `124` was closed by the
script with `keep: []`. No shared browser, cache, runtime, device, or network
operation was performed.

Evidence: server start/stop output and green raw `finishReceipt`.

Input: green run complete.

Environment: owned server stopped; no owned listener or process remains.

Next: commit the r3 evidence and run the range/readback checks.

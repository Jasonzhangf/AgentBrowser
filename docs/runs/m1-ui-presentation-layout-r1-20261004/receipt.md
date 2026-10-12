# M1 UI presentation/layout author r1 - receipt

Status: **MODULE_UI_VERIFIED** (UI module boundary only; not review PASS, not M1
complete).

## Identity

- Base source SHA: `65f1b90531719c3854a169e58b58c0f9675d9d1e`
- Base source tree: `5a75141c5b7c5535ef3dad95e0e8696f2909a599`
- Product commit SHA: `08b8a63dc452f8dded3e5309f0ce85aa8b72293e`
- Product commit tree: `694a10bb75b4b0271b938c9fc15992ba3c2e3d94`
- Branch: `codex/m1-ui-presentation-layout-r1-20261004`
- Worktree: `/Volumes/Intel/playground/agentbrowser/m1-ui-presentation-layout-r1-20261004`

## Changed product files (allowlist)

- `packages/client-domain/probe.ts`
- `packages/client-domain/probe.test.ts`
- `packages/ui-plugins/main.tsx`
- `packages/ui-plugins/probe.css`
- `packages/ui-plugins/consumer-frame.ts`
- `packages/ui-plugins/browser-consumer.tsx`
- `packages/ui-plugins/ui-contract.test.ts`

## Owned evidence

- `docs/runs/m1-ui-presentation-layout-r1-20261004/blackbox/ego-e2e.mjs`
- `docs/runs/m1-ui-presentation-layout-r1-20261004/raw/verification.txt`
- `docs/runs/m1-ui-presentation-layout-r1-20261004/raw/e2e.json`
- `docs/runs/m1-ui-presentation-layout-r1-20261004/screenshots/*.png`

## Commands / exits

| Command | Exit |
| --- | --- |
| `npm ci` | 0 |
| `npm run typecheck` | 0 |
| `npm test` (27 pass / 0 fail) | 0 |
| `npm run build:ui` | 0 |
| `node scripts/w4-build-ui-consumer.mjs docs/runs/.../artifacts/consumer-after` | 0 |
| `node scripts/w4-ui-consumer.mjs docs/runs/.../artifacts/consumer-after` (PID 59958, port 60202) | served |
| real-browser E2E on Ego TaskSpace 112 | all assertions pass |
| `git diff --check 65f1b90 HEAD` | 0 |

## Bundle hashes (sha256, consumer-after)

- `app.js` `5f2d560e8b078bb65c316866f78c0398347e2e44e8fba32501139c9cf0c5ebfb`
- `app.css` `4a6aae033b5af29b04cdec27f4cf9d10bb9798f78b2121c36b11f295f0d976f7`
- `consumer-frame.js` `ee2256ba3e605f093619887ac81b13ee0add315d113d108d8b4f7601d8800b52`

## Browser E2E

- Browser: Ego TaskSpace 112, Page `p1`, URL `http://127.0.0.1:60202/consumer.html`.
- Real UI actions (clicks/Escape) only; `page.evaluate` used read-only for
  geometry/attribute assertions.
- Assertions passed: connected-no-frame empty visible; valid displayed frame
  native-video; observation/control-return/operation-pending keep video visible;
  drawer open/close one typed `set_presentation` each; no viewport commands;
  rejection explicit with retry; Escape close; dispose releases once with frozen
  native request count; desktop and phone viewports; drawer consumes native safe
  insets.
- TaskSpace finish receipt: `{closedSpace: true, keptManagedLabels: [],
  closedManagedLabels: ["p1"], preservedUnmanagedCount: 0}`.

## Handoff signature

UI -> native: `{id: string, op: 'set_presentation', state: 'browsing'|'overlay'}`.

native -> UI: `{id, op: 'set_presentation', status: 'applied',
geometry: {webRect: Rect, revision: number}}` or
`{id, op: 'set_presentation', status: 'rejected',
reason: 'NO_AUTHORITY'|'ACTIVITY_NOT_RESUMED'|'GEOMETRY_UNAVAILABLE'}`.

Read-only projection: `ProbeSnapshot.layout` (optional; absence = unknown) with
`drawableWindowRect`, `safeContentRect`, `advertisedViewportRect`,
`presentedContentRect`, `chromeRect`, `webRect`, `visibleContent`,
`displayedFrame`, `currentTabId`, `presentationViewport`, `geometryMatches`,
`presentation`, `revision`. Native owns displayed identity and geometry match.

## First gap / not claimed

- Independent architecture review has not run (author does not self-review).
- Native L1-L7 / BB01/03 acceptance and installed Android/Mac/M1 acceptance are
  not covered by this UI module evidence.

## Resources

- Owned consumer server before: PID 87770, port 58503 (terminated).
- Owned consumer server after: PID 59958, port 60202 (terminated).
- Ego TaskSpace 112 finished with `keep: []`.
- Candidate source and evidence retained for controller review.

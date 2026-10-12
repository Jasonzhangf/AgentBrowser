# M1 UI presentation/layout author r1 - run notes

Owner: fresh GCM implementation author, worktree
`/Volumes/Intel/playground/agentbrowser/m1-ui-presentation-layout-r1-20261004`,
branch `codex/m1-ui-presentation-layout-r1-20261004`.

Base source: `65f1b90531719c3854a169e58b58c0f9675d9d1e`,
tree `5a75141c5b7c5535ef3dad95e0e8696f2909a599` (accepted layout source).
Allowed product paths only; no native/JNI/connection/kernel/protocol/service/
.appsdk/AGENTS/package-manifest/build-script edits.

## Scope

Implement the accepted AB-02/03 UI/domain half of the native chrome layout
contract:

- typed `set_presentation {id, state: browsing|overlay}` command and typed
  `applied`/`rejected` receipt;
- read-only `ProbeSnapshot.layout` projection/parser with absence preserved as
  unknown;
- `visibleContent` drives native-video transparency and is independent of
  `connected`/`inputReady`/control/operation;
- drawer/backdrop intents call the typed command once per open/close and wait
  for the actual applied/rejected receipt; rejected stays usable and explicit;
- full-window overlay + safe-content drawer consume native rects/insets with no
  root padding and no viewport command on drawer/IME;
- Escape/Back/focus/dispose follow the accepted lifecycle.

## Nodes

### Input / environment

- Read global `AGENTS.md`, project contract, and the r5 design PASS:
  `m1-native-chrome-layout-design-r5-20261004/.../status.json` verdict `pass`,
  findings empty; `repair-design.md`, `implementation-contracts.md`,
  `blackbox-plan.md`.
- Read Skills `frontend-ui-reference`, `ego-browser`, `coding-principals`.
- `npm ci` exit 0. Public consumer build/server and real browser control
  capability confirmed before product edit; baseline drawer emitted no
  `set_presentation`.

### Implementation

- `packages/client-domain/probe.ts`: `PresentationState`, `PresentationCommand`,
  `PresentationResult`, `NativeLayoutProjection`, `parsePresentationResult`,
  `isPresentationResult`; strict current-contract validation; `layout` optional
  and absence preserved as unknown.
- `packages/ui-plugins/main.tsx`: `requestPresentation` sends the typed command
  once and returns the receipt; `openDrawer`/`closeDrawer` apply only on
  `applied`, show explicit reason on `rejected`; `data-ready` bound to
  `layout.visibleContent === 'native-video'`; safe insets derived from
  `safeContentRect` vs `webRect`, no root padding.
- `packages/ui-plugins/probe.css`: `.viewer-surface` hidden only on real ready;
  overlay/backdrop full-window; drawer content bounded by `--safe-*`.
- `packages/ui-plugins/consumer-frame.ts` / `browser-consumer.tsx`: public
  `NativePort` consumer scenarios `connected-no-frame`, `connected-video`,
  `presentation-rejected`, `operation-pending`.
- `ui-contract.test.ts` / `probe.test.ts`: contract and projection coverage.

### Verification

- `npm run typecheck` exit 0.
- `npm test` exit 0 (27 pass / 0 fail).
- `npm run build:ui` exit 0.
- `node scripts/w4-build-ui-consumer.mjs <owned-output>` exit 0.
- `node scripts/w4-ui-consumer.mjs <owned-output>` owned server; real-browser
  E2E via owned Ego TaskSpace 112.

Evidence paths: `raw/verification.txt`, `raw/e2e.json`, `screenshots/*.png`,
`blackbox/ego-e2e.mjs`.

### Exit / result

- Result: `MODULE_UI_VERIFIED` for the UI module boundary only.
- Not claimed: architecture review PASS, M1 acceptance, native L1-L7 / BB01/03.
- Native handoff: UI sends `{id, op:'set_presentation', state:'browsing'|'overlay'}`
  and expects `{id, op:'set_presentation', status:'applied', geometry:{webRect, revision}}`
  or `{id, op:'set_presentation', status:'rejected', reason:'NO_AUTHORITY'|'ACTIVITY_NOT_RESUMED'|'GEOMETRY_UNAVAILABLE'}`.
  Native owns displayed identity and geometry matching; UI adds no geometry or
  viewport derivation.

### Next

- Native implementer consumes the frozen typed API later; controller runs
  independent architecture review and integration.

## Resources / retention

- Owned consumer servers: before PID 87770 port 58503 (baseline), after PID
  59958 port 60202 (E2E). Both terminated after evidence capture.
- Owned Ego TaskSpace 112 finished with `keep: []`
  (`closedSpace: true`, `closedManagedLabels: ["p1"]`).
- Retained: candidate source, `docs/runs/m1-ui-presentation-layout-r1-20261004/`
  notes/receipt/blackbox/raw/screenshots for review.
- Removed: scratch debug screenshot and temporary logs outside the run dir.

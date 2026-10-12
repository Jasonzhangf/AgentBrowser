# M1 UI applied presentation receipt repair r2 - run notes

Owner: standalone GCM implementation author.

## Node 0 - identity and scope

Time: 2026-10-03

Node: worktree/base identity.

Conclusion: Owned worktree is clean on
`codex/m1-ui-presentation-receipt-r2-20261004` at
`a6bec4e302b6113fd16cb78dde2eb05c68b28939`, tree
`765ea92e4cdf90c240fbf8bcdad784e68897c407`. `origin/main` is
`bc9d67ad037be2c7119bee692f9ad362417ffd05`.

Evidence: `git status --short --branch`, `git rev-parse HEAD`,
`git rev-parse HEAD^{tree}`, `git rev-parse origin/main`.

Input: accepted layout r5 contract, UI layout r1 receipt/raw, and the independent
P1 review at
`/Volumes/Intel/playground/agentbrowser/m1-ui-presentation-layout-r1-20261004/.agent-collab/review/m1-ui-presentation-layout-impl-20261004-r1/review.final.md`.

Environment: external Intel playground worktree; product paths restricted to
`packages/ui-plugins/main.tsx`, `consumer-frame.ts`, `browser-consumer.tsx`, and
`ui-contract.test.ts`; `packages/client-domain/probe.test.ts` only if a public
contract test requires it.

Next: add public-consumer red scenarios, then reproduce the two status-failure
transitions before changing the UI receipt logic.

## Node 1 - accepted contract and P1

Time: 2026-10-03

Node: owner/root-cause analysis.

Conclusion: `set_presentation` is a synchronous typed command. Only an
`applied` receipt may change the drawer, and its native-owned
`geometry.revision` is the ordering fact. `main.tsx` currently applies the local
transition and then immediately refreshes status; if that refresh fails, the
retained `ProbeSnapshot.layout` still carries the old presentation and the
synchronization effect undoes the accepted transition. The review identifies
the first divergence at `packages/ui-plugins/main.tsx:435`.

Evidence: accepted `implementation-contracts.md`; independent review P1
evidence/remediation; source trace in `main.tsx`.

Input: exact owned source above.

Environment: source-only analysis; no product edit yet.

Next: build a public-consumer scenario that makes status fail after an applied
receipt while retaining the old projection, then record red evidence.

## Node 2 - red reproduction through public consumer

Time: 2026-10-03

Node: public black-box reproduction before product fix.

Conclusion: Added public consumer scenarios
`presentation-open-status-error` and `presentation-close-status-error`. The
built consumer, loaded through its real iframe, was driven only by UI clicks and
Escape. Desktop and phone both reproduced the review failure: applied overlay
then `STATUS_FAILED` left `data-drawer-open=false` while native/receipt was
overlay; applied browsing then `STATUS_FAILED` left `data-drawer-open=true`
while the receipt was browsing. The trace shows the applied receipt and its
revision before the failed status request.

Evidence: `raw/receipt-before.json`; `screenshots/before-*`.

Input: exact owned source at `a6bec4e`; built consumer
`artifacts/consumer-before`.

Environment: Ego TaskSpace 119, owned server port 60194, desktop 1280x800 and
phone 390x844; read-only `page.evaluate` assertions.

Next: implement a single receipt-revision guard in the UI consumer.

## Node 3 - receipt revision guard implementation

Time: 2026-10-03

Node: product fix.

Conclusion: `main.tsx` now stores only the last applied receipt revision in
`appliedPresentationRevisionRef`. The synchronization effect ignores any
subsequent layout projection with `revision < appliedRevision`; once
`revision >= appliedRevision` arrives, the guard is cleared and the projection
is allowed to synchronize the drawer. Rejected and invalid results do not set
the guard. This uses the native-owned order fact and introduces no geometry or
presentation-state copy, second state machine, timer, or control-plane field.

Evidence: `packages/ui-plugins/main.tsx`; public scenarios in
`consumer-frame.ts` and `browser-consumer.tsx`; source guard in
`ui-contract.test.ts`.

Input: red reproduction in Node 2.

Environment: allowed UI consumer paths only.

Next: run exact-source verification, rebuild the consumer, and repeat the same
real-browser matrix green.

## Node 4 - green verification

Time: 2026-10-03

Node: fixed candidate verification.

Conclusion: `npm run typecheck`, `npm test` (28/28), `npm run build:ui`, and
`w4-build-ui-consumer` all exit 0 on the fixed source. The same real-browser
matrix passes on desktop and phone: applied open/close survives status failure,
later current projections synchronize, rejects remain explicit and retryable,
observe/wait keeps video visible, no viewport command is emitted, and dispose
stops polling and unmounts once. The fixed bundle hashes are recorded in
`raw/verification-after.txt`.

Evidence: `raw/receipt-after.json`, `raw/verification-after.txt`;
`screenshots/after-*`.

Input: fixed source and rebuilt `artifacts/consumer-after`.

Environment: Ego TaskSpace 120 (then 121 for final repeat), owned server port
64750; desktop and phone viewports.

Next: write the receipt and commit the allowlist.

## Node 5 - allowlist commit

Time: 2026-10-03

Node: product/evidence commit.

Conclusion: Committed the allowed product files plus this run directory through
the existing `.githooks/pre-commit` hook. The hook ran `verify:local` and passed
typecheck, 28/28 tests, and `build:ui`. Candidate commit
`8c4528419c39806b8280218bfb463c21afdd7287` / tree
`d043aaa85368df5859ea58e5ea79fba9012c748f`. `git diff --check a6bec4e..HEAD`
exits 0. No forbidden path changed.

Evidence: hook stdout; commit stdout; `git status`, `git rev-parse`,
`git diff --check a6bec4e..HEAD`, scope diff.

Input: verified fixed source and evidence tree.

Environment: owned branch; no remote integration or native install.

Next: author receipt readback and controller handoff.

## Node 6 - final readback

Time: 2026-10-03

Node: final candidate/evidence binding.

Conclusion: Product commit `8c45284` remains the code-identical source of the
final receipt tree; later commits are docs/evidence-only. `git diff --check
a6bec4e..HEAD` is exit 0, no forbidden path changed, raw hashes remain bound,
and the final receipt commit/tree is reported in the handoff to avoid
self-reference in the receipt file.

Evidence: `raw/final-readback.txt`; final `git status`/`rev-parse` commands.

Input: committed candidate and evidence tree.

Environment: owned branch; no merge/push/install.

Next: handoff to controller for independent review.

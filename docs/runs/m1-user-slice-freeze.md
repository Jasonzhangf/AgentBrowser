# AB freeze inventory

Candidate branch: `codex/m1-user-slice-host-integration-20261010`.

## Intentional inventory

- `apps/macos/Sources/main.swift`: repair candidate `f9f715e` changes the frozen source only to replace all invalid diagnostics. Scope is 15 insertions/11 deletions: one `macDiagnostic` helper, 7 mechanical diagnostic-call replacements, and 4 interpolation restructures required by Swift parsing. This is not yet frozen or runtime-validated.
- `docs/runs/m1-user-slice-real-path/receipt.md`: pre-existing Task-12 Android real-path receipt. It is retained in the worktree and explicitly excluded from this candidate; it is not deleted or modified.
- `evidence-task6-status.json`: retained task6 lifecycle status receipt and committed.
- `evidence-task6-final-status.json`: retained task6 final coherent-identity receipt and committed.

## Remote integration

Fetched `origin/codex/m1-user-slice-host-integration-20261010`. The fetched remote tip is an ancestor of local `e7bc9be` (no remote commits require integration). No merge or rebase was required; no reset, restore, or stash was used.

## Obscura scope classification

Obscura r8 contains untracked path `docs/runs/m1-frame-forwarding-impl-r1-20261005/`. It is pre-existing Obscura evidence outside the AB source scope. It is retained, not deleted or modified, and excluded from both frozen source trees.

## Candidate identity

The prior AB candidate `10652a2` is rejected as non-buildable. Repair candidate `f9f715e` is the only candidate with a successful Mac build. Source SHA: `042552fdcee4a3a9bbe3c27ae6df0f447596c4ef3999c63084544d043d41cb07`; executable SHA: `8e589e168fcb6addbc673dd4496b69e9dfe978afdbd855896a34a0170e7fbfc1`. Runtime evidence predating `f9f715e` is not reusable.

## Freeze state

This manifest update records scope only. The repair candidate awaits a new freeze and independent review. No install, restart, or black-box rerun is authorized by this step.

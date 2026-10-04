# Smallest Source-Owner Repair Design

Status: frozen design, not implemented.

## Decision

A repair is necessary. The smallest owner-correct correction is limited to
`.githooks/pre-push`.

Do not remove mainline protection. Do not add an environment token, registry,
payload field, log marker, or second control plane. Do not use `--no-verify`,
`core.hooksPath` overrides, hook removal, or a clone that evades the declared
local hook.

## Required Behavior

For a push containing `remote_ref refs/heads/main`:

1. Allow only `local_ref refs/heads/main`.
2. Require the checked-out branch to be `main`.
3. Require the pushed local SHA to equal `HEAD`.
4. Require `git status --porcelain` to be empty.
5. Require a non-zero remote SHA.
6. Require the remote SHA to be an ancestor of the pushed local SHA.
7. If all checks pass, run the existing `npm run verify:ci`.
8. If any check fails, exit non-zero before any remote update.

`refs/heads/master` remains rejected. Unrelated branches retain the existing
behavior: the hook runs `npm run verify:ci` and does not apply mainline-only
checks.

The exact reference implementation is
`harness/reference-pre-push.sh`. It is fixture evidence only. The product
change is the same logic in `.githooks/pre-push`.

## Why This Is The Minimum

- It preserves the existing rejection of direct candidate-to-main pushes.
- It preserves the existing rejection of dirty main pushes.
- It preserves fast-forward and non-fast-forward safety.
- It makes the declared `npm run verify:ci` gate reachable for main.
- It distinguishes normal feature-branch operations from the one
  contract-authorized local-main delivery edge.
- It does not claim that the hook proves independent review or candidate
  equivalence. Those remain preceding controller and review gates.

## Bootstrap Boundary

The fixed hook is delivered on a candidate branch. A candidate-to-main push is
still rejected. The first delivery must merge the reviewed candidate into a
clean local-main integration worktree. After that merge, the working tree's
`.githooks/pre-push` is the fixed version and the normal local-main push can
run the project gate.

The canonical main checkout is dirty/read-only in this environment and must
not be used as the integration surface. The controller must provide or select
a clean main integration worktree or equivalent clean-main candidate surface
before executing the push.

## Preserved Boundaries

- `.githooks/pre-commit` remains unchanged. Direct commits on main remain
  rejected. Merge commits are outside that hook's observed behavior.
- `scripts/setup/enable-local-protection.sh` remains unchanged.
- `package.json` and `npm run verify:ci` remain the project gate source.
- `.appsdk/**` lifecycle records and the controller DAG remain unchanged.
- No server-side protection is assumed or weakened.

## Success, Failure, Cancel, And Cleanup

Success: a clean local main whose remote ancestor relation is fast-forward
passes `npm run verify:ci` and reaches the declared remote.

Failure: candidate-to-main, dirty main, missing remote main, non-fast-forward,
or a failing project gate exits non-zero before the remote update.

Cancel: no state is created by the hook. The worktree and local main remain
unchanged.

Cleanup: only the task's owned fixture roots and raw evidence are retained for
controller review. No shared remote, canonical worktree, or other worker's
resource is removed.

## First Owner Handoff

The controller should hand the exact `.githooks/pre-push` implementation to
the source owner after design admission. The controller graph node
`集成远端并归档版本基线` already owns the downstream integration contract; no
parallel graph is needed.

# Implementation Contract

Status: `PARTIAL` until the source-owner candidate, actual project gate,
independent review, clean-main integration, and remote receipt exist.

## Allowed Product Change

Only `.githooks/pre-push` may change for the repair.

## Forbidden Changes

- `.githooks/pre-commit`
- `scripts/setup/enable-local-protection.sh`
- `package.json` or `package-lock.json`
- `.appsdk/**`
- `AGENTS.md`
- controller graph files
- product/runtime source
- any hook bypass, hook removal, hook rename, `core.hooksPath` override, or
  `--no-verify`

The run directory may contain the harness, reference hook, raw outputs, and
disposable fixtures. Those are audit evidence, not product source.

## Required Candidate Sequence

1. Create a clean source-owner candidate worktree from the latest
   `origin/main`.
2. Apply the behavior frozen in `repair-design.md` to `.githooks/pre-push`.
3. Run `git diff --check`.
4. Run the harness against the actual candidate hook:

   ```sh
   docs/runs/m1-mainline-delivery-audit-r1-20261004/harness/pre-push-blackbox.sh \
     <candidate>/.githooks/pre-push \
     <owned-fixture-root>
   ```

   Expected: six scenarios, zero failures.
5. Run the real project gate on the exact candidate:

   ```sh
   npm run verify:ci
   ```

   Record the exit status, candidate SHA, tree, and output path. A fixture gate
   result does not satisfy this step.
6. Submit the exact candidate to independent architecture review. This audit
   does not self-review or self-PASS.
7. After review PASS, fetch the latest `origin/main`; if it moved, refresh the
   candidate and rerun affected checks.
8. Create or select a clean local-main integration worktree. Merge the
   reviewed candidate. Confirm the integrated tree and `.githooks/pre-push`
   blob are equivalent to the reviewed candidate.
9. Run `npm run verify:ci` on the integrated main tree.
10. Push local `main` to `origin main` through the fixed hook.
11. Read back `origin/main` with `git ls-remote origin refs/heads/main` and
    compare it to the pushed commit.
12. Retain the candidate SHA, main merge SHA, hook blob/hash, gate logs,
    review PASS, push receipt, and cleanup evidence.

## Acceptance Matrix

| Case | Public entry | Expected |
| --- | --- | --- |
| Candidate branch to main | `git push <remote> candidate:refs/heads/main` | Rejected before gate |
| Clean local main | `git push <remote> main:refs/heads/main` | Gate runs, remote advances |
| Dirty local main | same with untracked or tracked change | Rejected before gate |
| Failing gate | clean main, project gate fails | Rejected, remote unchanged |
| Non-fast-forward | remote main diverges | Rejected, remote unchanged |
| Unrelated branch | `git push <remote> candidate:refs/heads/candidate` | Gate runs, branch advances |

## First Gap

No source-owner candidate exists yet. The first missing artifact is the exact
`.githooks/pre-push` implementation after controller design admission.

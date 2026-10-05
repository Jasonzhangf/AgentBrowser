# M1 Mainline Delivery Audit R1 Summary

Status: `AUDIT_READY`

## Conclusion

The observed denial is a real local protection defect, not a missing remote
push or a hidden AppSDK command. The existing `.githooks/pre-push` rejects all
main and master pushes before project verification. The declared AppSDK
delivery contract requires a protected local-main merge and push, and the
installed AppSDK CLI does not provide that push. A clean main merge itself
works; the first missing edge is the normal local-main push.

The smallest owner-correct repair is limited to `.githooks/pre-push`. It must
allow only a clean, fast-forward, checked-out local `main` whose pushed SHA is
`HEAD`, then run the existing `npm run verify:ci`. Candidate-to-main, dirty
main, failing gate, non-fast-forward, and master updates remain rejected.

## Evidence

| Evidence | Result |
| --- | --- |
| Direct observed hook stdin | exit `1`, `Protected mainline push rejected` |
| Actual `git push` to owned bare remote | exit `1`, no remote refs created |
| Current-hook six-case harness | exit `1`, 2 failures: clean main and failing gate |
| Reference-hook six-case harness | exit `0`, 0 failures |
| Controller graph validation | `dagpipe graph validate` exit `0`; graph SHA-256 `896e2c39105ca5bfb9c62bd770a2755e913035f72b793f3cde58cb960b45c2bb` |
| Product diff | empty; only run-directory docs/harness/raw/fixtures exist |

Raw files:

- `raw/hook-direct-main.stdout.txt`
- `raw/hook-direct-main.exit`
- `raw/git-push-main.stdout.txt`
- `raw/git-push-main.exit`
- `raw/git-push-main.refs.txt`
- `raw/pre-commit-merge-probe.stdout.txt`
- `raw/harness-current.stdout.txt`
- `raw/harness-current-r2.stdout.txt`
- `raw/harness-current-r2.exit`
- `raw/harness-reference.stdout.txt`
- `raw/harness-reference.exit`

## Unverified

- Actual project `npm run verify:ci` has not been run for this audit.
- The reference hook is fixture evidence, not a reviewed product candidate.
- No real `origin` push, merge, install, build, or review was performed.
- Server-side GitHub protection is unverified.
- OB delivery configuration is not reachable or proved from this AB worktree.

## First Gap And Next Action

First gap: no source-owner candidate implements the frozen `.githooks/pre-push`
change.

Next action: controller admits this design and assigns the exact
`.githooks/pre-push` implementation to a source-owner candidate; then run the
harness against the actual hook, run project `verify:ci` on the exact
candidate, obtain independent architecture review, integrate into a clean main
worktree, run `verify:ci` there, push through the fixed hook, and read back the
remote receipt.

This audit does not self-review or claim PASS.

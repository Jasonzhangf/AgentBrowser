# M1 Mainline Delivery Capability Audit

Status: `AUDIT_READY`

This audit is read-only for product source. It verifies the owned worktree,
traces the local Git protection and declared AppSDK delivery ownership, and
reproduces the observed rejection through the actual hook and a public Git
consumer. It does not implement the repair, perform a real origin push, run
project `verify:ci`, or claim review.

## Baseline

| Item | Observed value |
| --- | --- |
| Worktree | `/Volumes/Intel/playground/agentbrowser/m1-mainline-delivery-audit-r1-20261004` |
| Branch | `codex/m1-mainline-delivery-audit-r1-20261004` |
| HEAD | `bc9d67ad037be2c7119bee692f9ad362417ffd05` |
| `origin/main` at audit time | `bc9d67ad037be2c7119bee692f9ad362417ffd05` |
| `core.hooksPath` | `.githooks` from `file:/Volumes/extension/code/AgentBrowser/.git/config` |
| `pre-commit` blob / SHA-256 | `5a6e6cba30c8bac4fc779228d51bdbeabea89503` / `2d460b0ac0412d3ed5c5e3d81401bebb2f6e293be862107f2afe74639946fd8a` |
| `pre-push` blob / SHA-256 | `514865b13da09bd07ceeab45bfc923728f227e0a` / `d2212ec080fe15ec63ec6df4e5f6b736bfdb29a5a9e607c3157d362397698991` |
| Protection installer blob / SHA-256 | `db1679b510617e2e142fcdd6f740d6acbb7160e8` / `b1ec23953f572e78f7467141ea3d53e6eaa9494d28d3625b46ce4b7336ddbcfa` |

## Findings

### P0: No supported normal push path for an authorized clean local main

`scripts/setup/enable-local-protection.sh` sets `core.hooksPath` to the
versioned `.githooks` directory. The effective `pre-push` hook rejects every
destination `refs/heads/main` or `refs/heads/master` before it reaches
`npm run verify:ci`:

```sh
case "$remote_ref" in refs/heads/main|refs/heads/master) echo 'Protected mainline push rejected' >&2; exit 1;; esac
```

The declared AppSDK delivery contract requires the opposite final edge:

```text
exact integration build/test
-> protected merge/push
-> remote main receipt
```

The installed AppSDK CLI owns lifecycle and merge-admission records. It does
not expose a merge or protected-push command. Therefore no existing supported
entrypoint resolves the observed denial. The local-main push must be performed
by the integration owner through Git, and the local hook currently makes that
normal operation impossible without a forbidden bypass.

Evidence:

- `.githooks/pre-push` source, line 4.
- `.appsdk/docs/design/appsdk-project-integration.md`, lines 47-56.
- `.appsdk/skills/appsdk-project-governance/references/review-delivery.md`,
  lines 56-64.
- Direct hook invocation with the observed stdin exited `1` and printed
  `Protected mainline push rejected`.
- Actual `git push <owned-bare> HEAD:refs/heads/main` exited `1` with the same
  stderr. The owned bare remote remained empty.

### P0: The missing owner is the local protected-mainline push edge

The first missing edge is:

```text
reviewed candidate
-> clean-main integration tree
-> normal local-main push
-> remote receipt
```

The clean-main merge itself is not blocked. A disposable non-fast-forward merge
on `main` created a merge commit without invoking `pre-commit`; no
`pre-merge-commit` hook exists. The first failing edge is the push after the
merge.

The owner is the project Git protection surface, not the AppSDK record owner:

- `scripts/setup/enable-local-protection.sh` owns installation of the hook path.
- `.githooks/pre-push` owns the local mainline push decision.
- `agentbrowser.delivery.integrate_baseline@1` in the controller graph owns the
  delivery node that consumes that push edge.

The controller graph remains topologically valid and SESE. No parallel graph
or new registry is needed. The correction belongs in the implementation
contract of `集成远端并归档版本基线`.

### P1: Main pushes do not reach the declared project gate

The current hook rejects `refs/heads/main` before `npm run verify:ci`. The
fixture harness therefore observed:

- candidate branch to main: rejected before the fixture gate;
- clean local main to main: rejected before the fixture gate;
- failing fixture gate: never reached;
- unrelated branch: reached the fixture gate and was allowed.

This means the current hook does not distinguish a clean authorized main
delivery from a direct or dirty main push. It blocks both.

### P1: Server-side mainline enforcement is unverified

The repository has no `.github` workflow files in this checkout. The audit
observed `origin/main` only through read-only `git ls-remote`. No GitHub branch
protection or equivalent server-side policy was inspected or proved. This is
an unverified configuration, not a proved absence of remote enforcement.

### Boundary: `pre-commit` is not a merge gate

`pre-commit` rejects direct commits while the current branch is `main` or
`master`, but Git did not invoke it for a true merge commit in the disposable
probe. The merge succeeded. This is recorded as an observed lifecycle boundary,
not as a request to broaden the repair. The integration owner still owns the
required clean-main and verification checks outside the hook.

## AppSDK And OB Ownership

The AB contract names AppSDK lifecycle records for worktree, candidate,
pre-review validation, architecture review, effectiveness, integration,
mainline receipt, merge, promotion, and freeze. The installed AppSDK version is
`0.1.0010`; the project lock declares SDK `0.1.6`. AppSDK's declared mainline
map assigns remote observation to `appsdk::host_vcs` and merge admission to
`appsdk::merge_queue`, but neither provides a Git push command.

The read-only OB delivery path is not reachable from this AB worktree as a
runtime or repository artifact. No OB hook or remote-push configuration was
proved or changed. The audit therefore does not claim an OB failure or an OB
repair.

## Evidence Boundary

- Direct hook and actual local Git consumer exits are recorded under `raw/`.
- The six-case successor harness uses a fixture gate stub. It is not evidence
  that project `verify:ci` passed.
- No actual `origin` write, merge, install, Android build, or review was
  performed.
- The reference hook under `harness/reference-pre-push.sh` is design evidence,
  not product source.

## First Gap And Next Action

First gap: the authorized clean local-main push edge is absent in
`.githooks/pre-push`.

Next executable action: the controller admits this design, then a new
source-owner candidate implements the exact `.githooks/pre-push` change in
`repair-design.md`, runs the harness against the actual hook, runs project
`verify:ci` on the exact candidate, and submits the candidate to independent
review before clean-main integration.

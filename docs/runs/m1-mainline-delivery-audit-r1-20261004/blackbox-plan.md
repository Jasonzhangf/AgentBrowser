# Black-Box Successor Verification Plan

The harness is
`harness/pre-push-blackbox.sh`. It creates an owned disposable bare remote and
consumer repository under the supplied fixture root. It does not write to
`origin`.

## Fixture Gate

The harness puts a stub `npm` first on `PATH`. The stub records
`run verify:ci` and returns the requested status. This proves hook routing and
rejection behavior only. It does not prove project `verify:ci`.

The exact eventual candidate must separately run:

```sh
npm run verify:ci
```

on the candidate and on the integrated main tree.

## Cases

| Case | Setup | Expected result | Assertion |
| --- | --- | --- | --- |
| Candidate to main | candidate branch, remote main at base | Reject | Remote main unchanged; stub gate not called |
| Clean local main | local main ahead of remote main | Allow | Remote main equals local main; stub gate called |
| Dirty local main | clean-main setup plus untracked file | Reject | Remote main unchanged; stub gate not called |
| Failing gate | clean local main, stub gate exit 1 | Reject | Remote main unchanged; stub gate called |
| Non-fast-forward | remote main at divergent commit | Reject | Remote main unchanged |
| Unrelated branch | candidate branch to non-main remote ref | Allow | Remote candidate equals local candidate; stub gate called |

## Commands

Run against the current hook:

```sh
harness/pre-push-blackbox.sh .githooks/pre-push <owned-current-fixture-root>
```

Run against a successor candidate hook:

```sh
harness/pre-push-blackbox.sh <candidate>/.githooks/pre-push <owned-successor-fixture-root>
```

Run the actual hook consumer with the observed stdin:

```sh
printf '%s\n' \
  'refs/heads/codex/m1-repair-model-20261002 4aed7acfac95e3fe141b51f83538a614e5986927 refs/heads/main bc9d67ad037be2c7119bee692f9ad362417ffd05' \
  | .githooks/pre-push
```

## Observed Audit Results

- Current hook: exit `1`; clean-main and failing-gate scenarios fail; the
  direct observed main push is rejected before the gate.
- Reference hook: exit `0`; six scenarios, zero failures.
- The reference result is fixture-only. It is not a claim that the actual
  project gate passed.

## Retained Evidence

Raw exits and outputs are in `raw/`. The owned fixture roots are retained for
controller review:

- `fixtures/hook-consumer-remote.git`
- `fixtures/pre-commit-merge-probe-3-20261003`
- `fixtures/harness-current-20261003`
- `fixtures/harness-current-r2-20261003`
- `fixtures/harness-reference-20261003`

No product source, shared remote, or other worker's resource was modified by
these probes.

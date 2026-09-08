# AppSDK admission harness

`scripts/appsdk-admission.py` is the AgentBrowser-side admission entry point.
It observes a committed candidate and an external Obscura protocol checkout,
then runs the project gates in one serial process. It does not create or edit
AppSDK lifecycle records; AppSDK remains the owner of record validation and the
final admission decision.

Run it from the candidate worktree after committing the candidate:

```sh
OBSCURA_PROTOCOL_ROOT=/absolute/path/to/obscura/protocol/browser \
  python3 scripts/appsdk-admission.py \
  --evidence-dir /tmp/agentbrowser-appsdk-admission-harness-20260908
```

`OBSCURA_PROTOCOL_ROOT` may also be supplied as `--protocol-root`. The path
must be a directory in a clean Obscura Git checkout containing both
`Cargo.toml` and `src/lib.rs`. The harness records the Obscura repository
commit/tree and the protocol source subtree tree. It passes the exact absolute
path through the environment; it never copies protocol source into
AgentBrowser. If `scripts/connection.py` exists, the harness requires its
explicit `OBSCURA_PROTOCOL_ROOT` binding and a Cargo path patch. A design-only
checkout with no Rust manifest records that adapter as not applicable.

The serial order is:

1. Require a clean non-`main` candidate and capture `HEAD`, `HEAD^{tree}`, the
   `origin/main` base, and changed paths.
2. Bind and hash the external protocol source. Reject a dirty or untracked
   Obscura source and any copied `protocol/browser/**` path in AgentBrowser.
3. Install locked dependencies discovered from tracked lockfiles. `npm ci`,
   `pnpm install --frozen-lockfile`, and `cargo fetch --locked` are run from
   their owning manifest directory. When `scripts/connection.py` declares a
   Cargo path patch, the fetch receives the same explicit protocol path.
4. Run `appsdk verify` once and `appsdk verify --admission` once. Both command
   logs and the resolved AppSDK binary path, size, and SHA-256 are recorded.
5. Only when both verify commands pass, run `appsdk compile` exactly once with
   `OBSCURA_PROTOCOL_ROOT` in its environment. No compile retry or fallback is
   attempted.
6. Bind every generated compiled manifest and each declared module artifact to
   its manifest hash and output hash. Candidate and protocol commit/tree are
   checked again after compile.

The first failure is emitted as JSON and written to
`<evidence-dir>/<candidate-commit-prefix>-admission-<UTC>/admission-witness.json`.
The witness contains the failing stage/code, preserved command output log,
`retry_allowed: false`, canonical owner, and one next action. A blocked result
is evidence of the blocker only; it is never converted into a PASS. Missing
protocol, dependencies, AppSDK inputs, goal confirmation, or compiled output
therefore remain explicit failures.

The helper's own writes are limited to the requested evidence directory. It
does not write `.appsdk/records/**`, `.appsdk/sdk.lock`, hash/freeze state, or
Obscura files. `appsdk compile` may create its normal ignored `generated/**`
outputs; those outputs are hashed and reported separately from lifecycle
records. Generated output does not prove install, restart, deployed-entrypoint
replay, review, merge, or product acceptance.

## Baseline observed in the design checkout

At the `origin/main` design baseline (`eed395cf49d5c7a514d91c6efc3eba4aba7671d5`),
there is no Rust or package dependency manifest and no `scripts/connection.py`.
The direct AppSDK commands were observed separately before this helper was
added:

```text
appsdk verify             -> exit 1, DECLARED_RECORD_CONTRACT_MISMATCH
appsdk verify --admission -> exit 1, DECLARED_RECORD_CONTRACT_MISMATCH
appsdk compile            -> exit 1, GOAL_NOT_CONFIRMED:received
```

The design checkout has no real Obscura protocol root available. Running the
harness without `OBSCURA_PROTOCOL_ROOT` therefore records
`OBSCURA_PROTOCOL_ROOT_UNSET` as the first failure and does not invoke compile.
That result preserves the missing external input; it does not claim an AppSDK
or product admission.

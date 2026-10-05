# M1 Mainline Delivery Audit R1 Notes

Format: `time/node | status/conclusion | evidence | version/environment | next`

2026-10-03T21:02:13-0700 / worktree-anchor | confirmed | `pwd`, `git status --short --branch`, `git rev-parse HEAD`, `git worktree list --porcelain` | owned worktree `/Volumes/Intel/playground/agentbrowser/m1-mainline-delivery-audit-r1-20261004`; branch `codex/m1-mainline-delivery-audit-r1-20261004`; HEAD `bc9d67ad037be2c7119bee692f9ad362417ffd05`; clean | trace hooks and config

2026-10-03T21:02:13-0700 / hook-source-trace | confirmed | `.githooks/pre-commit`, `.githooks/pre-push`, `scripts/setup/enable-local-protection.sh`, `package.json` | source HEAD above; `core.hooksPath=.githooks` from common config `/Volumes/extension/code/AgentBrowser/.git/config`; `pre-push` rejects `refs/heads/main` and `refs/heads/master` before `npm run verify:ci` | run actual public Git consumer against owned disposable bare remote

2026-10-03T21:02:13-0700 / delivery-contract-trace | confirmed | `.appsdk/docs/design/appsdk-project-integration.md`, `.appsdk/skills/appsdk-project-governance/references/review-delivery.md`, `.appsdk/maps/mainline-call-map.json`, `.appsdk/maps/verification-map.json` | AppSDK owns lifecycle/merge-admission records and remote receipt gates; checked-in project contract names protected merge/push as a required edge; installed AppSDK CLI source exposes no `merge`/`push` command | inspect graph handoff and design smallest hook fix

2026-10-03T21:02:13-0700 / controller-graph | confirmed | `/Volumes/Intel/playground/agentbrowser/m1-repair-model-20261002/docs/dagpipe/graphs/m1-delivery.graph.json`; `dagpipe graph validate` exit 0; graph SHA-256 `896e2c39105ca5bfb9c62bd770a2755e913035f72b793f3cde58cb960b45c2bb` | graph is a valid 7-node SESE graph; `集成远端并归档版本基线` already owns candidate-to-remote integration; no parallel graph needed | map hook correction into that node's implementation contract

2026-10-03T21:02:13-0700 / remote-read-only | observed | `git ls-remote --heads origin`, `git ls-remote origin refs/heads/main` | remote `refs/heads/main` is `bc9d67ad037be2c7119bee692f9ad362417ffd05`; no write performed | run owned local bare-remote hook probes

2026-10-03T21:02:13-0700 / actual-hook-rejection | reproduced | direct `.githooks/pre-push` with observed stdin, exit 1, stderr `Protected mainline push rejected`; actual `git push <owned-bare> HEAD:refs/heads/main`, exit 1, same stderr; bare refs empty | source HEAD `bc9d67ad...`; owned fixture `docs/runs/m1-mainline-delivery-audit-r1-20261004/fixtures/hook-consumer-remote.git`; no origin write | design successor hook behavior and black-box matrix

2026-10-03T21:02:13-0700 / pre-commit-merge-fixture-error | failed/superseded | first merge fixture installed hooks before creating base commits; base commit on `main` was correctly blocked, so the merge probe was invalid | owned fixture only; no product change; actual `npm` ran because fixture commit ordering was wrong | recreate fixture with commits before hook installation

2026-10-03T21:02:13-0700 / pre-commit-merge-behavior | confirmed | corrected owned fixture: commits created before hooks, then a non-fast-forward merge on `main`; `git merge --no-edit candidate` exit 0; output `Merge made by the 'ort' strategy`; no `npm` invocation | fixture `docs/runs/m1-mainline-delivery-audit-r1-20261004/fixtures/pre-commit-merge-probe-3-20261003`; source hook blob `5a6e6cba30c8bac4fc779228d51bdbeabea89503` | design pre-push-only correction unless further evidence shows a merge gate

2026-10-03T21:02:13-0700 / blackbox-fixture-initial-error | failed/superseded | first harness run failed because the bare remote was seeded before it had base objects | `raw/harness-current.stdout.txt`, `raw/harness-current.exit`; owned fixture `fixtures/harness-current-20261003` | patch test harness only and rerun

2026-10-03T21:02:13-0700 / blackbox-current-hook-r2 | confirmed red | current `.githooks/pre-push` harness exit 1; candidate->main rejected, dirty main rejected, non-fast-forward rejected by Git, unrelated branch allowed; clean local main rejected and failing gate was not reached | `raw/harness-current-r2.stdout.txt`, `raw/harness-current-r2.exit`; hook SHA-256 `d2212ec080fe15ec63ec6df4e5f6b736bfdb29a5a9e607c3157d362397698991` | run same harness against reference hook

2026-10-03T21:02:13-0700 / blackbox-reference-hook | confirmed fixture green | reference `harness/reference-pre-push.sh` exit 0; all 6 scenarios passed: clean local main allowed only after fixture gate, candidate->main/dirty/failing-gate/non-fast-forward rejected, unrelated branch allowed | `raw/harness-reference.stdout.txt`, `raw/harness-reference.exit`; fixture-only gate stub, not actual `verify:ci` | freeze exact source repair and actual successor verification contract

2026-10-03T21:02:13-0700 / docs-hook-dependencies | passed | `node_modules` absent; ran `npm ci` in owned worktree; exit 0, 17 packages, no vulnerabilities | `raw/npm-ci.stdout.txt`; package-lock `package-lock.json` unchanged | stage and commit run-directory docs/harness/raw through existing hook

2026-10-03T21:17:29-0700 / docs-commit | passed | committed run-directory docs/harness/raw through existing `pre-commit` hook (`npm run verify:local`); product diff vs `bc9d67ad` empty | `raw/commit.stdout.txt`; HEAD `42fa0465d0c69a879c7d360cce1e9e73c1ac94ed`, tree `e9bfeb1a5e5dd347f8ff6967392b6b196886084b` | final verify + append final hash note

2026-10-03T21:22:15-0700 / final-verify | passed | `git status --short --branch` shows only untracked owned `fixtures/`; `git diff --check HEAD^ HEAD` exit 0; non-run product diff empty; committed tree is run directory only | HEAD `42fa0465d0c69a879c7d360cce1e9e73c1ac94ed`, tree `e9bfeb1a5e5dd347f8ff6967392b6b196886084b`; `raw/` and `harness/` committed | report AUDIT_READY to controller

2026-10-03T21:22:53-0700 / notes-commit | passed | committed this notes final-hash entry through the existing `pre-commit` hook (`npm run verify:local`: typecheck, 8 tests, build:ui all pass); output captured | HEAD `154831f7c52bccdda8c88300200b16695474ebe1`, tree `8d9f8f905316d1c0d9edfc59272ffa1233b38226`; `raw/final-commit.stdout.txt` | add final raw evidence and close audit

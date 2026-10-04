#!/bin/sh
set -eu

usage() {
  echo "usage: $0 <hook-path> [fixture-root]" >&2
  exit 2
}

[ "$#" -ge 1 ] || usage
hook=$1
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=${2:-"$script_dir/../fixtures/harness-$(date +%Y%m%dT%H%M%S)"}

[ -f "$hook" ] || { echo "missing hook: $hook" >&2; exit 2; }
if [ -e "$root" ]; then
  echo "fixture root already exists: $root" >&2
  exit 2
fi

mkdir -p "$root"
root=$(CDPATH= cd -- "$root" && pwd)
repo=
bare=
bin=
gate_log=
base_sha=
main_sha=
candidate_sha=
scenario_count=0
failure_count=0

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

setup() {
  name=$1
  case_dir="$root/$name"
  repo="$case_dir/repo"
  bare="$case_dir/remote.git"
  bin="$case_dir/bin"
  gate_log="$case_dir/gate.log"
  mkdir -p "$repo/.githooks" "$bin"
  cp "$hook" "$repo/.githooks/pre-push"
  chmod +x "$repo/.githooks/pre-push"
  printf '%s\n' \
    '#!/bin/sh' \
    'printf "%s\\n" "$*" >>"$PROBE_GATE_LOG"' \
    'exit "${PROBE_GATE_EXIT:-0}"' >"$bin/npm"
  chmod +x "$bin/npm"

  git init -q -b main "$repo"
  git -C "$repo" config user.name "Pre-push Fixture"
  git -C "$repo" config user.email "pre-push-fixture@example.invalid"
  printf 'base\n' >"$repo/README"
  git -C "$repo" add README .githooks/pre-push
  git -C "$repo" commit -q -m base
  base_sha=$(git -C "$repo" rev-parse HEAD)

  git -C "$repo" switch -q -c candidate
  printf 'candidate\n' >>"$repo/README"
  git -C "$repo" commit -q -am candidate
  candidate_sha=$(git -C "$repo" rev-parse HEAD)

  git -C "$repo" switch -q main
  printf 'main\n' >"$repo/main.txt"
  git -C "$repo" add main.txt
  git -C "$repo" commit -q -m main-update
  main_sha=$(git -C "$repo" rev-parse HEAD)

  git init -q --bare "$bare"
  git --git-dir="$bare" fetch -q "$repo" "$base_sha"
  git --git-dir="$bare" update-ref refs/heads/main "$base_sha"
  git -C "$repo" config core.hooksPath .githooks
}

run_push() {
  gate_exit=$1
  refspec=$2
  : >"$gate_log"
  push_rc=0
  push_output=$(PROBE_GATE_LOG="$gate_log" PROBE_GATE_EXIT="$gate_exit" \
    PATH="$bin:$PATH" git -C "$repo" push "$bare" "$refspec" 2>&1) || push_rc=$?
  printf '%s\n' "$push_output"
  return "$push_rc"
}

remote_main() {
  git --git-dir="$bare" rev-parse --verify refs/heads/main 2>/dev/null || true
}

remote_branch() {
  git --git-dir="$bare" rev-parse --verify refs/heads/candidate 2>/dev/null || true
}

assert_gate_ran() {
  if [ ! -s "$gate_log" ] || ! grep -q 'run verify:ci' "$gate_log"; then
    fail "expected fixture gate run verify:ci"
  fi
}

assert_gate_not_run() {
  if [ -s "$gate_log" ]; then
    fail "fixture gate ran before the hook rejected the push"
  fi
}

scenario_candidate_to_main() {
  setup candidate-to-main
  if run_push 0 candidate:refs/heads/main; then
    fail "candidate branch was allowed to update main"
  fi
  if [ "$(remote_main)" != "$base_sha" ]; then
    fail "remote main changed after rejected candidate push"
  fi
  assert_gate_not_run
}

scenario_clean_main() {
  setup clean-main
  if ! run_push 0 main:refs/heads/main; then
    fail "clean local main push failed"
  fi
  if [ "$(remote_main)" != "$main_sha" ]; then
    fail "remote main did not reach clean local main"
  fi
  assert_gate_ran
}

scenario_dirty_main() {
  setup dirty-main
  printf 'dirty\n' >"$repo/dirty.txt"
  if run_push 0 main:refs/heads/main; then
    fail "dirty local main was allowed to update main"
  fi
  if [ "$(remote_main)" != "$base_sha" ]; then
    fail "remote main changed after dirty push rejection"
  fi
  assert_gate_not_run
}

scenario_failing_gate() {
  setup failing-gate
  if run_push 1 main:refs/heads/main; then
    fail "failing fixture gate was allowed to update main"
  fi
  if [ "$(remote_main)" != "$base_sha" ]; then
    fail "remote main changed after failing gate"
  fi
  assert_gate_ran
}

scenario_non_fast_forward() {
  setup non-fast-forward
  git -C "$repo" switch -q --detach "$base_sha"
  printf 'remote-side\n' >"$repo/remote.txt"
  git -C "$repo" add remote.txt
  git -C "$repo" commit -q -m remote-side
  remote_side_sha=$(git -C "$repo" rev-parse HEAD)
  git --git-dir="$bare" fetch -q "$repo" "$remote_side_sha"
  git --git-dir="$bare" update-ref refs/heads/main "$remote_side_sha"
  git -C "$repo" switch -q main
  if run_push 0 main:refs/heads/main; then
    fail "non-fast-forward main update was allowed"
  fi
  if [ "$(remote_main)" != "$remote_side_sha" ]; then
    fail "remote main changed after non-fast-forward rejection"
  fi
}

scenario_unrelated_branch() {
  setup unrelated-branch
  if ! run_push 0 candidate:refs/heads/candidate; then
    fail "unrelated branch push failed"
  fi
  if [ "$(remote_branch)" != "$candidate_sha" ]; then
    fail "remote candidate branch did not reach candidate"
  fi
  assert_gate_ran
}

run_scenario() {
  name=$1
  scenario_count=$((scenario_count + 1))
  printf '\n=== scenario: %s ===\n' "$name"
  if ( "$name" ); then
    printf 'RESULT PASS %s\n' "$name"
  else
    printf 'RESULT FAIL %s\n' "$name"
    failure_count=$((failure_count + 1))
  fi
}

printf 'hook=%s\nfixture_root=%s\n' "$hook" "$root"
run_scenario scenario_candidate_to_main
run_scenario scenario_clean_main
run_scenario scenario_dirty_main
run_scenario scenario_failing_gate
run_scenario scenario_non_fast_forward
run_scenario scenario_unrelated_branch

printf '\nsummary: %s scenarios, %s failures\n' "$scenario_count" "$failure_count"
[ "$failure_count" -eq 0 ]

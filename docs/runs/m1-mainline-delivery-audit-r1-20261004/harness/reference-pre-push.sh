#!/bin/sh
set -eu

while read -r local_ref local_sha remote_ref remote_sha; do
  case "$remote_ref" in
    refs/heads/main)
      if [ "$local_ref" != "refs/heads/main" ]; then
        echo 'Protected mainline push rejected: only local main may update main' >&2
        exit 1
      fi
      current_branch=$(git symbolic-ref --quiet --short HEAD || true)
      if [ "$current_branch" != "main" ]; then
        echo 'Protected mainline push rejected: main must be checked out' >&2
        exit 1
      fi
      if [ "$local_sha" != "$(git rev-parse HEAD)" ]; then
        echo 'Protected mainline push rejected: pushed main is not HEAD' >&2
        exit 1
      fi
      if [ -n "$(git status --porcelain)" ]; then
        echo 'Protected mainline push rejected: worktree is dirty' >&2
        exit 1
      fi
      case "$remote_sha" in
        0000000000000000000000000000000000000000)
          echo 'Protected mainline push rejected: remote main is missing' >&2
          exit 1
          ;;
      esac
      if ! git merge-base --is-ancestor "$remote_sha" "$local_sha"; then
        echo 'Protected mainline push rejected: non-fast-forward update' >&2
        exit 1
      fi
      ;;
    refs/heads/master)
      echo 'Protected mainline push rejected' >&2
      exit 1
      ;;
  esac
done

npm run verify:ci

#!/bin/zsh
set -u
if [ $# -lt 2 ]; then
  printf 'usage: run.sh <logbase> <command...>\n' >&2
  exit 2
fi
logbase=$1
shift
"$@" > "$logbase.log" 2>&1
code=$?
printf 'EXIT=%s\n' "$code" > "$logbase.exit"
exit "$code"

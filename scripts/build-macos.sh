#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "$0")/.." && pwd)"
mode="${1:-build}"
if [[ "$mode" != "build" && "$mode" != "run" ]]; then
  printf '%s\n' 'usage: scripts/build-macos.sh [build|run] [--pairing-dir DIR]' >&2
  exit 2
fi

protocol_root="${OBSCURA_PROTOCOL_ROOT:?Set OBSCURA_PROTOCOL_ROOT to the read-only Obscura protocol/browser owner}"
protocol_root="$(cd "$protocol_root" && pwd)"
if [[ ! -f "$protocol_root/src/lib.rs" ]]; then
  printf '%s\n' 'Obscura protocol source entry missing' >&2
  exit 2
fi

cd "$root_dir"
node scripts/build-macos-ui.mjs

CARGO_BUILD_JOBS=2 CARGO_INCREMENTAL=0 cargo build --release \
  -p agentbrowser-macos-bridge \
  --config "patch.crates-io.obscura-host-protocol.path=\"$protocol_root\""

app_dir="$root_dir/apps/macos/build/AgentBrowserMac.app"
mkdir -p "$app_dir/Contents/MacOS" "$app_dir/Contents/Resources"
cp apps/macos/Info.plist "$app_dir/Contents/Info.plist"
cp target/release/agentbrowser-macos-bridge "$app_dir/Contents/MacOS/AgentBrowserMacBridge"
mkdir -p "$app_dir/Contents/Resources/ui"
cp -R apps/macos/build/ui/. "$app_dir/Contents/Resources/ui/"
xcrun swiftc -O apps/macos/Sources/main.swift \
  -o "$app_dir/Contents/MacOS/AgentBrowserMac" \
  -framework AppKit \
  -framework WebKit \
  -framework VideoToolbox \
  -framework CoreMedia \
  -framework CoreVideo \
  -framework CoreImage \
  -framework QuartzCore

# AppSDK consumes the exact built bundle from the generated module artifact.
artifact_dir="$root_dir/generated/modules/macos-shell/lib"
mkdir -p "$artifact_dir"
rm -rf "$artifact_dir/AgentBrowserMac.app"
cp -R "$app_dir" "$artifact_dir/AgentBrowserMac.app"
ditto -c -k --norsrc --keepParent \
  "$app_dir" "$artifact_dir/AgentBrowserMac.app.zip"

if [[ "$mode" == "run" ]]; then
  shift
  exec "$app_dir/Contents/MacOS/AgentBrowserMac" "$@"
fi

printf '%s\n' "$app_dir"

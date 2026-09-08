#!/bin/sh

set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
manifest_path=${LOCAL_DIRECT_MANIFEST_PATH:-"$repo_root/Cargo.toml"}
target_dir=${LOCAL_DIRECT_TARGET_DIR:-${CARGO_TARGET_DIR:-"$repo_root/target"}}

if [ ! -f "$manifest_path" ]; then
    printf 'local-direct: fixture build unavailable: Cargo workspace manifest not found: %s\n' "$manifest_path" >&2
    exit 78
fi

fixture_source="$repo_root/packages/android-bridge/examples/device_fixture.rs"
if [ ! -f "$fixture_source" ]; then
    printf 'local-direct: fixture build unavailable: source not found: %s\n' "$fixture_source" >&2
    exit 78
fi

cargo build \
    --manifest-path "$manifest_path" \
    --example device_fixture \
    -p agentbrowser-android >&2

fixture_bin="$target_dir/debug/examples/device_fixture"
if [ ! -x "$fixture_bin" ]; then
    printf 'local-direct: cargo build completed without executable fixture: %s\n' "$fixture_bin" >&2
    exit 78
fi

printf '%s\n' "$fixture_bin"

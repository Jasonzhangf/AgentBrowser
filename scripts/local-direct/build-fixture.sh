#!/bin/sh

set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
manifest_path=${LOCAL_DIRECT_MANIFEST_PATH:-"$repo_root/Cargo.toml"}

protocol_root_input=${OBSCURA_PROTOCOL_ROOT:-${LOCAL_DIRECT_PROTOCOL_ROOT:-}}
if [ -z "$protocol_root_input" ]; then
    printf 'local-direct: fixture build unavailable: set OBSCURA_PROTOCOL_ROOT or LOCAL_DIRECT_PROTOCOL_ROOT to the Obscura protocol crate root\n' >&2
    exit 78
fi

if [ ! -d "$protocol_root_input" ]; then
    printf 'local-direct: fixture build unavailable: protocol root is not a directory: %s\n' "$protocol_root_input" >&2
    exit 78
fi

protocol_root=$(CDPATH= cd -- "$protocol_root_input" && pwd)
if [ ! -f "$protocol_root/Cargo.toml" ]; then
    printf 'local-direct: fixture build unavailable: protocol Cargo.toml not found: %s/Cargo.toml\n' "$protocol_root" >&2
    exit 78
fi

if [ ! -f "$protocol_root/src/lib.rs" ]; then
    printf 'local-direct: fixture build unavailable: protocol source entry not found: %s/src/lib.rs\n' "$protocol_root" >&2
    exit 78
fi

if [ ! -f "$manifest_path" ]; then
    printf 'local-direct: fixture build unavailable: Cargo workspace manifest not found: %s\n' "$manifest_path" >&2
    exit 78
fi

manifest_dir=$(CDPATH= cd -- "$(dirname -- "$manifest_path")" && pwd)
target_dir=${LOCAL_DIRECT_TARGET_DIR:-${CARGO_TARGET_DIR:-"$manifest_dir/target"}}
fixture_source="$manifest_dir/packages/android-bridge/examples/device_fixture.rs"
if [ ! -f "$fixture_source" ]; then
    printf 'local-direct: fixture build unavailable: source not found: %s\n' "$fixture_source" >&2
    exit 78
fi

cargo build \
    --manifest-path "$manifest_path" \
    --example device_fixture \
    -p agentbrowser-android \
    --target-dir "$target_dir" \
    --config "patch.crates-io.obscura-host-protocol.path=\"$protocol_root\"" >&2

fixture_bin="$target_dir/debug/examples/device_fixture"
if [ ! -x "$fixture_bin" ]; then
    printf 'local-direct: cargo build completed without executable fixture: %s\n' "$fixture_bin" >&2
    exit 78
fi

printf '%s\n' "$fixture_bin"

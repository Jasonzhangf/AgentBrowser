#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../apps/android"
exec ./gradlew --console=plain "$@"

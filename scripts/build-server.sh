#!/bin/bash
# Build the product-only Python sidecar from the committed uv lock.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Determine the exact Rust host tuple used by Tauri. Refuse an unavailable or
# malformed toolchain instead of producing an unusable `-unknown` sidecar name.
PLATFORM=$(rustc -vV | sed -n 's/^host: //p')
if [[ ! "$PLATFORM" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{2,127}$ ]]; then
    echo "Error: unable to determine a valid Rust host tuple"
    exit 1
fi

echo "Building voicebox-server for platform: $PLATFORM"

# Never mutate the environment or resolve dependencies during a release build.
uv run --frozen --isolated --no-default-groups --group build python backend/build_binary.py

# Create binaries directory if it doesn't exist
mkdir -p tauri/src-tauri/binaries

# Copy binary with platform suffix
if [ -f backend/dist/voicebox-server ]; then
    cp backend/dist/voicebox-server "tauri/src-tauri/binaries/voicebox-server-${PLATFORM}"
    chmod +x "tauri/src-tauri/binaries/voicebox-server-${PLATFORM}"
    echo "Built voicebox-server-${PLATFORM}"
elif [ -f backend/dist/voicebox-server.exe ]; then
    cp backend/dist/voicebox-server.exe "tauri/src-tauri/binaries/voicebox-server-${PLATFORM}.exe"
    echo "Built voicebox-server-${PLATFORM}.exe"
else
    echo "Error: Binary not found in dist/"
    exit 1
fi

cp backend/dist/voicebox-server.provenance.json \
  "tauri/src-tauri/binaries/voicebox-server-${PLATFORM}.provenance.json"

echo "Build complete!"

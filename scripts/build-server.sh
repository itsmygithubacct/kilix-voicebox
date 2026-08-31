#!/bin/bash
# Build the product-only Python sidecar from the committed uv lock.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Determine platform
PLATFORM=$(rustc --print host-tuple 2>/dev/null || echo "unknown")

echo "Building voicebox-server for platform: $PLATFORM"

# Never mutate the environment or resolve dependencies during a release build.
uv run --frozen --group build python backend/build_binary.py

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

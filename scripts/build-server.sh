#!/bin/bash
# Build the product-only Python sidecar from the committed uv lock.
#
# The producer owns one fresh staging directory per invocation and finishes by
# writing a handoff that names and hashes exactly the outputs that invocation
# produced. This script collects only what that handoff names, so a build that
# fails or produces nothing cannot fall back to an earlier build's binary or
# provenance, and the files installed for Tauri are always this build's.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

HANDOFF_SCHEMA="kilix.voicebox.sidecar-output/candidate-v1"
HANDOFF_NAME="sidecar-outputs"
EXPECTED_PROVENANCE="dist/voicebox-server.provenance.json"

if command -v sha256sum >/dev/null 2>&1; then
    sha256_of() { sha256sum -- "$1" | cut -d ' ' -f 1; }
elif command -v shasum >/dev/null 2>&1; then
    sha256_of() { shasum -a 256 -- "$1" | cut -d ' ' -f 1; }
else
    echo "Error: no SHA-256 tool is available to verify the build outputs" >&2
    exit 1
fi

# `is a regular file` is asserted separately from `is not a symbolic link`,
# because -f follows links: a link planted in the staging or destination
# directory would otherwise let a build collect, or write through to, a file
# outside the outputs this invocation produced.
collect_output() {
    local source="$1" expected="$2" destination="$3" description="$4" actual
    if [ -L "$source" ] || [ ! -f "$source" ]; then
        echo "Error: $description is not a regular file: $source" >&2
        exit 1
    fi
    actual="$(sha256_of "$source")"
    if [ "$actual" != "$expected" ]; then
        echo "Error: $description does not match the identity this build recorded" >&2
        exit 1
    fi
    if [ -L "$destination" ]; then
        echo "Error: sidecar destination is a symbolic link: $destination" >&2
        exit 1
    fi
    rm -f -- "$destination"
    cp -- "$source" "$destination"
}

# Determine the exact Rust host tuple used by Tauri. Refuse an unavailable or
# malformed toolchain instead of producing an unusable `-unknown` sidecar name.
PLATFORM=$(rustc -vV | sed -n 's/^host: //p')
if [[ ! "$PLATFORM" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{2,127}$ ]]; then
    echo "Error: unable to determine a valid Rust host tuple"
    exit 1
fi

echo "Building voicebox-server for platform: $PLATFORM"

# A fresh identity for this invocation. It names the staging directory the
# producer must create, and the producer repeats it in the handoff, so outputs
# left by any other build are refused below rather than shipped.
BUILD_ID="$(od -An -v -t x1 -N 16 /dev/urandom | tr -d ' \n')"
if [[ ! "$BUILD_ID" =~ ^[0-9a-f]{32}$ ]]; then
    echo "Error: unable to generate a build identity" >&2
    exit 1
fi

STAGE_ROOT="$REPO_ROOT/kilix_voicebox_backend/build"
if [ -L "$STAGE_ROOT" ]; then
    echo "Error: staging root is a symbolic link: $STAGE_ROOT" >&2
    exit 1
fi
mkdir -p "$STAGE_ROOT"
STAGE="$STAGE_ROOT/stage-$BUILD_ID"
if [ -e "$STAGE" ] || [ -L "$STAGE" ]; then
    echo "Error: staging directory already exists: $STAGE" >&2
    exit 1
fi
trap 'rm -rf -- "$STAGE"' EXIT

# Never let caller-controlled Python startup state reach PyInstaller's child
# interpreters.  The explicit environment also keeps the locked uv execution
# independent of user uv configuration while retaining only the paths needed
# to locate the selected build tools and scratch directory.  The staging
# directory and build identity are passed as arguments, so this build's output
# selection does not depend on any environment variable surviving that filter.
env -i \
    HOME="${HOME:?HOME is required for the locked build cache}" \
    PATH="$PATH" \
    TMPDIR="${TMPDIR:-/tmp}" \
    LC_ALL=C.UTF-8 \
    PYTHONNOUSERSITE=1 \
    PYTHONSAFEPATH=1 \
    uv --no-config run --frozen --isolated --no-default-groups --group build \
        python -I kilix_voicebox_backend/build_binary.py \
            --stage-dir "$STAGE" --build-id "$BUILD_ID"

HANDOFF="$STAGE/$HANDOFF_NAME"
if [ -L "$HANDOFF" ] || [ ! -f "$HANDOFF" ]; then
    echo "Error: the build produced no sidecar handoff at $HANDOFF" >&2
    exit 1
fi

handoff_schema= handoff_build_id= handoff_binary= handoff_binary_sha256=
handoff_binary_bytes= handoff_provenance= handoff_provenance_sha256=
while IFS='=' read -r key value; do
    case "$key" in
        schema) handoff_schema="$value" ;;
        build_id) handoff_build_id="$value" ;;
        binary) handoff_binary="$value" ;;
        binary_sha256) handoff_binary_sha256="$value" ;;
        binary_bytes) handoff_binary_bytes="$value" ;;
        provenance) handoff_provenance="$value" ;;
        provenance_sha256) handoff_provenance_sha256="$value" ;;
        *)
            echo "Error: unrecognized sidecar handoff field: $key" >&2
            exit 1
            ;;
    esac
done < "$HANDOFF"

if [ "$handoff_schema" != "$HANDOFF_SCHEMA" ]; then
    echo "Error: unsupported sidecar handoff schema: $handoff_schema" >&2
    exit 1
fi
if [ "$handoff_build_id" != "$BUILD_ID" ]; then
    echo "Error: sidecar handoff was not produced by this build" >&2
    exit 1
fi
case "$handoff_binary" in
    dist/voicebox-server) SUFFIX="" ;;
    dist/voicebox-server.exe) SUFFIX=".exe" ;;
    *)
        echo "Error: sidecar handoff names an unexpected binary: $handoff_binary" >&2
        exit 1
        ;;
esac
if [ "$handoff_provenance" != "$EXPECTED_PROVENANCE" ]; then
    echo "Error: sidecar handoff names an unexpected provenance file: $handoff_provenance" >&2
    exit 1
fi
if [[ ! "$handoff_binary_sha256" =~ ^[0-9a-f]{64}$ ]] \
    || [[ ! "$handoff_provenance_sha256" =~ ^[0-9a-f]{64}$ ]] \
    || [[ ! "$handoff_binary_bytes" =~ ^[0-9]+$ ]]; then
    echo "Error: sidecar handoff carries a malformed output identity" >&2
    exit 1
fi

BINARIES_DIR="$REPO_ROOT/tauri/src-tauri/binaries"
if [ -L "$BINARIES_DIR" ]; then
    echo "Error: sidecar destination is a symbolic link: $BINARIES_DIR" >&2
    exit 1
fi
mkdir -p "$BINARIES_DIR"

DESTINATION="$BINARIES_DIR/voicebox-server-${PLATFORM}${SUFFIX}"
collect_output "$STAGE/$handoff_binary" "$handoff_binary_sha256" \
    "$DESTINATION" "produced sidecar"
chmod +x "$DESTINATION"
if [ "$(wc -c < "$DESTINATION" | tr -d ' ')" != "$handoff_binary_bytes" ]; then
    echo "Error: collected sidecar size differs from the recorded size" >&2
    exit 1
fi
echo "Built voicebox-server-${PLATFORM}${SUFFIX} ($handoff_binary_bytes bytes)"

collect_output "$STAGE/$handoff_provenance" "$handoff_provenance_sha256" \
    "$BINARIES_DIR/voicebox-server-${PLATFORM}.provenance.json" \
    "produced provenance"

echo "Build complete!"

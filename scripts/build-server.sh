#!/bin/bash
# Build the product-only Python sidecar from the committed uv lock.
#
# The producer owns one fresh staging directory per invocation and finishes by
# writing a handoff that names and hashes exactly the outputs that invocation
# produced. This script collects only what that handoff names, so a build that
# fails or produces nothing cannot fall back to an earlier build's binary or
# provenance. Publication retains the previous pair until both replacements
# succeed; it is failure-consistent, not a crash-atomic two-file transaction.

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

# Check lexical components BEFORE mkdir/read/copy, not a resolved path that
# conceals an alias. This is trusted private-tree hygiene, not a lock against
# another owner changing directories concurrently. Callers pass paths in REPO_ROOT.
check_directory_chain() {
    local path="$1" current="$REPO_ROOT" rest component
    case "$path" in
        "$REPO_ROOT") return 0 ;;
        "$REPO_ROOT"/*) rest="${path#"$REPO_ROOT"/}" ;;
        *) echo "Error: output directory is outside the repository: $path" >&2; return 1 ;;
    esac
    while [ -n "$rest" ]; do
        component="${rest%%/*}"
        if [ "$component" = . ] || [ "$component" = .. ]; then
            echo "Error: noncanonical output directory: $path" >&2
            return 1
        fi
        current="$current/$component"
        if [ -L "$current" ]; then
            echo "Error: output directory is a symbolic link: $current" >&2
            return 1
        fi
        if [ -e "$current" ] && [ ! -d "$current" ]; then
            echo "Error: output parent is not a directory: $current" >&2
            return 1
        fi
        case "$rest" in */*) rest="${rest#*/}" ;; *) rest="" ;; esac
    done
}

validate_output() {
    local source="$1" expected="$2" description="$3" actual
    check_directory_chain "${source%/*}" || return 1
    if [ -L "$source" ] || [ ! -f "$source" ]; then
        echo "Error: $description is not a regular file: $source" >&2
        return 1
    fi
    actual="$(sha256_of "$source")" || return 1
    if [ "$actual" != "$expected" ]; then
        echo "Error: $description does not match the identity this build recorded" >&2
        return 1
    fi
}

validate_destination() {
    local destination="$1"
    if [ -L "$destination" ]; then
        echo "Error: sidecar destination is a symbolic link: $destination" >&2
        return 1
    fi
    if [ -e "$destination" ] && [ ! -f "$destination" ]; then
        echo "Error: sidecar destination is not a regular file: $destination" >&2
        return 1
    fi
}

validate_size() {
    local file="$1" expected="$2" actual
    actual="$(wc -c < "$file" | tr -d ' ')" || return 1
    if [ "$actual" != "$expected" ]; then
        echo "Error: collected output size differs from the recorded size: $file" >&2
        return 1
    fi
}

TRANSACTION=""
publication_started=0
prior_binary=0
prior_provenance=0

restore_member() {
    local previous="$1" destination="$2" existed="$3"
    validate_destination "$destination" || return 1
    if [ "$existed" = 1 ]; then
        # A failed first promotion may have left this exact old inode in place.
        [ "$previous" -ef "$destination" ] || mv -f -- "$previous" "$destination"
    else
        rm -f -- "$destination"
    fi
}

cleanup() {
    local status=$? rollback_failed=0 keep_transaction=0
    trap - EXIT
    # Do not interrupt rollback/cleanup itself; SIGKILL/power loss is outside
    # this guarantee. No background jobs or concurrent packagers are supported.
    trap '' HUP INT TERM
    set +e
    if ! check_directory_chain "$STAGE_ROOT" || ! rm -rf -- "$STAGE"; then
        echo "Error: could not remove owned build stage: $STAGE" >&2
        status=1
    fi
    if [ "$status" -ne 0 ] && [ "$publication_started" = 1 ]; then
        if check_directory_chain "$BINARIES_DIR"; then
            restore_member "$TRANSACTION/previous-binary" "$DESTINATION" "$prior_binary" || rollback_failed=1
            restore_member "$TRANSACTION/previous-provenance" "$PROVENANCE_DESTINATION" "$prior_provenance" || rollback_failed=1
        else
            rollback_failed=1
        fi
        if [ "$rollback_failed" = 1 ]; then
            keep_transaction=1
            status=1
            echo "Error: publication rollback incomplete; DO NOT package these outputs." >&2
            printf 'Recovery directory: %q\nRecovery instructions: %q\n' \
                "$TRANSACTION" "$TRANSACTION/recovery.txt" >&2
            printf 'Binary destination: %q\nProvenance destination: %q\n' \
                "$DESTINATION" "$PROVENANCE_DESTINATION" >&2
        fi
    fi
    if [ -n "$TRANSACTION" ] && [ "$keep_transaction" = 0 ]; then
        if ! check_directory_chain "$BINARIES_DIR" || ! rm -rf -- "$TRANSACTION"; then
            echo "Error: could not remove owned publication directory: $TRANSACTION" >&2
            status=1
        fi
    fi
    if [ "$status" = 0 ]; then
        echo "Built voicebox-server-${PLATFORM}${SUFFIX} ($handoff_binary_bytes bytes)" || status=1
        [ "$status" != 0 ] || echo "Build complete!" || status=1
    fi
    exit "$status"
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
BINARIES_DIR="$REPO_ROOT/tauri/src-tauri/binaries"
check_directory_chain "$STAGE_ROOT"
check_directory_chain "$BINARIES_DIR"
mkdir -p "$STAGE_ROOT"
STAGE="$STAGE_ROOT/stage-$BUILD_ID"
if [ -e "$STAGE" ] || [ -L "$STAGE" ]; then
    echo "Error: staging directory already exists: $STAGE" >&2
    exit 1
fi
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

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
check_directory_chain "$STAGE"
if [ -L "$HANDOFF" ] || [ ! -f "$HANDOFF" ]; then
    echo "Error: the build produced no sidecar handoff at $HANDOFF" >&2
    exit 1
fi

handoff_schema='' handoff_build_id='' handoff_binary='' handoff_binary_sha256=''
handoff_binary_bytes='' handoff_provenance='' handoff_provenance_sha256=''
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

check_directory_chain "$BINARIES_DIR"
mkdir -p "$BINARIES_DIR"

DESTINATION="$BINARIES_DIR/voicebox-server-${PLATFORM}${SUFFIX}"
PROVENANCE_DESTINATION="$BINARIES_DIR/voicebox-server-${PLATFORM}.provenance.json"
validate_destination "$DESTINATION"
validate_destination "$PROVENANCE_DESTINATION"
validate_output "$STAGE/$handoff_binary" "$handoff_binary_sha256" "produced sidecar"
validate_size "$STAGE/$handoff_binary" "$handoff_binary_bytes"
validate_output "$STAGE/$handoff_provenance" "$handoff_provenance_sha256" "produced provenance"
provenance_bytes="$(wc -c < "$STAGE/$handoff_provenance" | tr -d ' ')"

# Sibling staging guarantees same-filesystem renames. No destination is removed
# at startup or touched until BOTH copies and their executable mode validate.
TRANSACTION="$(mktemp -d "$BINARIES_DIR/.voicebox-publish-$BUILD_ID.XXXXXXXX")"
cp -- "$STAGE/$handoff_binary" "$TRANSACTION/new-binary"
cp -- "$STAGE/$handoff_provenance" "$TRANSACTION/new-provenance"
chmod 755 "$TRANSACTION/new-binary"
validate_output "$TRANSACTION/new-binary" "$handoff_binary_sha256" "copied sidecar"
validate_size "$TRANSACTION/new-binary" "$handoff_binary_bytes"
validate_output "$TRANSACTION/new-provenance" "$handoff_provenance_sha256" "copied provenance"
validate_size "$TRANSACTION/new-provenance" "$provenance_bytes"
[ -x "$TRANSACTION/new-binary" ] || { echo "Error: copied sidecar is not executable" >&2; exit 1; }

# Keep exact old inodes, including modes, until completion. A partial prior
# destination is also restored to its original presence/absence on failure.
if [ -f "$DESTINATION" ]; then
    ln -- "$DESTINATION" "$TRANSACTION/previous-binary"
    prior_binary=1
fi
if [ -f "$PROVENANCE_DESTINATION" ]; then
    ln -- "$PROVENANCE_DESTINATION" "$TRANSACTION/previous-provenance"
    prior_provenance=1
fi
{
    echo 'Publication recovery: stop other builders/packagers and inspect both members.'
    echo 'An existing previous-* file holds the old inode; an already restored member is at its destination.'
    printf 'binary_destination=%q\nprovenance_destination=%q\n' "$DESTINATION" "$PROVENANCE_DESTINATION"
    printf 'previous_binary_existed=%s\nprevious_provenance_existed=%s\n' "$prior_binary" "$prior_provenance"
    for member in binary provenance; do
        if [ -f "$TRANSACTION/previous-$member" ]; then
            printf 'previous_%s_sha256=%s\n' "$member" "$(sha256_of "$TRANSACTION/previous-$member")"
            printf 'Restore this file to its destination if needed: %q\n' "$TRANSACTION/previous-$member"
        else
            printf 'The previous %s was absent; remove only its new destination to restore that state.\n' "$member"
        fi
    done
} > "$TRANSACTION/recovery.txt"

# Set intent before the first rename: a handled interrupt after a rename but
# before its shell status is observed still rolls back both members. These two
# renames are NOT atomic to concurrent readers or a crash; G3 must reject a
# mismatched pair before packaging, and builders/packagers must be serialized.
publication_started=1
mv -f -- "$TRANSACTION/new-binary" "$DESTINATION"
mv -f -- "$TRANSACTION/new-provenance" "$PROVENANCE_DESTINATION"
validate_output "$DESTINATION" "$handoff_binary_sha256" "published sidecar"
validate_size "$DESTINATION" "$handoff_binary_bytes"
validate_output "$PROVENANCE_DESTINATION" "$handoff_provenance_sha256" "published provenance"
validate_size "$PROVENANCE_DESTINATION" "$provenance_bytes"
[ -x "$DESTINATION" ] || { echo "Error: published sidecar is not executable" >&2; exit 1; }
# EXIT performs owned cleanup and only then prints success for the complete pair.

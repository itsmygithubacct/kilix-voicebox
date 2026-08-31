#!/bin/bash
# Generate the TypeScript client from an isolated, locked FastAPI process.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
RUNTIME_BASE="${XDG_RUNTIME_DIR:-/tmp}"
RUNTIME_DIR="$(mktemp -d --tmpdir="$RUNTIME_BASE" kilix-voicebox-openapi.XXXXXXXX)"
SOCKET_PATH="$RUNTIME_DIR/backend.sock"
DATA_HOME="$RUNTIME_DIR/data"
BACKEND_PID=""
SESSION_TOKEN="$(uv run --frozen python -c 'import secrets; print(secrets.token_hex(32))')"

cleanup() {
    if [ -n "$BACKEND_PID" ]; then
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    fi
    rm -rf -- "$RUNTIME_DIR"
}
trap cleanup EXIT INT TERM

chmod 700 "$RUNTIME_DIR"
install -d -m 700 "$DATA_HOME"
cd "$REPO_ROOT"

KILIX_VOICEBOX_SESSION_TOKEN="$SESSION_TOKEN" XDG_DATA_HOME="$DATA_HOME" \
    uv run --frozen uvicorn backend.main:app --uds "$SOCKET_PATH" --no-access-log &
BACKEND_PID=$!

for _attempt in {1..30}; do
    if curl --fail --silent --unix-socket "$SOCKET_PATH" \
        -H "X-Kilix-Voicebox-Session: $SESSION_TOKEN" http://localhost/openapi.json \
        > app/openapi.json; then
        break
    fi
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "OpenAPI backend exited before becoming ready" >&2
        exit 1
    fi
    sleep 1
done

if [ ! -s app/openapi.json ]; then
    echo "OpenAPI schema was not produced" >&2
    exit 1
fi

node -e '
const schema = JSON.parse(require("fs").readFileSync("app/openapi.json", "utf8"));
if (schema?.info?.title !== "Kilix Voicebox API") process.exit(1);
'

cd app
npx --no-install openapi-typescript-codegen \
    --input openapi.json \
    --output src/lib/api \
    --client fetch \
    --useOptions \
    --exportSchemas true

echo "Generated the Voicebox API client from 1/1 isolated schema source."

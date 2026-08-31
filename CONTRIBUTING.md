# Contributing to the F104 Voicebox candidate

This repository publishes an F104 candidate work ref for reproducibility. It
does not currently claim a public contribution intake or supported release.
Changes are accepted through the release owner's approved workflow.

## Development setup

The pinned toolchain is Bun 1.3.8, Python 3.12 with `uv`, and Rust 1.85.

```sh
bun install --frozen-lockfile
uv sync --frozen --group dev
```

The app delegates synthesis and transcription to separately installed Kilix
providers. Do not add model downloads, embedded inference engines, provider
installation, or provider lifecycle management to this repository.

For browser development, set the same freshly generated lowercase-hex session
credential in `KILIX_VOICEBOX_SESSION_TOKEN` and
`VITE_VOICEBOX_SESSION_TOKEN`. The bundled app generates and transfers its own
credential without placing it in a command line or log.

## Required checks

Run the controls that apply to your change before submitting it:

```sh
set -euo pipefail
bootstrap_output="$(env \
  -u GIT_ALTERNATE_OBJECT_DIRECTORIES -u GIT_COMMON_DIR \
  -u GIT_CONFIG_COUNT -u GIT_CONFIG_PARAMETERS -u GIT_DIR \
  -u GIT_INDEX_FILE -u GIT_NAMESPACE -u GIT_OBJECT_DIRECTORY \
  -u GIT_QUARANTINE_PATH -u GIT_REPLACE_REF_BASE -u GIT_SHALLOW_FILE \
  -u GIT_WORK_TREE GIT_NO_REPLACE_OBJECTS=1 \
  git -C "$PWD" show HEAD:.kilix-import/verify_import.py | python3 -I -S -)"
[[ "$bootstrap_output" == "VOICEBOX_CURATED_IMPORT: PASS ("* ]]
[[ "$bootstrap_output" != *$'\n'* ]]
printf '%s\n' "$bootstrap_output"
make check-backend
bun run --cwd app typecheck
bun run --cwd app build
bun run --cwd web typecheck
bun run --cwd web build
bun run --cwd tauri typecheck
bun run --cwd tauri build
```

For native changes, also run `cargo check --locked` in `tauri/src-tauri` on a
host with the Tauri system development packages installed. Build the backend
sidecar only from the committed source and lockfile using
`scripts/build-server.sh`.

Keep generated media, imported samples, consent records, provenance records,
databases, caches, credentials, and built sidecars out of Git.

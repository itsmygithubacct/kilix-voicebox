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

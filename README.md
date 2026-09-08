# Kilix Voicebox

Kilix Voicebox is a local-only desktop studio and product-data orchestrator for
independent Kilix speech providers. This candidate deliberately contains no
Qwen, Whisper, MLX, PyTorch, model downloader, or model runtime.

The repository is an F104 candidate. It does not select a Qwen runtime, model
revision, install source, or license posture. Those decisions remain outside
this repository; until a provider is installed and selected, the UI reports the
provider's stable unavailable state.

## Trust boundary

```text
Tauri/React UI
      |
      | loopback HTTP only
      v
Voicebox product backend
      |
      | private same-UID Unix SOCK_SEQPACKET + one bounded FD
      +--------------------+----------------------+
      v                    v
kilix-qwen-tts       kilix-transcribe
```

- Voicebox binds only to `127.0.0.1`, accepts no user-supplied server URL, and
  requires an unguessable per-process credential on every application request.
- Provider endpoints are fixed names below a private `0700` runtime directory.
- Linux peer credentials must match the current UID; sockets must be `0600`.
- Provider messages are length-, depth-, key-, string-, number-, and FD-bounded.
- Transcripts may use a read-only result descriptor, limited to 16 MiB and
  verified against its size, SHA-256, and model identity. They are returned
  directly without adding them to the generation store.
- Voicebox never imports provider runtimes or performs a second inference path.
- Voice cloning requires an explicit consent assertion bound to the canonical PCM
  digest and original sample-container digest.
- Built-in voices and voice design use the installed model's supported mode.
  They require no sample upload and create generation provenance without a
  sample-consent record. The selected profile groups their history.
- Consent and generation provenance are written atomically as private `0600`
  records below the Voicebox XDG data directory.
- API responses expose loopback resource URLs, not private filesystem paths.
- Profile, sample, avatar, generation, and story deletion show an exact preview
  and archive bytes plus catalog recovery records in private recoverable trash.
- Profile exports omit source samples unless `include_samples=true` is explicit.

## Development controls

Python dependencies and build tools are pinned by `uv.lock`:

```bash
make sync-python
make check-backend
```

Frontend dependencies are pinned by `bun.lock`:

```bash
bun install --frozen-lockfile
bun run --cwd app typecheck
bun run --cwd app build
bun run --cwd tauri typecheck
bun run --cwd tauri build
```

Build the provider-free Python sidecar and its provenance manifest with:

```bash
./scripts/build-server.sh
```

The manifest records the source commit and tree, lock digest, binary digest,
binary length, provider endpoints, and the absence of an embedded model runtime.
The build runs the locked PyInstaller environment without inheriting Python
startup variables or user uv configuration.

Each run owns one staging directory, `kilix_voicebox_backend/build/stage-<id>`,
which the producer creates and the script removes when it exits. The producer
finishes by writing a handoff naming and hashing exactly the outputs of that
run, and the script installs into `tauri/src-tauri/binaries/` only what the
handoff names. It validates both sources, stages both copies in a fresh sibling
directory on the destination filesystem, and checks their digests, sizes and
the binary's executable mode before replacing either destination. Symbolic
links in source/destination entries or their parents within the repository are
refused before reading or writing through them. Earlier output is never used
as a substitute for this run's producer output or removed at startup.

The previous pair's inodes are retained until both promotions and final checks
succeed. Ordinary copy, chmod, validation or promotion failures and handled
HUP/INT/TERM restore the previous files (or their previous absence). Success is
printed only after both members succeed and owned cleanup finishes. If rollback
fails, the script exits unsuccessfully, prints the exact recovery directory and
destinations, and retains `recovery.txt` and available previous files there;
do not package those outputs until they have been recovered and verified.

This is failure-consistent publication in a trusted private build tree, not an
atomic or durable two-file commit. Builders and packagers must be serialized;
there is no concurrent-writer protection or concurrent-reader snapshot. A
SIGKILL, power loss or filesystem crash between the two renames can leave a
mixed pair. The later artifact-identity gate (G3, still required) must reject
an inconsistent pair before packaging; this collector does not provide that
gate or crash durability. HUP/INT/TERM are ignored during the final
rollback/cleanup itself. `kilix_voicebox_backend/build_binary.py` can also be
run directly, but requires `--stage-dir` naming a directory that does not yet
exist.

## Local API

Voicebox serves the product API on `http://127.0.0.1:17493`. A voice-cloning
request must include explicit consent:

```bash
curl -X POST http://127.0.0.1:17493/generate \
  -H 'Content-Type: application/json' \
  -H "X-Kilix-Voicebox-Session: $KILIX_VOICEBOX_SESSION_TOKEN" \
  -d '{"profile_id":"PROFILE_ID","text":"Hello","language":"en","consent_asserted":true}'
```

The same endpoint accepts `mode: "named_voice"` with `voice_id: "Vivian"`, or
`mode: "voice_design"` with a `description`. These modes require an installed
CustomVoice or VoiceDesign model respectively, and omit `consent_asserted`.
Conflicting mode fields are refused before contacting a provider. Named/design
outputs carry their mode, request digest, engine/model revision, seed and audio
digest in versioned private provenance; cloned outputs retain their existing
sample-consent binding.

Voicebox accepts and generates seeds from 0 through 9007199254740991 so the
browser, provider request and stored provenance keep the same exact integer.
Canonical WAV headers are checked against the bytes copied into private storage,
alongside their exact size and digest, before audio or provenance is published.

Generated audio is retrieved by opaque identifier at `/audio/{generation_id}`.
Provider installation and model selection are content-owned operations and are
not implemented by Voicebox.

For browser development, generate one session value and export it to both the
backend and Vite before starting either process:

```bash
export KILIX_VOICEBOX_SESSION_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export VITE_VOICEBOX_SESSION_TOKEN="$KILIX_VOICEBOX_SESSION_TOKEN"
```

## Data locations

Product data follows XDG conventions. Override the base only for isolated tests
with `--data-dir`; startup rejects non-private directories.
Provider sockets use `XDG_RUNTIME_DIR` and fixed endpoint names.

The candidate work ref is public at
`https://github.com/itsmygithubacct/kilix-voicebox.git`. That work ref is not a
release: pushes to `main`, tags, repository settings, and release admission
remain owner-reserved.

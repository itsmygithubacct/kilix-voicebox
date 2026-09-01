# Voicebox product backend

This FastAPI process owns product data and coordinates independent Kilix speech
providers. It does not own, import, install, download, or execute model runtimes.

## Components

- `kilix_provider.py` implements the bounded private Unix-socket transport.
- `orchestrator.py` canonicalizes a single voice sample, obtains consent, passes
  anonymous file descriptors to providers, and verifies returned output.
- `private_store.py` owns private consent, provenance, generation, and trash
  records.
- `tts.py` and `transcribe.py` are provider clients only.
- `main.py` exposes the loopback product API and maps provider failures to stable
  public error codes.

The only provider endpoints are `kilix-qwen-tts.sock` and
`kilix-transcribe.sock`. The transport uses `SOCK_SEQPACKET`, accepts at most one
SCM_RIGHTS descriptor, checks private runtime and socket permissions, and on
Linux requires a same-UID peer through `SO_PEERCRED`.

## Privacy behavior

- Source audio remains private product data.
- Synthesis is refused without an explicit user consent assertion.
- Consent binds the canonical PCM digest, original container digest, capture
  time, declared use, model revision request, and later output provenance.
- Transcription passes one bounded descriptor and retains no extra product copy.
- Profile exports exclude source samples by default.
- JSON responses contain stable resource URLs and error codes, never local paths
  or raw exception messages.
- Generation deletion previews both the audio and provenance records, then moves
  them to recoverable private trash.

## Run and test

From the repository root:

```bash
make sync-python
make check-backend
make dev-backend
```

`make dev-backend` binds to `127.0.0.1:17493`. Remote and LAN binding are not
supported. Model availability is reported by providers; no runtime or profile
is selected by this candidate.

The frozen sidecar build is:

```bash
./scripts/build-server.sh
```

It emits a binary and a sidecar provenance manifest without bundling any speech
runtime.

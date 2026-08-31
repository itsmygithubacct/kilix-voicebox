# Kilix Voicebox troubleshooting

This candidate is local-only and delegates speech work to independent providers.

## Provider unavailable

Confirm XDG_RUNTIME_DIR is set, the runtime directory is owned by the current
user with mode 0700, and the fixed provider sockets are present with mode 0600:

- kilix-qwen-tts.sock
- kilix-transcribe.sock

A provider that has no release-selected runtime returns RUNTIME_UNSELECTED.
Voicebox does not download, install, select, or repair model payloads.

## Generation refused

Voice cloning requires an explicit consent assertion and exactly one bounded
source sample. The source must be mono, signed 16-bit, 24 kHz PCM WAV and no
longer than 30 seconds in this candidate.

Public API errors contain stable codes. Inspect local service logs for operator
diagnostics; the frontend intentionally receives no exception text or local
filesystem paths.

## Local connection

Voicebox always uses http://127.0.0.1:17493 for its product API. Remote and LAN
server modes are unsupported. A persisted URL is ignored.

## Data permissions

The product XDG data root and every managed directory must be owned by the
current user and inaccessible to group or other users. Correct the ownership or
permissions out of band; Voicebox fails closed instead of weakening them.

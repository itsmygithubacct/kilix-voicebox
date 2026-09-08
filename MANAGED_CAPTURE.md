# Managed microphone capture

This candidate adds Voicebox adoption of the installed Kilix Voice microphone
owner. It is a bounded P6/P8 implementation, not physical-microphone, speech
quality, GPU, fit, soak or complete Voicebox qualification.

Click **Start Recording** to use the shared system microphone. Opening a dialog
does not request browser microphone access or open an audio device. The former
independent waveform microphone stream is removed. The existing authenticated
loopback bridge and allowed-origin list remain the product authority; there is
no new listener, permission, provider service launcher or model acquisition.

The bridge delegates exclusively to `voicelib.audio.MicCapture` and
`voicelib.microphone.CaptureControl`. Voice owns the existing per-user microphone
lease, subprocess tree, inherited guard and authenticated cleanup. Production
uses the existing Voice input-device setting and actual bounded logind lock
adapter. Locked, inactive and unknown lock states refuse recording. Python test
constructors can select synthetic recorders and private lease directories;
HTTP callers cannot select a recorder command, device namespace or lock callback.

One authenticated POST `/recording` carries a fresh 32-character lowercase hex
ID and integer `seconds` in 1..30. Its original HTTP connection owns the request.
No capture begins before this request. PCM is mono, 16 kHz, signed 16-bit and
bounded by the requested duration (at most 960000 bytes). Acquisition and capture
share one absolute limit of the requested seconds plus a fixed two-second
cleanup margin, within Voice's 120-second ceiling. That limit is not renewed.
The browser also bounds the complete recording request by seconds plus six.

`/recording/status` is passive: starting, recording, stopping, inactive or
unavailable, opaque active/last-completed IDs and PCM duration only. Inactive
means this bridge has no recording, not that a device used by another program
is free. The indication lives outside sample dialogs. A disconnected or failed
status channel displays uncertainty. A local aborted request is not an off
indication: the pending ID remains until a matching completion is observed.
An ID whose request never reached the backend may therefore retain uncertainty
for that page session. Unknown status never authorizes an acquisition.

POST `/recording/{id}/stop` asks for a completed clip. POST
`/recording/{id}/cancel` discards it. Both affect only the matching current ID;
their replies are requests acknowledged, not cleanup proof. Original-connection
disconnect, dialog/tab close, hiding the page, cancellation, deadline or lock
loss stop capture. Provider shutdown also requests owned cancellation. Voice's
parent-death/heartbeat boundary remains responsible for abnormal backend death.
Every frame checks ownership failure, even if a surviving recorder keeps writing.

Audio is returned as a bounded WAV only after authenticated owned cleanup and a
final control check. Cancellation or uncertainty discards PCM. An unproved stop
leaves this bridge unavailable and retains Voice's persistent quarantine; no
context exit, missing process or cancel acknowledgement clears that grant. A
fresh bridge still encounters that grant through Voice's unchanged acquire API.

The same recording control survives WAV serialization and the final capture
cleanup. HTTP keeps the active ID until the handler accepts or refuses delivery,
after waiting for the operation and constructing its response. Accepted Cancel
requests remain ordered with that final decision; no successor can overlap this
pending claim. Successful operation completion does not mark its HTTP receiver
disconnected. Task cancellation or actual disconnection still signals loss and
awaits the owned operation before releasing its claim.

Voice's actual logind adapter caches answers for up to 100 ms and bounds each
query to 150 ms. Rechecking the same adapter retains that declared behavior;
synthetic boundary tests do not establish instantaneous physical-lock detection.

The existing Record → preview → **Transcribe** flow remains explicit. Managed
recorded audio cannot be submitted before cleanup because it is not delivered
before that proof. Transcribe also continues to accept existing file uploads.
It stages the bounded upload privately, borrows one no-follow read-only/CLOEXEC
descriptor, hashes a bounded byte population, and calls the installed controlled
transcribe client. That client owns all provider protocol/result-FD parsing. A
single 300-second operation budget covers staging, hashing, request and final
delivery, with an independent browser limit covering the HTTP upload as well.
Actual HTTP disconnect, page/dialog close and lock loss cancel only this job.
Local cancellation closes owned request channels and input FDs; it does not mean
the independently supervised provider is ready. There is no background provider
request thread left after the HTTP handler returns. The handler awaits its
controlled operation, including on asyncio task cancellation.

Recording and transcription do not create or modify clone-consent records.
The user still explicitly supplies reference text and consent through existing
profile/sample/generation controls. Digest-bound consent, copied-audio validation,
provenance, export and trash implementations are unchanged. PCM exists in memory
and then in the explicit browser preview; transcription's private staged upload
is removed on all exits. Errors sent to the browser contain stable codes only.

## Provisioned dependencies and validation

The implementation uses Voice commit
`869366f22bb930230778b7cec5229f42b4211131` and controlled transcribe client
`d5e7355c4cae196f8f341a070693c16d65ffa049`, both accepted for their stated scopes.
They must be provisioned as importable installed packages alongside the backend.
Missing modules refuse the affected operation; ordinary backend import remains
available. This source candidate does not select final package/source pins or
claim a frozen Tauri sidecar build containing those external modules. Final
sidecar/OS dependency staging remains a release-integration input.

Tests require those exact installed inputs and the existing locked Python
dependencies. Run the complete backend discovery population and existing import,
boundary, lint and frontend type controls. New controls use actual separate
synthetic recorder, escaped descendant, HTTP backend and private transcribe peer
processes. Fixture-only lock changes are distinct from physical session evidence.
No test opens a physical microphone, performs neural inference, starts a shared
provider or creates cloning permission.

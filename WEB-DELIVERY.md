# Selected offline web delivery

`tools/build_web_delivery.py` builds the retained React frontend and locked
Python backend into `kilix.voicebox.web-delivery/v1`. Supply clean committed
Voicebox/host/managed Voice/canonical lease/Content/Transcribe-client sources,
the explicit copied CPython 3.12.8 home, complete uv.lock software wheelhouse,
and Bun 1.3.8 with frozen bun.lock dependencies. Its manifest binds every
payload hash/mode, source commit/tree and dependency. No model or native
inference engine is included. The separately gated Tauri build is unchanged.

Install/run/update/rollback/uninstall through `kilix voicebox`; see the host's
VOICEBOX-DELIVERY.md. An explicit offline package path and SHA256 is required.
The backend serves an immutable public shell, and requires a fresh per-launch
credential on all API/media resources. The installed browser uses its own
ephemeral loopback endpoint, private X display/bus and disposable profile.

Named voice, consented cloning, voice design, transcription, profiles, history,
audio export/recoverable deletion, channels and story timelines use the retained
product. Synthesis mode maps to provider model selection: CustomVoice for built-in
voices, Base for cloning, VoiceDesign for descriptions. Content agreement/asset
installation remains separate from sample consent. Model status shows exact
revision, terms, download/temporary size and pending qualification; provider
unavailability/refusal remains explicit. Cancellation closes the original owned
job channel and does not stop shared services.

The final web closure explicitly selects a separately reviewed managed Voice
successor on the current published Whistle history and the canonical lease
component. The historical Transcribe final-only client projection remains
declared. Old/new streaming providers retain minor-0 compatibility; incremental
transcript presentation and diarization are not implemented here. Unsupported
Base/0.6B CustomVoice instruction combinations, other platforms, embedded
upstream model downloads and remote-server/update flows remain unavailable.

Synthetic provider/capture tests prove product and installed source/population
behavior. They do not qualify actual model quality, memory/resource fit,
physical audio devices, guest upgrades or terminal pane acceptance. No real
license receipt or model download is produced by this delivery.

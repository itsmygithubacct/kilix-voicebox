"""Optional Kilix Voice microphone API for capture tests.

Production managed_capture lazy-imports voicelib and raises
MICROPHONE_UNAVAILABLE when Voice is absent. Tests must intercept the
managed capture implementation selected by CaptureControl, including Voice
versions that preserve a separate legacy MicCapture implementation.
"""
try:
    from voicelib import audio, microphone, util
except ImportError:
    audio = microphone = util = None

capture_class = None
if audio is not None:
    try:
        from voicelib.managed_audio import ManagedMicCapture as capture_class
    except ModuleNotFoundError as error:
        if error.name != 'voicelib.managed_audio':
            raise
        capture_class = audio.MicCapture

VOICE_MICROPHONE_AVAILABLE = audio is not None and microphone is not None and util is not None

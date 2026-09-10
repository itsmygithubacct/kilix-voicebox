"""Optional Kilix Voice microphone API for capture tests.

Production managed_capture lazy-imports voicelib and raises
MICROPHONE_UNAVAILABLE when Voice is absent. Capture tests patch
voicelib.audio at runtime, so they cannot load without it. Skip the
class instead of failing unittest discover.
"""
try:
    from voicelib import audio, microphone, util
except ImportError:
    audio = microphone = util = None

VOICE_MICROPHONE_AVAILABLE = audio is not None and microphone is not None and util is not None

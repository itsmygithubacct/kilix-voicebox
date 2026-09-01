"""Compatibility marker for the removed embedded inference backends.

Kilix Voicebox delegates synthesis and transcription to the independent
``kilix-qwen-tts`` and ``kilix-transcribe`` providers. No model backend is
constructed in this application repository.
"""

from ..kilix_provider import ProviderClient, ProviderEndpoint, ProviderError

__all__ = ["ProviderClient", "ProviderEndpoint", "ProviderError"]

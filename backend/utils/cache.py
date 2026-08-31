"""Product-owned derived-audio cache helpers with no model serialization."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .. import config


def _get_cache_dir() -> Path:
    return config.get_cache_dir()


def get_cache_key(audio_path: str, reference_text: str) -> str:
    digest = hashlib.sha256()
    with open(audio_path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    digest.update(b"\x00")
    digest.update(reference_text.encode("utf-8"))
    return digest.hexdigest()


def clear_voice_prompt_cache() -> int:
    """Remove product-owned combined WAVs; provider prompt caches stay provider-owned."""

    return _clear_matching("combined_*.wav")


def clear_profile_cache(profile_id: str) -> int:
    safe = "".join(character for character in profile_id if character.isalnum() or character in "-_")
    if safe != profile_id or not safe:
        return 0
    return _clear_matching(f"combined_{safe}_*.wav")


def _clear_matching(pattern: str) -> int:
    deleted = 0
    cache_dir = _get_cache_dir()
    if not cache_dir.exists():
        return deleted
    for candidate in cache_dir.glob(pattern):
        if candidate.is_file() and not candidate.is_symlink():
            candidate.unlink()
            deleted += 1
    return deleted

"""Bounded audio processing utilities for product-owned files."""

from pathlib import Path

import numpy as np
import soundfile as sf


MAX_AUDIO_CHANNELS = 8
MAX_SOURCE_SAMPLES = 48_000_000
MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 192_000


def normalize_audio(
    audio: np.ndarray,
    target_db: float = -20.0,
    peak_limit: float = 0.85,
) -> np.ndarray:
    """Normalize an audio array to a target RMS with peak limiting."""
    audio = audio.astype(np.float32, copy=False)
    rms = np.sqrt(np.mean(audio**2))
    target_rms = 10**(target_db / 20)
    if rms > 0:
        gain = target_rms / rms
        audio = audio * gain
    return np.clip(audio, -peak_limit, peak_limit)


def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or len(audio) == 0:
        return audio.astype(np.float32, copy=False)
    target_frames = round(len(audio) * target_rate / source_rate)
    if not 0 < target_frames <= MAX_SOURCE_SAMPLES:
        raise ValueError("AUDIO_RESAMPLE_SIZE")
    source_positions = np.arange(len(audio), dtype=np.float64)
    target_positions = np.arange(target_frames, dtype=np.float64) * source_rate / target_rate
    if audio.ndim == 1:
        return np.interp(target_positions, source_positions, audio).astype(np.float32)
    channels = [
        np.interp(target_positions, source_positions, audio[:, index])
        for index in range(audio.shape[1])
    ]
    return np.column_stack(channels).astype(np.float32)


def load_audio(
    path: str | Path,
    sample_rate: int = 24000,
    mono: bool = True,
    max_duration_seconds: float = 900.0,
) -> tuple[np.ndarray, int]:
    """Decode, bound, optionally mix down, and deterministically resample audio."""
    if type(sample_rate) is not int or not MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE:
        raise ValueError("AUDIO_SAMPLE_RATE")
    if (
        type(max_duration_seconds) not in {int, float}
        or not 0 < max_duration_seconds <= 900
    ):
        raise ValueError("AUDIO_DURATION_BOUND")
    info = sf.info(path)
    if (
        type(info.samplerate) is not int
        or not MIN_SAMPLE_RATE <= info.samplerate <= MAX_SAMPLE_RATE
        or type(info.channels) is not int
        or not 0 < info.channels <= MAX_AUDIO_CHANNELS
        or type(info.frames) is not int
        or info.frames <= 0
        or info.frames * info.channels > MAX_SOURCE_SAMPLES
        or info.frames > info.samplerate * max_duration_seconds
    ):
        raise ValueError("AUDIO_DECODE_BOUND")
    audio, source_rate = sf.read(path, dtype="float32", always_2d=True)
    if source_rate != info.samplerate or audio.shape != (info.frames, info.channels):
        raise ValueError("AUDIO_CHANGED")
    if not np.isfinite(audio).all():
        raise ValueError("AUDIO_NONFINITE")
    if mono:
        audio = audio.mean(axis=1, dtype=np.float64).astype(np.float32)
    audio = _resample(audio, source_rate, sample_rate)
    return audio, sample_rate


def save_audio(
    audio: np.ndarray,
    path: str | Path,
    sample_rate: int = 24000,
) -> None:
    """Write a finite bounded audio array as PCM WAV."""
    if type(sample_rate) is not int or not MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE:
        raise ValueError("AUDIO_SAMPLE_RATE")
    if audio.ndim not in {1, 2} or not 0 < audio.size <= MAX_SOURCE_SAMPLES:
        raise ValueError("AUDIO_OUTPUT_SIZE")
    if not np.isfinite(audio).all():
        raise ValueError("AUDIO_NONFINITE")
    sf.write(path, audio, sample_rate, format="WAV", subtype="PCM_16")


def validate_reference_audio(
    audio_path: str,
    min_duration: float = 2.0,
    max_duration: float = 30.0,
    min_rms: float = 0.01,
) -> tuple[bool, str | None]:
    """Validate a bounded reference sample without exposing decoder errors."""
    try:
        audio, sr = load_audio(audio_path, max_duration_seconds=max_duration)
        duration = len(audio) / sr
        if duration < min_duration:
            return False, f"Audio too short (minimum {min_duration} seconds)"
        if duration > max_duration:
            return False, f"Audio too long (maximum {max_duration} seconds)"
        rms = np.sqrt(np.mean(audio**2))
        if rms < min_rms:
            return False, "Audio is too quiet or silent"
        if np.abs(audio).max() > 0.99:
            return False, "Audio is clipping (reduce input gain)"
        return True, None
    except (OSError, RuntimeError, TypeError, ValueError, sf.LibsndfileError):
        return False, "AUDIO_DECODE_FAILED"

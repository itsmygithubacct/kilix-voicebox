"""Thin Voicebox orchestration over the two reusable speech providers."""

from __future__ import annotations

import hashlib
import fcntl
import io
import json
import os
import secrets
import stat
import struct
import tempfile
import uuid
import wave
from pathlib import Path
from typing import Any

from .kilix_provider import ProviderClient, ProviderError, ProviderReply, decode_document
from .private_store import (
    MAX_SAMPLE_BYTES,
    GenerationProvenance,
    GeneratedVoiceProvenance,
    PrivateVoiceStore,
    StoreError,
    read_owned_sample,
)


MAX_TEXT_BYTES = 16_384
MAX_INSTRUCTION_BYTES = 4_096
MAX_TRANSCRIPT_BYTES = 16 * 1024 * 1024
_GENERATED_MODELS = {
    "qwen3-tts-0.6b-customvoice": ("85e237c12c027371202489a0ec509ded67b5e4b5", "named_voice"),
    "qwen3-tts-1.7b-customvoice": ("0c0e3051f131929182e2c023b9537f8b1c68adfe", "named_voice"),
    "qwen3-tts-1.7b-voicedesign": ("5ecdb67327fd37bb2e042aab12ff7391903235d3", "voice_design"),
}


def _transcript_result(reply: ProviderReply) -> dict[str, Any]:
    _require(reply.payload.get("type") == "result", "INVALID_RESPONSE")
    result = reply.payload.get("result")
    _require(type(result) is dict, "INVALID_RESPONSE")
    if "transcript" not in result:
        # The earlier candidate returned short inline results. Its control
        # decoder has already enforced the smaller packet/structure bounds.
        _require(not reply.descriptors, "DESCRIPTOR_MISMATCH")
        return result
    metadata = result["transcript"]
    _require(type(metadata) is dict and set(metadata) == {"fd", "byte_length", "sha256"},
             "INVALID_RESPONSE")
    _require(len(reply.descriptors) == 1 and type(metadata["fd"]) is int
             and metadata["fd"] == 0, "DESCRIPTOR_MISMATCH")
    size = metadata["byte_length"]
    digest = metadata["sha256"]
    _require(type(size) is int and 0 < size <= MAX_TRANSCRIPT_BYTES, "LIMIT_EXCEEDED")
    _require(type(digest) is str and len(digest) == 64
             and all(c in "0123456789abcdef" for c in digest), "INVALID_RESPONSE")
    descriptor = reply.descriptors[0]
    info = os.fstat(descriptor)
    _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
             and info.st_size == size, "DESCRIPTOR_MISMATCH")
    _require(fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY,
             "DESCRIPTOR_MISMATCH")
    payload = os.pread(descriptor, size + 1, 0)
    _require(len(payload) == size and hashlib.sha256(payload).hexdigest() == digest,
             "DESCRIPTOR_MISMATCH")
    document = decode_document(payload, maximum_bytes=MAX_TRANSCRIPT_BYTES,
                               maximum_nodes=1_000_000)
    for field in ("model_revision", "duration_ms"):
        _require(field in result and document.get(field) == result[field], "INVALID_RESPONSE")
    for object_name in ("engine", "model"):
        identity = document.get(object_name)
        _require(type(identity) is dict and type(identity.get("id")) is str
                 and identity["id"] == result.get(f"{object_name}_id"), "INVALID_RESPONSE")
    _require(type(document.get("text")) is str and type(document.get("segments")) is list
             and type(document.get("output_format")) is str
             and document["output_format"] in {"text", "json", "webvtt", "srt"}
             and type(document.get("output")) is str, "INVALID_RESPONSE")
    return document


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ProviderError(code)


def _text(value: Any, maximum: int, code: str) -> str:
    _require(type(value) is str and bool(value.strip()), code)
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ProviderError(code) from error
    _require(size <= maximum, code)
    return value


def _prompt_pcm(sample_path: Path) -> tuple[bytes, str, int, int]:
    """Return canonical 24 kHz mono PCM and the source-container digest."""

    container, container_sha256 = read_owned_sample(sample_path)
    try:
        with wave.open(io.BytesIO(container), "rb") as source:
            _require(source.getcomptype() == "NONE", "UNSAFE_INPUT")
            _require(source.getnchannels() == 1, "UNSAFE_INPUT")
            _require(source.getsampwidth() == 2, "UNSAFE_INPUT")
            sample_rate = source.getframerate()
            _require(sample_rate == 24_000, "UNSAFE_INPUT")
            frame_count = source.getnframes()
            _require(0 < frame_count <= 30 * sample_rate, "INPUT_SIZE")
            pcm = source.readframes(frame_count)
    except (EOFError, wave.Error) as error:
        raise StoreError("UNSAFE_INPUT", "source sample is not canonical PCM WAV") from error
    _require(len(pcm) == frame_count * 2, "UNSAFE_INPUT")
    frames_per_ms = sample_rate // 1000
    frame_count -= frame_count % frames_per_ms
    _require(frame_count > 0, "UNSAFE_INPUT")
    pcm = pcm[:frame_count * 2]
    _require(len(pcm) <= MAX_SAMPLE_BYTES, "INPUT_SIZE")
    return pcm, container_sha256, frame_count, frame_count // frames_per_ms


def _memory_descriptor(payload: bytes, private_directory: Path) -> int:
    writer = -1
    reader = -1
    name: str | None = None
    try:
        writer, name = tempfile.mkstemp(
            prefix=".provider-prompt-", dir=private_directory
        )
        view = memoryview(payload)
        while view:
            written = os.write(writer, view)
            _require(written > 0, "INPUT_READ")
            view = view[written:]
        os.fsync(writer)
        reader = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        os.unlink(name)
        name = None
        os.close(writer)
        writer = -1
        return reader
    except Exception:
        if reader >= 0:
            os.close(reader)
        if writer >= 0:
            os.close(writer)
        if name is not None:
            Path(name).unlink(missing_ok=True)
        raise


def _validate_wav_descriptor(descriptor: int, output_bytes: int, duration_ms: int) -> None:
    """Bind result metadata to one bounded canonical WAV descriptor."""

    info = os.fstat(descriptor)
    _require(stat.S_ISREG(info.st_mode), "INVALID_RESPONSE")
    _require(info.st_uid == os.geteuid(), "INVALID_RESPONSE")
    _require(info.st_size == output_bytes, "INVALID_RESPONSE")
    os.lseek(descriptor, 0, os.SEEK_SET)
    try:
        with os.fdopen(os.dup(descriptor), "rb", closefd=True) as source_file:
            with wave.open(source_file, "rb") as source:
                _require(source.getcomptype() == "NONE", "INVALID_RESPONSE")
                _require(source.getnchannels() == 1, "INVALID_RESPONSE")
                _require(source.getsampwidth() == 2, "INVALID_RESPONSE")
                _require(source.getframerate() == 24_000, "INVALID_RESPONSE")
                frames = source.getnframes()
                _require(frames > 0, "INVALID_RESPONSE")
    except (EOFError, OSError, wave.Error) as error:
        raise ProviderError("INVALID_RESPONSE") from error
    measured_ms = (frames * 1_000 + 23_999) // 24_000
    _require(measured_ms == duration_ms, "INVALID_RESPONSE")
    os.lseek(descriptor, 0, os.SEEK_SET)


def _audio_media_type(descriptor: int) -> str:
    try:
        header = os.pread(descriptor, 12, 0)
    except OSError as error:
        raise ProviderError("UNSAFE_INPUT") from error
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "audio/wav"
    if header[:4] == b"fLaC":
        return "audio/flac"
    if header[:4] == b"OggS":
        return "audio/ogg"
    raise ProviderError("UNSAFE_INPUT")


class VoiceboxOrchestrator:
    """Product policy and persistence; model execution stays in providers."""

    def __init__(
        self,
        qwen: ProviderClient,
        transcribe: ProviderClient,
        store: PrivateVoiceStore,
    ) -> None:
        self.qwen = qwen
        self.transcribe = transcribe
        self.store = store

    def synthesize_clone(
        self,
        *,
        profile_id: str,
        sample_path: Path,
        text: str,
        language: str,
        consent_asserted: bool,
        instruction: str | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        text = _text(text, MAX_TEXT_BYTES, "INVALID_TEXT")
        language = _text(language, 64, "INVALID_LANGUAGE")
        if instruction is not None:
            instruction = _text(instruction, MAX_INSTRUCTION_BYTES, "INVALID_INSTRUCTION")
        _require(seed is None or (type(seed) is int and 0 <= seed < 2**63), "INVALID_SEED")
        selected_seed = secrets.randbits(63) if seed is None else seed
        pcm, container_sha256, frame_count, duration_ms = _prompt_pcm(sample_path)
        consent = self.store.attest_sample(
            profile_id=profile_id,
            sample_path=sample_path,
            asserted_by_user=consent_asserted,
            provider_source=pcm,
        )
        _require(consent.source_container_sha256 == container_sha256, "INPUT_CHANGED")
        generation_id = uuid.uuid4().hex
        job_id = f"voicebox-{generation_id}"
        args = {
            "task": "synthesize",
            "text": text,
            "model_id": "auto",
            "language": language,
            "max_duration_ms": 900_000,
            "mode": "prompt_clone",
            "prompt_fd": 0,
            "prompt_audio": {
                "sample_format": "s16le",
                "sample_rate_hz": 24_000,
                "channels": 1,
                "duration_ms": duration_ms,
                "frame_count": frame_count,
                "byte_length": len(pcm),
                "sha256": consent.source_sha256,
            },
            "consent": consent.wire_value,
            "output": {"sample_format": "s16le", "sample_rate_hz": 24_000,
                       "channels": 1},
        }
        if instruction is not None:
            args["instruction"] = instruction
        args["seed"] = selected_seed
        request = {
            "schema": self.qwen.endpoint.schema,
            "type": "request",
            "request_id": uuid.uuid4().hex,
            "op": "submit",
            "job_id": job_id,
            "deadline_ms": 3_600_000,
            "args": args,
        }
        descriptor = _memory_descriptor(pcm, self.store.root)
        try:
            reply = self.qwen.request(request, descriptor)
        finally:
            os.close(descriptor)
        return self._commit_synthesis_reply(
            reply,
            generation_id=generation_id,
            job_id=job_id,
            source_sha256=consent.source_sha256,
            consent_sha256=consent.sha256,
            seed=selected_seed,
        )

    def synthesize_generated(
        self,
        *,
        mode: str,
        text: str,
        language: str,
        voice_id: str | None = None,
        description: str | None = None,
        instruction: str | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """Use a model's built-in voice or design mode without a sample upload."""
        _require(type(mode) is str and mode in {"named_voice", "voice_design"},
                 "UNSUPPORTED_CAPABILITY")
        text = _text(text, MAX_TEXT_BYTES, "INVALID_TEXT")
        language = _text(language, 64, "INVALID_LANGUAGE")
        _require(seed is None or (type(seed) is int and 0 <= seed < 2**63), "INVALID_SEED")
        selected_seed = secrets.randbits(63) if seed is None else seed
        arguments = {
            "task": "synthesize", "mode": mode, "text": text, "language": language,
            "model_id": "auto", "seed": selected_seed, "max_duration_ms": 900_000,
            "output": {"sample_format": "s16le", "sample_rate_hz": 24_000, "channels": 1},
        }
        if mode == "named_voice":
            _require(description is None, "INVALID_REQUEST")
            arguments["voice_id"] = _text(voice_id, 64, "INVALID_VOICE")
        else:
            _require(voice_id is None, "INVALID_REQUEST")
            arguments["description"] = _text(description, MAX_INSTRUCTION_BYTES,
                                              "INVALID_DESCRIPTION")
        if instruction is not None:
            arguments["instruction"] = _text(instruction, MAX_INSTRUCTION_BYTES,
                                              "INVALID_INSTRUCTION")
        generation_id = uuid.uuid4().hex
        job_id = f"voicebox-{generation_id}"
        request = {"schema": self.qwen.endpoint.schema, "type": "request",
                   "request_id": uuid.uuid4().hex, "op": "submit", "job_id": job_id,
                   "deadline_ms": 3_600_000, "args": arguments}
        request_digest = hashlib.sha256(json.dumps(arguments, sort_keys=True,
                                                  separators=(",", ":")).encode()).hexdigest()
        with self.qwen.request(request) as reply:
            _require(reply.payload.get("type") == "result" and len(reply.descriptors) == 1,
                     "INVALID_RESPONSE")
            result = reply.payload.get("result")
            _require(type(result) is dict and set(result) == {
                "engine_id", "engine_revision", "model_id", "model_revision", "duration_ms",
                "seed", "audio",
            }, "INVALID_RESPONSE")
            _require(result["engine_id"] == "qwen3-tts"
                     and result["engine_revision"] == "6cafe5582caea83df269c36b1ce62d953a9cc66b",
                     "INVALID_RESPONSE")
            _require(type(result["model_id"]) is str
                     and _GENERATED_MODELS.get(result["model_id"]) == (result["model_revision"], mode),
                     "INVALID_RESPONSE")
            _require(type(result["seed"]) is int and result["seed"] == selected_seed,
                     "INVALID_RESPONSE")
            duration = result["duration_ms"]
            _require(type(duration) is int and 0 < duration <= 900_000, "INVALID_RESPONSE")
            audio = result["audio"]
            _require(type(audio) is dict and set(audio) == {"byte_length", "sha256"},
                     "INVALID_RESPONSE")
            descriptor = reply.descriptors[0]
            info = os.fstat(descriptor)
            size = duration * 48
            header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", size + 36, b"WAVE",
                                 b"fmt ", 16, 1, 1, 24_000, 48_000, 2, 16, b"data", size)
            _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                     and info.st_size == size + 44
                     and fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
                     and os.pread(descriptor, 44, 0) == header,
                     "INVALID_RESPONSE")
            _require(type(audio["byte_length"]) is int and audio["byte_length"] == size + 44,
                     "INVALID_RESPONSE")
            provenance = GeneratedVoiceProvenance.create(
                generation_id=generation_id, job_id=job_id, provider="kilix-qwen-tts",
                model_id=result["model_id"], model_revision=result["model_revision"],
                engine_id=result["engine_id"], engine_revision=result["engine_revision"],
                output_sha256=audio["sha256"], output_bytes=audio["byte_length"],
                mode=mode, request_sha256=request_digest, seed=selected_seed,
            )
            with os.fdopen(os.dup(descriptor), "rb", closefd=True) as source:
                source.seek(0)
                destination = self.store.commit_generation(source=source, provenance=provenance)
            return {"generation_id": generation_id, "job_id": job_id, "mode": mode,
                    "model_id": result["model_id"], "model_revision": result["model_revision"],
                    "output_sha256": audio["sha256"], "output_bytes": audio["byte_length"],
                    "duration_ms": duration, "seed": selected_seed, "stored_name": destination.name}

    def _commit_synthesis_reply(
        self,
        reply: ProviderReply,
        *,
        generation_id: str,
        job_id: str,
        source_sha256: str,
        consent_sha256: str,
        seed: int,
    ) -> dict[str, Any]:
        with reply:
            _require(reply.payload.get("type") == "result", "INVALID_RESPONSE")
            _require(len(reply.descriptors) == 1, "DESCRIPTOR_MISMATCH")
            result = reply.payload.get("result")
            _require(type(result) is dict, "INVALID_RESPONSE")
            audio = result.get("audio")
            _require(type(audio) is dict, "INVALID_RESPONSE")
            output_sha256 = audio.get("sha256")
            output_bytes = audio.get("byte_length")
            model_id = result.get("model_id")
            model_revision = result.get("model_revision")
            duration_ms = result.get("duration_ms")
            conditioning = result.get("conditioning")
            _require(type(output_sha256) is str and len(output_sha256) == 64,
                     "INVALID_RESPONSE")
            _require(type(output_bytes) is int and output_bytes > 0, "INVALID_RESPONSE")
            _require(type(model_id) is str and type(model_revision) is str, "INVALID_RESPONSE")
            _require(type(duration_ms) is int and 0 < duration_ms <= 900_000,
                     "INVALID_RESPONSE")
            _require(type(conditioning) is dict, "INVALID_RESPONSE")
            _require(conditioning.get("prompt_sha256") == source_sha256,
                     "INVALID_RESPONSE")
            _require(conditioning.get("consent_sha256") == consent_sha256,
                     "INVALID_RESPONSE")
            _validate_wav_descriptor(reply.descriptors[0], output_bytes, duration_ms)
            provenance = GenerationProvenance.create(
                generation_id=generation_id,
                job_id=job_id,
                provider="kilix-qwen-tts",
                model_id=model_id,
                model_revision=model_revision,
                output_sha256=output_sha256,
                output_bytes=output_bytes,
                source_sha256=source_sha256,
                consent_sha256=consent_sha256,
            )
            with os.fdopen(os.dup(reply.descriptors[0]), "rb", closefd=True) as source:
                destination = self.store.commit_generation(source=source, provenance=provenance)
            return {
                "generation_id": generation_id,
                "job_id": job_id,
                "model_id": model_id,
                "model_revision": model_revision,
                "output_sha256": output_sha256,
                "output_bytes": output_bytes,
                "duration_ms": duration_ms,
                "seed": seed,
                "stored_name": destination.name,
            }

    def transcribe_file(
        self,
        *,
        audio_path: Path,
        language: str | None = None,
        output: str = "json",
        task: str = "transcribe",
    ) -> dict[str, Any]:
        _require(task in {"transcribe", "translate", "diarize"}, "UNSUPPORTED_CAPABILITY")
        _require(output in {"text", "json", "webvtt", "srt"}, "UNSUPPORTED_CAPABILITY")
        if language is not None:
            language = _text(language, 64, "INVALID_LANGUAGE")
        info = audio_path.stat(follow_symlinks=False)
        _require(stat.S_ISREG(info.st_mode), "UNSAFE_INPUT")
        _require(info.st_uid == os.geteuid() and 0 < info.st_size <= 2_147_483_648,
                 "UNSAFE_INPUT")
        digest = hashlib.sha256()
        descriptor = os.open(audio_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            media_type = _audio_media_type(descriptor)
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(descriptor)
            _require((after.st_dev, after.st_ino, after.st_size)
                     == (info.st_dev, info.st_ino, info.st_size), "INPUT_CHANGED")
            os.lseek(descriptor, 0, os.SEEK_SET)
            job_id = f"voicebox-{uuid.uuid4().hex}"
            request = {
                "schema": self.transcribe.endpoint.schema,
                "type": "request",
                "request_id": uuid.uuid4().hex,
                "op": "submit",
                "job_id": job_id,
                "deadline_ms": 3_600_000,
                "args": {
                    "task": task,
                    "language": language,
                    "output": output,
                    "audio_fd": 0,
                    "audio": {
                        "byte_length": info.st_size,
                        "sha256": digest.hexdigest(),
                        "media_type": media_type,
                    },
                    "diarization_session": task == "diarize",
                },
            }
            reply = self.transcribe.request(request, descriptor)
        finally:
            os.close(descriptor)
        with reply:
            return _transcript_result(reply)

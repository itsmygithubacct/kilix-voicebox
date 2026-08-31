"""Transcription provider access; Voicebox contains no Whisper runtime."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from .kilix_provider import (
    TRANSCRIBE_ENDPOINT,
    ProviderClient,
    ProviderError,
    runtime_directory_from_environment,
)


_client: ProviderClient | None = None


def get_transcribe_client(runtime_directory: Path | None = None) -> ProviderClient:
    global _client
    if runtime_directory is not None:
        return ProviderClient(TRANSCRIBE_ENDPOINT, runtime_directory)
    if _client is None:
        _client = ProviderClient(TRANSCRIBE_ENDPOINT, runtime_directory_from_environment())
    return _client


async def status() -> dict[str, Any]:
    client = get_transcribe_client()
    request = {
        "schema": client.endpoint.schema,
        "type": "request",
        "request_id": uuid.uuid4().hex,
        "op": "status",
        "deadline_ms": 5_000,
        "args": {},
    }
    with await asyncio.to_thread(client.request, request) as reply:
        result = reply.payload.get("result")
        if type(result) is not dict:
            raise ProviderError("INVALID_RESPONSE")
        return result


async def models() -> list[dict[str, Any]]:
    client = get_transcribe_client()
    request = {
        "schema": client.endpoint.schema,
        "type": "request",
        "request_id": uuid.uuid4().hex,
        "op": "models",
        "deadline_ms": 5_000,
        "args": {},
    }
    with await asyncio.to_thread(client.request, request) as reply:
        result = reply.payload.get("result")
        population = result.get("models") if type(result) is dict else None
        if type(population) is not list or any(type(item) is not dict for item in population):
            raise ProviderError("INVALID_RESPONSE")
        return population


async def unload_transcribe_model() -> None:
    client = get_transcribe_client()
    request = {
        "schema": client.endpoint.schema,
        "type": "request",
        "request_id": uuid.uuid4().hex,
        "op": "unload",
        "deadline_ms": 30_000,
        "args": {},
    }
    with await asyncio.to_thread(client.request, request):
        return None


def reset_client() -> None:
    global _client
    _client = None

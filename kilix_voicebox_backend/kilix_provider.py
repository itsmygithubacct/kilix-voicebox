"""Private Unix-socket clients for Kilix speech providers.

Voicebox owns product orchestration and user data, never a second copy of the
Qwen or transcription runtime.  This candidate client is deliberately small:
it connects only to fixed provider socket names beneath a private runtime
directory, verifies the kernel peer UID, transfers at most one descriptor, and
returns bounded structured events without exposing provider exception text.
"""

from __future__ import annotations

import array
import json
import math
import os
import socket
import stat
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


QWEN_SCHEMA = "kilix.qwen-tts.provider/candidate-v1"
TRANSCRIBE_SCHEMA = "kilix.transcribe.provider/candidate-v1"
MAX_CONTROL_BYTES = 65_536
MAX_EVENTS = 256
MAX_DEPTH = 16
MAX_NODES = 2_048
MAX_KEY_BYTES = 64
MAX_NUMBER_BYTES = 64
MAX_INTEGER_BITS = 213
_HEADER = struct.Struct("!I")
_PEERCRED = struct.Struct("3i")


class ProviderError(RuntimeError):
    """Redacted provider failure safe for the Voicebox API boundary."""

    def __init__(self, code: str):
        safe_code = (
            code
            if type(code) is str
            and 0 < len(code) <= 64
            and "A" <= code[0] <= "Z"
            and all("A" <= character <= "Z" or "0" <= character <= "9" or character == "_"
                    for character in code)
            else "PROVIDER_ERROR"
        )
        super().__init__(f"speech provider refused the request ({safe_code})")
        self.code = safe_code


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ProviderError(code)


def _constant(_value: str) -> None:
    raise ProviderError("INVALID_RESPONSE")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, "INVALID_RESPONSE")
        result[key] = value
    return result


def _integer(token: str) -> int:
    _require(len(token) <= MAX_NUMBER_BYTES, "LIMIT_EXCEEDED")
    value = int(token)
    _require(value.bit_length() <= MAX_INTEGER_BITS, "LIMIT_EXCEEDED")
    return value


def _floating(token: str) -> float:
    _require(len(token) <= MAX_NUMBER_BYTES, "LIMIT_EXCEEDED")
    value = float(token)
    _require(math.isfinite(value), "INVALID_RESPONSE")
    return value


def _raw_depth(text: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            _require(depth <= MAX_DEPTH, "LIMIT_EXCEEDED")
        elif character in "]}":
            depth -= 1
            _require(depth >= 0, "INVALID_RESPONSE")


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ProviderError("INVALID_RESPONSE") from error


def _validate_structure(value: Any, *, maximum_bytes: int = MAX_CONTROL_BYTES,
                        maximum_nodes: int = MAX_NODES) -> None:
    nodes = 0
    scalar_bytes = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        _require(nodes <= maximum_nodes and depth <= MAX_DEPTH, "LIMIT_EXCEEDED")
        if isinstance(current, dict):
            _require(nodes + len(stack) + len(current) <= maximum_nodes, "LIMIT_EXCEEDED")
            for key, child in current.items():
                _require(type(key) is str and _utf8_size(key) <= MAX_KEY_BYTES,
                         "INVALID_RESPONSE")
                scalar_bytes += _utf8_size(key)
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            _require(nodes + len(stack) + len(current) <= maximum_nodes, "LIMIT_EXCEEDED")
            stack.extend((child, depth + 1) for child in current)
        elif type(current) is int:
            _require(current.bit_length() <= MAX_INTEGER_BITS, "LIMIT_EXCEEDED")
            scalar_bytes += len(str(current))
        elif type(current) is float:
            _require(math.isfinite(current), "INVALID_RESPONSE")
            scalar_bytes += len(repr(current))
        elif type(current) is str:
            scalar_bytes += _utf8_size(current)
        else:
            _require(current is None or type(current) is bool, "INVALID_RESPONSE")
            scalar_bytes += 4
        _require(scalar_bytes <= maximum_bytes, "LIMIT_EXCEEDED")


def encode_message(value: dict[str, Any]) -> bytes:
    _require(type(value) is dict, "INVALID_REQUEST")
    _validate_structure(value)
    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeEncodeError) as error:
        raise ProviderError("INVALID_REQUEST") from error
    _require(0 < len(payload) <= MAX_CONTROL_BYTES, "LIMIT_EXCEEDED")
    return _HEADER.pack(len(payload)) + payload


def decode_message(packet: bytes) -> dict[str, Any]:
    _require(type(packet) is bytes and len(packet) >= _HEADER.size, "INVALID_RESPONSE")
    declared = _HEADER.unpack_from(packet)[0]
    payload = packet[_HEADER.size:]
    _require(declared == len(payload) and 0 < declared <= MAX_CONTROL_BYTES,
             "INVALID_RESPONSE")
    return decode_document(payload)


def decode_document(payload: bytes, *, maximum_bytes: int = MAX_CONTROL_BYTES,
                    maximum_nodes: int = MAX_NODES) -> dict[str, Any]:
    """Strict JSON for control packets or explicitly bounded result descriptors."""
    _require(type(payload) is bytes and 0 < len(payload) <= maximum_bytes,
             "LIMIT_EXCEEDED")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ProviderError("INVALID_RESPONSE") from error
    _raw_depth(text)
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_constant,
            parse_int=_integer,
            parse_float=_floating,
        )
    except ProviderError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ProviderError("INVALID_RESPONSE") from error
    _require(type(value) is dict, "INVALID_RESPONSE")
    _validate_structure(value, maximum_bytes=maximum_bytes, maximum_nodes=maximum_nodes)
    return value


def _close_all(descriptors: list[int] | tuple[int, ...]) -> None:
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError:
            pass


def send_message(channel: socket.socket, value: dict[str, Any], descriptor: int | None = None) -> None:
    packet = encode_message(value)
    ancillary: list[tuple[int, int, bytes]] = []
    if descriptor is not None:
        _require(type(descriptor) is int and descriptor >= 0, "DESCRIPTOR_MISMATCH")
        values = array.array("i", [descriptor])
        ancillary.append((socket.SOL_SOCKET, socket.SCM_RIGHTS, values.tobytes()))
    try:
        sent = channel.sendmsg([packet], ancillary)
    except OSError as error:
        raise ProviderError("PROVIDER_UNAVAILABLE") from error
    _require(sent == len(packet), "TRANSPORT_ERROR")


def receive_message(channel: socket.socket) -> tuple[dict[str, Any], tuple[int, ...]]:
    try:
        packet, ancillary, flags, _address = channel.recvmsg(
            MAX_CONTROL_BYTES + _HEADER.size,
            socket.CMSG_SPACE(array.array("i").itemsize),
            getattr(socket, "MSG_CMSG_CLOEXEC", 0),
        )
    except (OSError, TimeoutError) as error:
        raise ProviderError("PROVIDER_UNAVAILABLE") from error
    descriptors: list[int] = []
    try:
        for level, kind, data in ancillary:
            _require(level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS,
                     "DESCRIPTOR_MISMATCH")
            values = array.array("i")
            usable = len(data) - len(data) % values.itemsize
            values.frombytes(data[:usable])
            descriptors.extend(values)
        _require(not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC), "LIMIT_EXCEEDED")
        _require(len(descriptors) <= 1, "DESCRIPTOR_MISMATCH")
        return decode_message(packet), tuple(descriptors)
    except Exception:
        _close_all(descriptors)
        raise


@dataclass(frozen=True, slots=True)
class ProviderEndpoint:
    name: str
    schema: str
    socket_name: str

    def __post_init__(self) -> None:
        _require(self.name in {"qwen", "transcribe"}, "INVALID_ENDPOINT")
        _require(self.schema in {QWEN_SCHEMA, TRANSCRIBE_SCHEMA}, "INVALID_ENDPOINT")
        _require(self.socket_name in {"kilix-qwen-tts.sock", "kilix-transcribe.sock"},
                 "INVALID_ENDPOINT")


QWEN_ENDPOINT = ProviderEndpoint("qwen", QWEN_SCHEMA, "kilix-qwen-tts.sock")
TRANSCRIBE_ENDPOINT = ProviderEndpoint(
    "transcribe", TRANSCRIBE_SCHEMA, "kilix-transcribe.sock"
)


@dataclass(slots=True)
class ProviderReply:
    payload: dict[str, Any]
    descriptors: tuple[int, ...]

    def close(self) -> None:
        _close_all(self.descriptors)
        self.descriptors = ()

    def __enter__(self) -> "ProviderReply":
        return self

    def __exit__(self, _kind, _value, _traceback) -> None:
        self.close()


class ProviderClient:
    """One-request client for a fixed, same-UID provider endpoint."""

    def __init__(self, endpoint: ProviderEndpoint, runtime_directory: Path, timeout: float = 3600.0):
        _require(isinstance(runtime_directory, Path) and runtime_directory.is_absolute(),
                 "INVALID_ENDPOINT")
        _require(type(timeout) in {int, float} and type(timeout) is not bool
                 and math.isfinite(timeout) and 0 < timeout <= 3_600,
                 "INVALID_ENDPOINT")
        self.endpoint = endpoint
        self.runtime_directory = runtime_directory
        self.timeout = float(timeout)

    @property
    def socket_path(self) -> Path:
        return self.runtime_directory / self.endpoint.socket_name

    def _validate_endpoint(self) -> None:
        runtime = self.runtime_directory.stat(follow_symlinks=False)
        _require(stat.S_ISDIR(runtime.st_mode), "INVALID_ENDPOINT")
        _require(runtime.st_uid == os.geteuid() and runtime.st_mode & 0o077 == 0,
                 "UNAUTHORIZED_PEER")
        endpoint = self.socket_path.stat(follow_symlinks=False)
        _require(stat.S_ISSOCK(endpoint.st_mode), "INVALID_ENDPOINT")
        _require(endpoint.st_uid == os.geteuid() and endpoint.st_mode & 0o177 == 0,
                 "UNAUTHORIZED_PEER")

    def _connect(self, timeout: float | None = None) -> socket.socket:
        channel: socket.socket | None = None
        try:
            self._validate_endpoint()
            channel = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            channel.settimeout(self.timeout if timeout is None else timeout)
            channel.connect(str(self.socket_path))
            _require(hasattr(socket, "SO_PEERCRED"), "PEER_CREDENTIALS_UNAVAILABLE")
            credentials = channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                              _PEERCRED.size)
            _pid, uid, _gid = _PEERCRED.unpack(credentials)
            _require(uid == os.geteuid(), "UNAUTHORIZED_PEER")
            return channel
        except ProviderError:
            if channel is not None:
                channel.close()
            raise
        except (AttributeError, OSError, ValueError) as error:
            if channel is not None:
                channel.close()
            raise ProviderError("PROVIDER_UNAVAILABLE") from error

    def request(self, value: dict[str, Any], descriptor: int | None = None) -> ProviderReply:
        _require(value.get("schema") == self.endpoint.schema, "INCOMPATIBLE_PROTOCOL")
        request_id = value.get("request_id")
        _require(type(request_id) is str and bool(request_id), "INVALID_REQUEST")
        job_id = value.get("job_id")
        deadline_ms = value.get("deadline_ms")
        _require(type(deadline_ms) is int and 0 < deadline_ms <= 3_600_000,
                 "INVALID_REQUEST")
        started = time.monotonic()
        deadline = started + deadline_ms / 1000
        channel = self._connect(min(self.timeout, deadline_ms / 1000))
        try:
            send_message(channel, value, descriptor)
            for _index in range(MAX_EVENTS):
                remaining = deadline - time.monotonic()
                _require(remaining > 0, "DEADLINE_EXCEEDED")
                channel.settimeout(min(self.timeout, remaining))
                try:
                    event, descriptors = receive_message(channel)
                except ProviderError as error:
                    if error.code == "PROVIDER_UNAVAILABLE" and time.monotonic() >= deadline:
                        raise ProviderError("DEADLINE_EXCEEDED") from None
                    raise
                if event.get("request_id") != request_id:
                    _close_all(descriptors)
                    raise ProviderError("INVALID_RESPONSE")
                if event.get("schema") != self.endpoint.schema:
                    _close_all(descriptors)
                    raise ProviderError("INCOMPATIBLE_PROTOCOL")
                if event.get("job_id") != job_id:
                    _close_all(descriptors)
                    raise ProviderError("INVALID_RESPONSE")
                kind = event.get("type")
                if type(kind) is not str:
                    _close_all(descriptors)
                    raise ProviderError("INVALID_RESPONSE")
                if kind == "error":
                    _close_all(descriptors)
                    error = event.get("error")
                    code = error.get("code") if type(error) is dict else None
                    raise ProviderError(code if type(code) is str and bool(code) else "PROVIDER_ERROR")
                if kind in {"result", "hello", "models", "status", "unloaded", "canceled"}:
                    return ProviderReply(event, descriptors)
                _close_all(descriptors)
                _require(kind in {"accepted", "queued", "loading", "progress", "chunk"},
                         "INVALID_RESPONSE")
            raise ProviderError("EVENT_LIMIT_EXCEEDED")
        finally:
            channel.close()


def runtime_directory_from_environment() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR")
    _require(type(value) is str and bool(value), "INVALID_ENDPOINT")
    path = Path(value)
    _require(path.is_absolute(), "INVALID_ENDPOINT")
    return path

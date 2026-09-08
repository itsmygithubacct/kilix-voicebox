"""HTTP lifetime adapters for the installed Voice owner and transcribe client.

This module owns request events and bounded PCM only. Voice owns the microphone
lease, recorder, descendant supervision and authenticated cleanup proof. No
constructor opens audio, and no recorded bytes enter the consent store here.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

from .kilix_provider import runtime_directory_from_environment
from .orchestrator import _audio_media_type


class CaptureRefusal(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _voice():
    try:
        return (importlib.import_module("voicelib.audio"),
                importlib.import_module("voicelib.microphone"),
                importlib.import_module("voicelib.settings"),
                importlib.import_module("voicelib.util"))
    except (ImportError, AttributeError):
        raise CaptureRefusal("MICROPHONE_UNAVAILABLE") from None


async def until_disconnected(request, function, gone: threading.Event):
    """Retain the owned operation until it returns, even on task cancellation.

    The operation must observe gone and have its own absolute deadline. A closed
    HTTP request is cancellation authority, never evidence of provider cleanup.
    """
    task = asyncio.create_task(asyncio.to_thread(function))
    try:
        while not task.done():
            if await request.is_disconnected():
                gone.set()
            await asyncio.wait({task}, timeout=0.05)
        return task.result()
    finally:
        gone.set()
        # Shield prevents cancellation of the future from hiding a live owner.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()


class RecordingRequest:
    def __init__(self, identifier: str, seconds: int, gone: threading.Event):
        self.identifier = identifier
        self.seconds = seconds
        self.gone = gone
        self.stop = threading.Event()
        self.cancel = threading.Event()
        self.started = time.monotonic()
        # The requested PCM duration is separate from the fixed teardown margin.
        # This one absolute limit is never renewed while acquiring or recording.
        self.deadline = self.started + seconds + 2
        self.duration = 0.0
        self.phase = "starting"
        self.entered = False


class CaptureBridge:
    """One pending HTTP recording, backed exclusively by Voice MicCapture.

    cfg/locked/namespace are Python fixture seams, never request/environment
    recorder overrides. Production uses Voice settings and the actual logind
    lock adapter. 'inactive' describes this bridge, not a globally free device.
    """
    def __init__(self, *, cfg=None, locked=None, namespace=None):
        self._cfg, self._locked, self._namespace = cfg, locked, namespace
        self._mutex = threading.Lock()
        self._active: RecordingRequest | None = None
        self._unavailable = False
        self._closing = False
        self._last_id: str | None = None

    def status(self) -> dict:
        with self._mutex:
            item = self._active
            return {"phase": "unavailable" if self._unavailable else (
                        item.phase if item else "inactive"),
                    "id": item.identifier if item else None,
                    "last_id": self._last_id,
                    "duration": item.duration if item else 0.0}

    def claim(self, identifier: str, seconds: int, gone: threading.Event) -> RecordingRequest:
        if (type(identifier) is not str or re.fullmatch(r"[0-9a-f]{32}", identifier) is None
                or type(seconds) is not int or not 1 <= seconds <= 30):
            raise CaptureRefusal("RECORDING_INVALID")
        with self._mutex:
            if self._unavailable or self._closing:
                raise CaptureRefusal("MICROPHONE_UNAVAILABLE")
            if self._active is not None:
                raise CaptureRefusal("MICROPHONE_BUSY")
            item = RecordingRequest(identifier, seconds, gone)
            self._active = item
            return item

    def control(self, identifier: str, *, cancel: bool) -> dict:
        with self._mutex:
            item = self._active
            if item is None or item.identifier != identifier:
                raise CaptureRefusal("RECORDING_NOT_ACTIVE")
            (item.cancel if cancel else item.stop).set()
            item.phase = "stopping"
        return self.status()

    def shutdown(self):
        with self._mutex:
            self._closing = True
            if self._active is not None:
                self._active.cancel.set()

    def abandon_unstarted(self, item: RecordingRequest):
        """An executor allocation failure starts no owner and holds no lease."""
        with self._mutex:
            if self._active is item and not item.entered:
                self._active = None
                self._last_id = item.identifier

    def record(self, item: RecordingRequest) -> bytes:
        with self._mutex:
            item.entered = True
        capture = None
        proven = True
        pcm = bytearray()
        try:
            audio, microphone, settings, util = _voice()
            cfg = self._cfg
            if cfg is None:
                values = settings.load()
                cfg = {"audio": {"device_in": values[settings.KEY_DEVICE_IN],
                                 "rate": 16000, "frame_ms": 20}}

            def indicator(phase):
                # Voice can prove its process cleanup before this adapter has
                # finished discarding data; retain the stopping indication.
                with self._mutex:
                    item.phase = "stopping" if phase == "inactive" else phase

            kwargs = {"cancelled": item.cancel.is_set, "disconnected": item.gone.is_set,
                      "indicator": indicator}
            if self._locked is not None:
                kwargs["locked"] = self._locked
            if self._namespace is not None:
                kwargs["namespace"] = self._namespace
            control = microphone.CaptureControl(item.deadline, **kwargs)
            control.check()
            if item.stop.is_set():
                raise CaptureRefusal("RECORDING_EMPTY")
            capture = audio.MicCapture(cfg, control=control)
            capture.start()
            maximum = item.seconds * capture.rate * 2
            stop_at = min(item.started + item.seconds, item.deadline)
            while time.monotonic() < stop_at and len(pcm) < maximum:
                control.check()
                if item.stop.is_set():
                    break
                frame = capture.read(timeout=0.05)
                if capture.error or capture.phase not in {"starting", "recording"}:
                    raise CaptureRefusal("RECORDING_FAILED")
                if frame is not None:
                    pcm.extend(frame[:maximum - len(pcm)])
                    item.duration = len(pcm) / (capture.rate * 2)
            control.check()
            item.phase = "stopping"
            proven = capture.stop()
            if not proven:
                raise CaptureRefusal("MICROPHONE_UNAVAILABLE")
            control.check()
            if capture.error or capture.overruns:
                raise CaptureRefusal("RECORDING_FAILED")
            if not pcm:
                raise CaptureRefusal("RECORDING_EMPTY")
            return util.write_wav(bytes(pcm), capture.rate)
        except CaptureRefusal:
            raise
        except Exception:
            # Includes dependency/recorder/lock errors: never expose argv, paths,
            # diagnostic text or a stack trace through the product API.
            raise CaptureRefusal("RECORDING_REFUSED") from None
        finally:
            try:
                if capture is not None:
                    proven = capture.stop() and proven
            except BaseException:
                proven = False
                raise
            finally:
                pcm.clear()
                with self._mutex:
                    if not proven:
                        self._unavailable = True
                    if self._active is item:
                        self._active = None
                        self._last_id = item.identifier
                if not proven:
                    raise CaptureRefusal("MICROPHONE_UNAVAILABLE") from None


def transcribe_owned(path: Path, language: str | None, gone: threading.Event,
                     *, deadline: float, locked=None) -> dict:
    """Use the installed controlled client; borrow one exact staged input FD.

    The original HTTP budget also covers hashing and final delivery. Local
    cancellation closes this request only, and does not mean a provider is ready.
    """
    descriptor = -1
    try:
        _, microphone, _, _ = _voice()
        try:
            client = importlib.import_module("kilix_transcribe.service")
        except ImportError:
            raise CaptureRefusal("TRANSCRIBE_UNAVAILABLE") from None
        lock_state = locked if locked is not None else microphone.LogindLockState()

        def cancelled():
            return gone.is_set() or time.monotonic() >= deadline or lock_state() is not False

        def check():
            if cancelled():
                raise CaptureRefusal("TRANSCRIPTION_STOPPED")

        check()
        before = path.stat(follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or not 0 < before.st_size <= 2_147_483_648):
            raise CaptureRefusal("INPUT_INVALID")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino, info.st_size) != (before.st_dev, before.st_ino, before.st_size):
            raise CaptureRefusal("INPUT_CHANGED")
        media_type = _audio_media_type(descriptor)
        digest = hashlib.sha256()
        count = 0
        while True:
            check()
            data = os.read(descriptor, min(1048576, info.st_size + 1 - count))
            if not data:
                break
            count += len(data)
            if count > info.st_size:
                raise CaptureRefusal("INPUT_CHANGED")
            digest.update(data)
        if count != info.st_size:
            raise CaptureRefusal("INPUT_CHANGED")
        os.lseek(descriptor, 0, os.SEEK_SET)
        check()
        value = client.request_value("submit", job_id="voicebox-" + uuid.uuid4().hex,
            timeout=max(0.001, deadline - time.monotonic()), args={
                "task": "transcribe", "language": language, "output": "json",
                "audio_fd": 0, "audio": {"byte_length": count,
                    "sha256": digest.hexdigest(), "media_type": media_type},
                "diarization_session": False})
        result = client.client_request(runtime_directory_from_environment(), value, descriptor,
                                       cancelled=cancelled)
        check()
        if (type(result) is not dict or type(result.get("text")) is not str
                or type(result.get("duration_ms")) is not int or result["duration_ms"] < 0):
            raise CaptureRefusal("TRANSCRIPTION_INVALID")
        return result
    except CaptureRefusal:
        raise
    except Exception:
        raise CaptureRefusal("TRANSCRIPTION_REFUSED") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)

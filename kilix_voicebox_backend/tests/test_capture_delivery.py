"""Final recording authority through real cleanup and handler acceptance."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException
from voicelib import audio, microphone, util
from kilix_voicebox_backend import main
from kilix_voicebox_backend.managed_capture import CaptureBridge, CaptureRefusal, until_disconnected

PEER = str(Path(__file__).with_name("capture_peer.py"))


class CaptureDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="delivery-")
        self.root = Path(self.temp.name)
        self.value = False
        self.bridge = CaptureBridge(cfg={"audio": {"capture_cmd": [sys.executable, "-I", PEER,
            "recorder", str(self.root)]}}, locked=self.locked, namespace=str(self.root / "leases"))
        self.fd0 = len(os.listdir("/proc/self/fd"))

    def locked(self):
        if self.value == "raising":
            raise RuntimeError("private authority diagnostic")
        return self.value

    def tearDown(self):
        self.bridge.shutdown()
        self.assertEqual(len(os.listdir("/proc/self/fd")), self.fd0)
        self.temp.cleanup()

    def reaped(self):
        tree = json.loads((self.root / "tree.json").read_text())
        self.assertTrue(all(not Path(f"/proc/{pid}").exists() for pid in tree.values()))

    def change(self, mode, item):
        self.reaped()
        self.assertEqual(self.bridge.status()["id"], item.identifier)
        if mode == "cancel":
            self.bridge.control(item.identifier, cancel=True)
        elif mode == "disconnect":
            item.gone.set()
        elif mode == "locked":
            self.value = True
        elif mode == "unknown":
            self.value = None
        elif mode == "raising":
            self.value = "raising"
        elif mode == "deadline":
            # Consume the actual original deadline; do not replace its control.
            time.sleep(max(0, item.control.deadline - time.monotonic()) + 0.01)

    def direct_boundary(self, boundary):
        for mode in ("valid", "cancel", "disconnect", "locked", "unknown", "raising", "deadline"):
            with self.subTest(boundary=boundary, mode=mode):
                self.value = False
                item = self.bridge.claim(uuid.uuid4().hex, 1, threading.Event())
                write, stop = util.write_wav, audio.MicCapture.stop
                visits = []
                def serialized(pcm, rate):
                    payload = write(pcm, rate)
                    if boundary == "serialization":
                        visits.append(True)
                        self.change(mode, item)
                    return payload
                stops = []
                def cleanup(capture):
                    result = stop(capture)
                    self.assertIs(capture._control, item.control)
                    stops.append(True)
                    if boundary == "final-cleanup" and len(stops) == 2:
                        visits.append(True)
                        self.change(mode, item)
                    return result
                with patch.object(util, "write_wav", serialized), patch.object(audio.MicCapture, "stop", cleanup):
                    if mode == "valid":
                        self.assertTrue(self.bridge.record(item).startswith(b"RIFF"))
                    else:
                        with self.assertRaisesRegex(CaptureRefusal, "^RECORDING_REFUSED$"):
                            self.bridge.record(item)
                self.assertEqual(visits, [True])
                self.reaped()
                self.assertEqual(self.bridge.status()["phase"], "inactive")
                self.assertEqual(len(os.listdir("/proc/self/fd")), self.fd0)

    def test_late_serialization_authority(self):
        self.direct_boundary("serialization")

    def test_authority_changes_inside_last_capture_cleanup(self):
        self.direct_boundary("final-cleanup")

    def test_handler_acceptance_rechecks_after_response_construction(self):
        class Request:
            async def is_disconnected(self):
                return False
        response = main.Response
        for mode in ("valid", "cancel", "disconnect", "locked", "unknown", "raising", "deadline"):
            with self.subTest(mode=mode):
                self.value = False
                visited = []
                def construct(*args, **kwargs):
                    result = response(*args, **kwargs)
                    item = self.bridge._active
                    self.assertTrue(item.completed)
                    self.assertFalse(item.gone.is_set())
                    visited.append(item.identifier)
                    self.change(mode, item)
                    with self.assertRaisesRegex(CaptureRefusal, "MICROPHONE_BUSY"):
                        self.bridge.claim(uuid.uuid4().hex, 1, threading.Event())
                    return result
                with patch.object(main, "_capture_bridge", self.bridge), patch.object(main, "Response", construct):
                    call = main.recording_start(main.RecordingInput(id=uuid.uuid4().hex, seconds=1), Request())
                    if mode == "valid":
                        self.assertTrue(asyncio.run(call).body.startswith(b"RIFF"))
                    else:
                        with self.assertRaises(HTTPException) as found:
                            asyncio.run(call)
                        self.assertEqual(found.exception.status_code, 409)
                        self.assertEqual(found.exception.detail, {"code": "RECORDING_REFUSED"})
                self.assertEqual(len(visited), 1)
                self.reaped()
                self.assertEqual(self.bridge.status()["phase"], "inactive")
                self.assertEqual(len(os.listdir("/proc/self/fd")), self.fd0)

    def test_deferred_claim_blocks_successor_until_checked_finalization(self):
        item = self.bridge.claim(uuid.uuid4().hex, 1, threading.Event())
        payload = self.bridge.record(item, defer_delivery=True)
        self.reaped()
        self.assertTrue(payload.startswith(b"RIFF"))
        self.assertEqual(self.bridge.status()["id"], item.identifier)
        self.bridge.control(item.identifier, cancel=False)
        self.bridge.finalize(item, deliver=True)
        self.assertEqual(self.bridge.status()["phase"], "inactive")
        successor = self.bridge.claim(uuid.uuid4().hex, 1, threading.Event())
        self.bridge.finalize(item)
        self.assertEqual(self.bridge.status()["id"], successor.identifier)
        with self.assertRaisesRegex(CaptureRefusal, "RECORDING_NOT_ACTIVE"):
            self.bridge.finalize(item, deliver=True)
        self.bridge.finalize(successor)

    def test_pending_owner_cannot_be_released_by_finalization(self):
        item = self.bridge.claim(uuid.uuid4().hex, 1, threading.Event())
        item.entered = True  # Synthetic executor barrier before allocation.
        with self.assertRaisesRegex(CaptureRefusal, "RECORDING_PENDING"):
            self.bridge.finalize(item)
        self.assertEqual(self.bridge.status()["id"], item.identifier)
        self.assertTrue(item.cancel.is_set())
        item.completed = True
        self.bridge.finalize(item)

    def test_successful_waiter_completion_is_not_receiver_loss(self):
        class Request:
            async def is_disconnected(self):
                return False
        gone = threading.Event()
        result = asyncio.run(until_disconnected(Request(), lambda: b"owned result", gone))
        self.assertEqual(result, b"owned result")
        self.assertFalse(gone.is_set())

    def test_actual_lock_adapter_retains_declared_cache(self):
        lock = microphone.LogindLockState("fixture")
        state, calls = [False], []
        def query(*args):
            calls.append(time.monotonic())
            return f"User={os.geteuid()}\nActive=yes\nLockedHint={'yes' if state[0] else 'no'}"
        with patch.object(lock, "_query", query):
            self.assertIs(lock(), False)
            state[0] = True
            # Same real adapter, unchanged100ms cache; not a physical lock test.
            self.assertIs(lock(), False)
            self.assertEqual(len(calls), 1)
            time.sleep(max(0, lock._until - time.monotonic()) + 0.01)
            self.assertIs(lock(), True)
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()

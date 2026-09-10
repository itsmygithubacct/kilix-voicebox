"""Managed capture uses real synthetic descendants and the installed clients."""
import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid
import wave

from kilix_voicebox_backend.managed_capture import (
    CaptureBridge, CaptureRefusal, transcribe_owned, until_disconnected,
)
from .voice_optional import VOICE_MICROPHONE_AVAILABLE, audio

PEER = str(Path(__file__).with_name("capture_peer.py"))


def wait_for(predicate, seconds=4):
    until = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= until:
            raise AssertionError("owned observation timed out")
        time.sleep(0.01)


@unittest.skipUnless(VOICE_MICROPHONE_AVAILABLE, "managed voice microphone API is not installed")
class ManagedCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.locked = threading.Event()
        self.bridge = CaptureBridge(cfg={"audio": {"capture_cmd": [sys.executable, "-I", PEER,
            "recorder", str(self.root)]}}, locked=self.locked.is_set,
            namespace=str(self.root / "leases"))
        self.threads = []
        self.processes = []
        self.fd_before = len(os.listdir("/proc/self/fd"))

    def tearDown(self):
        self.bridge.shutdown()
        for thread in self.threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=4)
        self.temp.cleanup()
        self.assertEqual(len(os.listdir("/proc/self/fd")), self.fd_before)

    def run_record(self, seconds=4, deadline=None):
        gone = threading.Event()
        item = self.bridge.claim(uuid.uuid4().hex, seconds, gone)
        if deadline is not None:
            item.deadline = time.monotonic() + deadline
        outcome = []
        def run():
            try:
                outcome.append(self.bridge.record(item))
            except CaptureRefusal as error:
                outcome.append(error.code)
        thread = threading.Thread(target=run)
        thread.start()
        self.threads.append(thread)
        wait_for(lambda: (self.root / "tree.json").exists() or bool(outcome))
        return item, gone, thread, outcome

    def tree(self):
        return json.loads((self.root / "tree.json").read_text())

    def absent(self, tree):
        for pid in tree.values():
            self.assertFalse(Path(f"/proc/{pid}").exists())

    def test_no_capture_before_claim_and_shared_owner_exclusivity(self):
        self.assertEqual(self.bridge.status()["phase"], "inactive")
        self.assertEqual(list(self.root.iterdir()), [])
        item, _, thread, outcome = self.run_record()
        tree = self.tree()
        self.assertEqual(self.bridge.status()["phase"], "recording")
        with self.assertRaisesRegex(CaptureRefusal, "MICROPHONE_BUSY"):
            self.bridge.claim(uuid.uuid4().hex, 1, threading.Event())
        other = CaptureBridge(cfg=self.bridge._cfg, locked=lambda: False,
                              namespace=str(self.root / "leases"))
        with self.assertRaises(CaptureRefusal):
            other.record(other.claim(uuid.uuid4().hex, 1, threading.Event()))
        self.assertTrue(Path(f"/proc/{tree['pid']}").exists())
        wait_for(lambda: item.duration > 0)
        self.bridge.control(item.identifier, cancel=False)
        thread.join(4)
        self.assertIsInstance(outcome[0], bytes)
        self.absent(tree)
        self.assertEqual(self.bridge.status()["last_id"], item.identifier)
        with wave.open(io.BytesIO(outcome[0])) as wav:
            self.assertEqual((wav.getnchannels(), wav.getframerate(), wav.getsampwidth()), (1, 16000, 2))
            self.assertEqual(wav.readframes(1), b"\x21\x00")

    def test_cancel_disconnect_lock_deadline_shutdown_discard_and_reap(self):
        for mode in ("cancel", "disconnect", "lock", "deadline", "shutdown"):
            with self.subTest(mode=mode):
                self.bridge._closing = False
                self.locked.clear()
                (self.root / "tree.json").unlink(missing_ok=True)
                item, gone, thread, outcome = self.run_record(deadline=0.5 if mode == "deadline" else None)
                tree = self.tree()
                if mode == "cancel":
                    self.bridge.control(item.identifier, cancel=True)
                elif mode == "disconnect":
                    gone.set()
                elif mode == "lock":
                    self.locked.set()
                elif mode == "deadline":
                    pass  # Original actual CaptureControl deadline ends capture.
                else:
                    self.bridge.shutdown()
                thread.join(4)
                self.assertFalse(thread.is_alive())
                self.assertIsInstance(outcome[0], str)
                self.assertEqual(self.bridge.status()["phase"], "inactive")
                self.absent(tree)

    def test_natural_limit_returns_only_after_proven_reap(self):
        _, _, thread, outcome = self.run_record(seconds=1)
        tree = self.tree()
        thread.join(4)
        self.absent(tree)
        self.assertIsInstance(outcome[0], bytes)
        self.assertLessEqual(len(outcome[0]), 32044)

    def test_unknown_lock_and_invalid_input_never_launch(self):
        self.bridge._locked = lambda: None
        with self.assertRaises(CaptureRefusal):
            self.bridge.record(self.bridge.claim(uuid.uuid4().hex, 1, threading.Event()))
        for value in (True, 0, 31, 10**1000, 1.2):
            with self.assertRaises(CaptureRefusal):
                self.bridge.claim(uuid.uuid4().hex, value, threading.Event())
        self.assertFalse((self.root / "tree.json").exists())
        self.assertFalse((self.root / "leases").exists())

    def test_stale_control_cannot_cancel_successor(self):
        first, _, thread, _ = self.run_record()
        self.bridge.control(first.identifier, cancel=True)
        thread.join(4)
        (self.root / "tree.json").unlink()
        second, _, thread, _ = self.run_record()
        with self.assertRaisesRegex(CaptureRefusal, "RECORDING_NOT_ACTIVE"):
            self.bridge.control(first.identifier, cancel=True)
        self.assertFalse(second.cancel.is_set())
        self.bridge.control(second.identifier, cancel=True)
        thread.join(4)

    def test_unproven_final_stop_discards_and_sticks_unavailable(self):
        original = audio.MicCapture.stop
        def uncertain(capture):
            original(capture)  # Real owned teardown; adapter receives no proof.
            return False
        with patch.object(audio.MicCapture, "stop", uncertain):
            item, _, thread, outcome = self.run_record()
            tree = self.tree()
            wait_for(lambda: item.duration > 0)
            self.bridge.control(item.identifier, cancel=False)
            thread.join(4)
        self.absent(tree)
        self.assertEqual(outcome, ["MICROPHONE_UNAVAILABLE"])
        self.assertEqual(self.bridge.status()["phase"], "unavailable")
        with self.assertRaisesRegex(CaptureRefusal, "MICROPHONE_UNAVAILABLE"):
            self.bridge.claim(uuid.uuid4().hex, 1, threading.Event())

    def test_reader_allocation_failure_reaps_and_closes_diagnostics(self):
        original = threading.Thread
        def allocate(*args, **kwargs):
            if kwargs.get("name") == "kilix-voice-mic":
                raise RuntimeError("private diagnostic must not leave backend")
            return original(*args, **kwargs)
        with patch.object(threading, "Thread", allocate):
            item = self.bridge.claim(uuid.uuid4().hex, 1, threading.Event())
            with self.assertRaisesRegex(CaptureRefusal, "^RECORDING_REFUSED$"):
                self.bridge.record(item)
        self.assertEqual(self.bridge.status()["phase"], "inactive")

    def test_actual_supervisor_loss_never_releases_quarantined_device(self):
        result = subprocess.run([sys.executable, '-B', PEER, 'loss', str(self.root)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(json.loads(result.stdout).values()))

    def test_executor_refusal_has_no_owner_or_stranded_claim(self):
        from kilix_voicebox_backend import main
        class Request:
            async def is_disconnected(self):
                return False
        from fastapi import HTTPException
        with patch.object(main, '_capture_bridge', self.bridge), patch(
                'kilix_voicebox_backend.managed_capture.asyncio.to_thread', side_effect=RuntimeError('fixture')):
            with self.assertRaises(HTTPException):
                asyncio.run(main.recording_start(main.RecordingInput(id='1' * 32, seconds=1), Request()))
        self.assertEqual(self.bridge.status()['phase'], 'inactive')
        self.assertFalse((self.root / 'tree.json').exists())

    def start_provider(self, mode):
        private = self.root / uuid.uuid4().hex
        private.mkdir(mode=0o700)
        process = subprocess.Popen([sys.executable, "-B", PEER, "provider", str(private), mode],
                                   stdin=subprocess.DEVNULL)
        self.processes.append(process)
        wait_for(lambda: (private / "ready").exists() or process.poll() is not None)
        self.assertIsNone(process.poll())
        return private, process

    def test_final_recorded_input_to_real_client_after_recorder_absent(self):
        item, _, thread, outcome = self.run_record()
        tree = self.tree()
        wait_for(lambda: item.duration > 0)
        self.bridge.control(item.identifier, cancel=False)
        thread.join(4)
        self.absent(tree)
        path = self.root / "input.wav"
        path.write_bytes(outcome[0])
        private, process = self.start_provider("success")
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(private)}):
            result = transcribe_owned(path, "en", threading.Event(), deadline=time.monotonic() + 3,
                                      locked=lambda: False)
        self.assertEqual(result["text"], "Synthetic speech.\n")
        self.assertEqual(process.wait(3), 0)
        self.assertEqual(json.loads((private / "received.json").read_text())["bytes"], len(outcome[0]))

    def test_real_client_disconnect_cancels_only_owned_job(self):
        from voicelib.util import write_wav
        path = self.root / "input.wav"
        path.write_bytes(write_wav(b"\x21\x00" * 1600, 16000))
        private, process = self.start_provider("cancel")
        gone = threading.Event()
        outcome = []
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(private)}):
            def request():
                try:
                    outcome.append(transcribe_owned(path, "en", gone, deadline=time.monotonic() + 4,
                                                    locked=lambda: False))
                except CaptureRefusal as error:
                    outcome.append(error.code)
            thread = threading.Thread(target=request)
            thread.start()
            self.threads.append(thread)
            wait_for(lambda: (private / "received.json").exists())
            gone.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome, ["TRANSCRIPTION_REFUSED"])
        self.assertEqual(process.wait(4), 0)
        self.assertTrue((private / "cancelled").exists())
        self.assertTrue((private / "disconnected").exists())

    def test_transcribe_unknown_lock_expiry_or_disconnect_never_connects(self):
        for mode in ("lock", "deadline", "disconnect"):
            gone = threading.Event()
            if mode == "disconnect":
                gone.set()
            with self.assertRaisesRegex(CaptureRefusal, "TRANSCRIPTION_STOPPED"):
                transcribe_owned(self.root / "absent", "en", gone,
                    deadline=time.monotonic() + (-1 if mode == "deadline" else 2),
                    locked=lambda: None if mode == "lock" else False)

    def test_http_cancellation_retains_operation_until_its_teardown(self):
        gone = threading.Event()
        completed = threading.Event()
        class Request:
            async def is_disconnected(self):
                return False
        def operation():
            gone.wait(2)
            time.sleep(0.1)
            completed.set()
        async def run():
            task = asyncio.create_task(until_disconnected(Request(), operation, gone))
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(completed.is_set())
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

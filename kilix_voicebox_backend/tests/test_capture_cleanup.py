"""Recoverable cleanup allocation failure must not substitute for callable return."""
import asyncio
import contextvars
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from kilix_voicebox_backend import main, managed_capture as capture
from .test_capture_queue import PEER, Request, reached
from .voice_optional import VOICE_MICROPHONE_AVAILABLE, audio


@unittest.skipUnless(VOICE_MICROPHONE_AVAILABLE, "managed voice microphone API is not installed")
class CaptureCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vbc-")
        self.root = Path(self.temp.name)
        self.bridge = capture.CaptureBridge(cfg={"audio": {"capture_cmd": [
            sys.executable, "-I", "-B", PEER, "recorder", str(self.root)]}},
            locked=lambda: False, namespace=str(self.root / "lease"))
        self.fds = len(os.listdir("/proc/self/fd"))
        self.threads = {t.ident for t in threading.enumerate()}

    def tearDown(self):
        self.bridge.shutdown()
        tree = self.root / "tree.json"
        if tree.exists():
            self.assertTrue(all(not Path(f"/proc/{pid}").exists()
                                for pid in json.loads(tree.read_text()).values()))
        self.assertEqual(len(os.listdir("/proc/self/fd")), self.fds)
        self.assertEqual({t.ident for t in threading.enumerate()}, self.threads)
        self.temp.cleanup()

    async def handler(self):
        return await main.recording_start(main.RecordingInput(id="c" * 32, seconds=1), Request())

    def test_actual_entered_capture_retries_cleanup_allocation(self):
        async def run():
            entered, release = threading.Event(), threading.Event()
            original_read = audio.MicCapture.read
            loop = asyncio.get_running_loop()
            original_future = loop.create_future
            faults = []
            task = None

            def read(owner, *args, **kwargs):
                entered.set()
                assert release.wait(5), "callable barrier backstop"
                return original_read(owner, *args, **kwargs)

            def allocate():
                if asyncio.current_task() is task and not faults:
                    faults.append(True)
                    raise MemoryError("one-shot cleanup future allocation")
                return original_future()

            with patch.object(audio.MicCapture, "read", read):
                try:
                    task = asyncio.create_task(self.handler())
                    await reached(entered.is_set)
                    await reached(lambda: (self.root / "tree.json").exists())
                    tree = json.loads((self.root / "tree.json").read_text())
                    self.assertTrue(all(Path(f"/proc/{pid}").exists() for pid in tree.values()))
                    item = self.bridge._active
                    deadline = item.deadline
                    with patch.object(loop, "create_future", allocate):
                        task.cancel()
                        await asyncio.sleep(.15)
                        self.assertEqual(faults, [True])
                        self.assertFalse(task.done())
                        self.assertIs(self.bridge._active, item)
                        self.assertFalse(item.completed)
                        # Actual Voice may already have reaped the tree; it has
                        # not released this callable's explicit barrier.
                        for _ in range(2):
                            task.cancel()
                            await asyncio.sleep(.02)
                            self.assertFalse(task.done())
                            self.assertIs(self.bridge._active, item)
                        self.assertEqual(item.deadline, deadline)
                finally:
                    release.set()
                    if task is not None:
                        result, = await asyncio.gather(task, return_exceptions=True)
                self.assertIsInstance(result, asyncio.CancelledError)
                self.assertTrue(item.completed)
                self.assertEqual(self.bridge.status()["phase"], "inactive")
        with patch.object(main, "_capture_bridge", self.bridge):
            asyncio.run(run())

    def test_common_waiter_retries_cleanup_allocation_and_repeated_cancel(self):
        variable = contextvars.ContextVar("cleanup-context")
        async def run():
            variable.set("same request context")
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            gone = threading.Event()
            descriptors, values, faults = [], [], []
            loop = asyncio.get_running_loop()
            original_future = loop.create_future
            task = None

            def work():
                fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                descriptors.append(fd)
                try:
                    values.append(variable.get())
                    entered.set()
                    assert release.wait(5), "common waiter barrier backstop"
                    return variable.get()
                finally:
                    os.close(fd)
                    finished.set()

            def allocate():
                if asyncio.current_task() is task and not faults:
                    faults.append(True)
                    raise MemoryError("one-shot common cleanup allocation")
                return original_future()

            try:
                task = asyncio.create_task(capture.until_disconnected(Request(), work, gone))
                await reached(entered.is_set)
                with patch.object(loop, "create_future", allocate):
                    task.cancel()
                    await asyncio.sleep(.15)
                    self.assertEqual(faults, [True])
                    self.assertFalse(task.done())
                    self.assertFalse(finished.is_set())
                    os.fstat(descriptors[0])
                    for _ in range(2):
                        task.cancel()
                        await asyncio.sleep(.02)
                        self.assertFalse(task.done())
                    self.assertEqual(values, ["same request context"])
                    self.assertTrue(gone.is_set())
            finally:
                release.set()
                if task is not None:
                    result, = await asyncio.gather(task, return_exceptions=True)
            self.assertIsInstance(result, asyncio.CancelledError)
            self.assertTrue(finished.is_set())
            with self.assertRaises(OSError):
                os.fstat(descriptors[0])
        asyncio.run(run())

    def test_common_success_retains_context_without_marking_gone(self):
        variable = contextvars.ContextVar("success-context")
        async def run():
            variable.set("successful request")
            gone = threading.Event()
            result = await capture.until_disconnected(Request(), lambda: variable.get(), gone)
            self.assertEqual(result, "successful request")
            self.assertFalse(gone.is_set())
        asyncio.run(run())

    def test_actual_normal_success_stop_cancel_deadline_without_fault(self):
        async def run(mode):
            previous = self.root / "tree.json"
            if previous.exists():
                self.assertTrue(all(not Path(f"/proc/{pid}").exists()
                                    for pid in json.loads(previous.read_text()).values()))
                previous.unlink()
            entered, release = threading.Event(), threading.Event()
            original_read = audio.MicCapture.read
            task = None
            def read(owner, *args, **kwargs):
                frame = original_read(owner, *args, **kwargs)
                if frame is not None and not entered.is_set():
                    entered.set()
                    assert release.wait(6), "normal control barrier backstop"
                return frame
            with patch.object(audio.MicCapture, "read", read):
                try:
                    task = asyncio.create_task(self.handler())
                    await reached(entered.is_set)
                    item = self.bridge._active
                    deadline = item.deadline
                    if mode == "deadline":
                        await asyncio.sleep(max(0, deadline - time.monotonic()) + .02)
                    elif mode in ("stop", "cancel"):
                        self.bridge.control(item.identifier, cancel=mode == "cancel")
                    await asyncio.sleep(.02)
                    self.assertFalse(task.done())
                    self.assertIs(self.bridge._active, item)
                    self.assertFalse(item.completed)
                    self.assertEqual(item.deadline, deadline)
                finally:
                    release.set()
                    if task is not None:
                        result, = await asyncio.gather(task, return_exceptions=True)
                if mode in ("cancel", "deadline"):
                    self.assertIsInstance(result, HTTPException)
                    self.assertEqual(result.status_code, 409)
                else:
                    self.assertFalse(isinstance(result, BaseException), repr(result))
                    self.assertTrue(result.body.startswith(b"RIFF"))
                    self.assertFalse(item.gone.is_set())
                self.assertTrue(item.completed)
                self.assertEqual(self.bridge.status()["phase"], "inactive")
        for mode in ("success", "stop", "cancel", "deadline"):
            with self.subTest(mode=mode), patch.object(main, "_capture_bridge", self.bridge):
                asyncio.run(run(mode))


if __name__ == "__main__":
    unittest.main()

"""Real default-pool admission and independent entered-operation cleanup."""
import asyncio
import concurrent.futures
import contextvars
import inspect
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
from .voice_optional import VOICE_MICROPHONE_AVAILABLE, capture_class

PEER = str(Path(__file__).with_name("capture_peer.py"))


class Request:
    def __init__(self):
        self.gone = False

    async def is_disconnected(self):
        return self.gone


async def reached(predicate, seconds=2):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "fixture entry barrier timed out"
        await asyncio.sleep(.005)


@unittest.skipUnless(VOICE_MICROPHONE_AVAILABLE, "managed voice microphone API is not installed")
class CaptureQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vbq-")
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

    async def handler(self, request):
        return await main.recording_start(main.RecordingInput(id="a" * 32, seconds=1), request)

    async def occupy(self):
        loop = asyncio.get_running_loop()
        await asyncio.to_thread(lambda: None)
        count = loop._default_executor._max_workers
        entered, release = [], threading.Event()
        def hold():
            entered.append(threading.get_ident())
            assert release.wait(8), "fixture occupancy backstop"
        futures = [loop.run_in_executor(None, hold) for _ in range(count)]
        await reached(lambda: len(entered) == count)
        return release, futures

    def test_original_pool_queued_controls_abandon_before_release(self):
        async def run(mode):
            request = Request()
            release, occupants = await self.occupy()
            task = None
            try:
                task = asyncio.create_task(self.handler(request))
                await reached(lambda: self.bridge._active is not None)
                item = self.bridge._active
                self.assertFalse(item.entered)
                if mode == "cancel":
                    self.bridge.control(item.identifier, cancel=True)
                elif mode == "stop":
                    self.bridge.control(item.identifier, cancel=False)
                elif mode == "disconnect":
                    request.gone = True
                elif mode == "shutdown":
                    self.bridge.shutdown()
                elif mode == "task":
                    task.cancel()
                limit = max(0, item.deadline - time.monotonic()) + .25 if mode == "deadline" else .25
                await reached(task.done, limit)
                self.assertEqual(self.bridge.status()["phase"], "inactive")
                self.assertFalse(release.is_set())
                with self.assertRaises(asyncio.CancelledError if mode == "task" else HTTPException):
                    await task
                self.assertFalse((self.root / "tree.json").exists())
            finally:
                release.set()
                await asyncio.gather(*occupants)
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
            self.assertFalse((self.root / "tree.json").exists())
        for mode in ("deadline", "cancel", "stop", "disconnect", "task", "shutdown"):
            with self.subTest(mode=mode), patch.object(main, "_capture_bridge", self.bridge):
                asyncio.run(run(mode))

    def test_abandoned_callable_cannot_touch_same_id_successor(self):
        old = self.bridge.claim("b" * 32, 1, threading.Event())
        operation = capture._QueuedOperation(lambda: self.bridge.record(old))
        self.assertTrue(operation.abandon())
        self.bridge.abandon_unstarted(old)
        successor = self.bridge.claim(old.identifier, 1, threading.Event())
        with self.assertRaises(capture.CaptureRefusal):
            operation()
        self.bridge.finalize(old)
        self.assertIs(self.bridge._active, successor)
        self.assertFalse(successor.cancel.is_set())
        self.assertFalse((self.root / "tree.json").exists())
        self.assertTrue(self.bridge.record(successor).startswith(b"RIFF"))
        with self.assertRaises(capture.CaptureRefusal):
            operation()
        self.assertEqual(self.bridge.status()["phase"], "inactive")

    def test_atomic_entry_races_and_single_execution(self):
        for _ in range(40):
            start = threading.Barrier(2)
            values, refused = [], []
            operation = capture._QueuedOperation(lambda: values.append(True))
            def enter():
                start.wait()
                try:
                    operation()
                except capture.CaptureRefusal:
                    refused.append(True)
            thread = threading.Thread(target=enter)
            thread.start()
            start.wait()
            abandoned = operation.abandon()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual((len(values), len(refused)), (0, 1) if abandoned else (1, 0))
            with self.assertRaises(capture.CaptureRefusal):
                operation()
            self.assertTrue(operation.settled())

    def test_entering_and_real_running_owners_survive_waiter_cancellation(self):
        async def run(boundary, mode):
            previous = self.root / "tree.json"
            if previous.exists():
                self.assertTrue(all(not Path(f"/proc/{pid}").exists()
                                    for pid in json.loads(previous.read_text()).values()))
                previous.unlink()
            entered, release = threading.Event(), threading.Event()
            record, read = self.bridge.record, capture_class.read
            def barrier():
                entered.set()
                assert release.wait(6), "owned entry barrier backstop"
            def at_entry(*args, **kwargs):
                if boundary == "entering":
                    barrier()
                return record(*args, **kwargs)
            def at_read(owner, *args, **kwargs):
                if boundary == "running" and not entered.is_set():
                    # Actual MicCapture.start has allocated the supervised tree.
                    barrier()
                return read(owner, *args, **kwargs)
            task = None
            with patch.object(self.bridge, "record", at_entry), patch.object(capture_class, "read", at_read):
                try:
                    task = asyncio.create_task(self.handler(Request()))
                    await reached(entered.is_set)
                    item = self.bridge._active
                    if boundary == "running":
                        await reached(lambda: (self.root / "tree.json").exists())
                        tree = json.loads((self.root / "tree.json").read_text())
                        self.assertTrue(all(Path(f"/proc/{pid}").exists() for pid in tree.values()))
                    if mode == "deadline":
                        await asyncio.sleep(max(0, item.deadline - time.monotonic()) + .02)
                    elif mode == "task":
                        task.cancel()
                        await asyncio.sleep(.02)
                        task.cancel()  # Interruption while already waiting for cleanup.
                    else:
                        self.bridge.control(item.identifier, cancel=True)
                    await asyncio.sleep(.08)
                    self.assertFalse(task.done())
                    self.assertIs(self.bridge._active, item)
                finally:
                    release.set()
                    if task is not None:
                        with self.assertRaises(asyncio.CancelledError if mode == "task" else HTTPException):
                            await asyncio.wait_for(task, 3)
            self.assertEqual(self.bridge.status()["phase"], "inactive")
        for boundary in ("entering", "running"):
            for mode in ("cancel", "task", "deadline"):
                with self.subTest(boundary=boundary, mode=mode), patch.object(main, "_capture_bridge", self.bridge):
                    asyncio.run(run(boundary, mode))

    def test_executor_submission_errors_after_enqueue_or_entry_keep_gate_proof(self):
        async def run(mode):
            await asyncio.to_thread(lambda: None)
            occupied = []
            release_pool = threading.Event()
            if mode == "queued":
                release_pool, occupied = await self.occupy()
            entered, release = threading.Event(), threading.Event()
            submit = concurrent.futures.ThreadPoolExecutor.submit
            original_record = self.bridge.record
            def record(*args, **kwargs):
                entered.set()
                assert release.wait(4)
                return original_record(*args, **kwargs)
            def fault(pool, *args, **kwargs):
                if mode != "before":
                    submit(pool, *args, **kwargs)
                if mode == "entered":
                    assert entered.wait(1)
                raise MemoryError("fixture executor failure")
            task = None
            try:
                with patch.object(concurrent.futures.ThreadPoolExecutor, "submit", fault), patch.object(self.bridge, "record", record):
                    task = asyncio.create_task(self.handler(Request()))
                    if mode == "entered":
                        await reached(entered.is_set)
                        await asyncio.sleep(.08)
                        self.assertFalse(task.done())
                        self.assertIsNotNone(self.bridge._active)
                        release.set()
                    with self.assertRaises(HTTPException) as found:
                        await asyncio.wait_for(task, .5)
                    self.assertEqual(found.exception.status_code, 503)
                    self.assertEqual(self.bridge.status()["phase"], "inactive")
            finally:
                release.set()
                release_pool.set()
                await asyncio.gather(*occupied)
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
            self.assertFalse((self.root / "tree.json").exists())
        for mode in ("before", "queued", "entered"):
            with self.subTest(mode=mode), patch.object(main, "_capture_bridge", self.bridge):
                asyncio.run(run(mode))

    def test_task_allocation_failure_closes_unscheduled_coroutine(self):
        original = asyncio.to_thread
        operations = []
        def observed(*args, **kwargs):
            operation = original(*args, **kwargs)
            operations.append(operation)
            return operation
        async def run():
            with patch.object(asyncio, "to_thread", observed), patch.object(asyncio, "create_task", side_effect=MemoryError):
                with self.assertRaises(HTTPException) as found:
                    await self.handler(Request())
                self.assertEqual(found.exception.status_code, 503)
            self.assertEqual(self.bridge.status()["phase"], "inactive")
        with patch.object(main, "_capture_bridge", self.bridge):
            asyncio.run(run())
        self.assertEqual(len(operations), 1)
        self.assertEqual(inspect.getcoroutinestate(operations[0]), inspect.CORO_CLOSED)
        self.assertFalse((self.root / "tree.json").exists())

    def test_common_waiter_preserves_context_and_live_descriptor_cleanup(self):
        variable = contextvars.ContextVar("fixture")
        async def run():
            variable.set("original context")
            gone = threading.Event()
            entered, release = threading.Event(), threading.Event()
            def work():
                fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                try:
                    entered.set()
                    assert release.wait(3)
                    return variable.get()
                finally:
                    os.close(fd)
            task = asyncio.create_task(capture.until_disconnected(Request(), work, gone))
            try:
                await reached(entered.is_set)
                task.cancel()
                await asyncio.sleep(.05)
                self.assertFalse(task.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertTrue(gone.is_set())
            result = await capture.until_disconnected(Request(), lambda: variable.get(), threading.Event())
            self.assertEqual(result, "original context")
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

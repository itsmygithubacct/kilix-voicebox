"""Separate synthetic processes for the managed recording contract only."""
import hashlib
import ctypes
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time


def recorder(root):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    escaped = subprocess.Popen([sys.executable, "-I", "-c",
        "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(100)"],
        start_new_session=True, close_fds=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (root / "tree.json").write_text(json.dumps({"pid": os.getpid(), "escaped": escaped.pid,
                                              "supervisor": os.getppid()}))
    while True:
        os.write(1, b"\x21\x00" * 320)
        time.sleep(0.02)


def provider(root, mode):
    from kilix_transcribe.protocol import receive_packet, send_packet
    from kilix_transcribe.surface import RESULT_SCHEMA, Transcript, SegmentUpdate, render_transcript
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
        listener.bind(str(root / "kilix-transcribe.sock"))
        os.chmod(root / "kilix-transcribe.sock", 0o600)
        listener.listen(2)
        (root / "ready").touch()
        channel, _ = listener.accept()
        channel.settimeout(5)
        with channel:
            value, descriptors = receive_packet(channel)
            assert len(descriptors) == 1
            descriptor = descriptors[0]
            try:
                payload = os.pread(descriptor, 1048576, 0)
                assert hashlib.sha256(payload).hexdigest() == value["args"]["audio"]["sha256"]
                (root / "received.json").write_text(json.dumps({"job": value["job_id"],
                    "bytes": len(payload), "digest": hashlib.sha256(payload).hexdigest()}))
            finally:
                os.close(descriptor)
            base = {"schema": value["schema"], "request_id": value["request_id"], "job_id": value["job_id"]}
            send_packet(channel, {**base, "type": "accepted", "result": {}})
            if mode != "success":
                listener.settimeout(3)
                try:
                    control, _ = listener.accept()
                except TimeoutError:
                    control = None
                if control is not None:
                    with control:
                        cancel, extra = receive_packet(control)
                        assert not extra and cancel["op"] == "cancel" and cancel["job_id"] == value["job_id"]
                        send_packet(control, {"schema": cancel["schema"], "request_id": cancel["request_id"],
                            "job_id": cancel["job_id"], "type": "canceled", "result": {"cancelled": True}})
                        (root / "cancelled").touch()
                try:
                    assert not channel.recv(1)
                except ConnectionResetError:
                    pass
                (root / "disconnected").touch()
                return
            segment = SegmentUpdate(1, 1, 0, 100, "Synthetic speech.", True, ())
            transcript = Transcript("transcribe", "whisper.cpp", "whisper-tiny", "en", (segment,))
            result = {"schema": RESULT_SCHEMA, "task": "transcribe", "engine": {"id": "whisper.cpp"},
                "model": {"id": "whisper-tiny"}, "language": "en",
                "engine_revision": "fixture", "model_revision": "fixture", "duration_ms": 100,
                "text": render_transcript(transcript, "text"),
                "segments": json.loads(render_transcript(transcript, "json"))["segments"],
                "output_format": "json", "output": render_transcript(transcript, "json")}
            payload = json.dumps(result).encode()
            with tempfile.TemporaryFile() as temporary:
                temporary.write(payload)
                temporary.flush()
                fd = os.open(f"/proc/self/fd/{temporary.fileno()}", os.O_RDONLY | os.O_CLOEXEC)
                try:
                    metadata = {"transcript": {"fd": 0, "byte_length": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest()}, "engine_id": "whisper.cpp",
                        "model_id": "whisper-tiny", "engine_revision": "fixture", "model_revision": "fixture",
                        "duration_ms": 100}
                    send_packet(channel, {**base, "type": "result", "result": metadata}, fd)
                finally:
                    os.close(fd)


def server(root, port):
    from kilix_voicebox_backend import main, config
    from kilix_voicebox_backend.managed_capture import CaptureBridge, transcribe_owned
    import uvicorn
    config.set_data_dir(str(root / "data"))
    main._capture_bridge = CaptureBridge(cfg={"audio": {"capture_cmd": [sys.executable,
        "-I", str(Path(__file__).resolve()), "recorder", str(root)]}},
        locked=lambda: (root / "locked").exists(), namespace=str(root / "leases"))
    main.transcribe_owned = lambda *args, **kwargs: transcribe_owned(
        *args, **kwargs, locked=lambda: (root / "locked").exists())
    uvicorn.run(main.app, host="127.0.0.1", port=int(port), access_log=False, log_level="error")


def loss_observer(root):
    from kilix_voicebox_backend.managed_capture import CaptureBridge, CaptureRefusal
    assert ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0
    unrelated = subprocess.Popen(['/bin/sleep', '30'])
    bridge = CaptureBridge(cfg={'audio': {'capture_cmd': [sys.executable, '-I', __file__,
        'recorder', str(root)]}}, locked=lambda: False, namespace=str(root / 'leases'))
    item = bridge.claim('1' * 32, 10, threading.Event())
    outcomes = []
    def run():
        try:
            outcomes.append(bridge.record(item))
        except CaptureRefusal as error:
            outcomes.append(error.code)
    thread = threading.Thread(target=run)
    owned = []
    try:
        thread.start()
        deadline = time.monotonic() + 4
        while not (root / 'tree.json').exists():
            assert time.monotonic() < deadline
            time.sleep(.01)
        tree = json.loads((root / 'tree.json').read_text())
        owned = [tree['pid'], tree['escaped']]
        os.kill(tree['supervisor'], signal.SIGKILL)
        thread.join(4)
        assert not thread.is_alive() and outcomes == ['MICROPHONE_UNAVAILABLE'], {
            'thread_alive': thread.is_alive(), 'outcomes': outcomes, 'status': bridge.status()}
        assert bridge.status()['phase'] == 'unavailable'
        # The escaped child has no inherited guard. Only explicit observer
        # cleanup removes these survivors; the adapter must retain uncertainty.
        assert all(Path(f'/proc/{pid}').exists() for pid in owned)
        for pid in owned:
            os.kill(pid, signal.SIGKILL)
        for pid in owned:
            os.waitpid(pid, 0)
        other = CaptureBridge(cfg=bridge._cfg, locked=lambda: False,
                              namespace=str(root / 'leases'))
        try:
            other.record(other.claim('2' * 32, 1, threading.Event()))
            raise AssertionError('persistent quarantine was lost')
        except CaptureRefusal:
            pass
        assert unrelated.poll() is None
        print(json.dumps({'unavailable': True, 'quarantine_after_all_guards_gone': True,
            'unrelated_preserved': True, 'owned_absent': all(not Path(f'/proc/{pid}').exists() for pid in owned)}))
    finally:
        bridge.shutdown()
        thread.join(4)
        for pid in owned:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
        unrelated.terminate()
        unrelated.wait(3)


if __name__ == "__main__":
    mode, root = sys.argv[1], Path(sys.argv[2])
    if mode == "recorder":
        recorder(root)
    elif mode == "provider":
        provider(root, sys.argv[3])
    elif mode == "server":
        server(root, sys.argv[3])
    elif mode == "loss":
        loss_observer(root)

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import wave
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kilix_voicebox_backend import export_import, models, profiles, stories
from kilix_voicebox_backend.database import (
    Base,
    Generation as DBGeneration,
    ProfileSample as DBProfileSample,
    Story as DBStory,
    StoryItem as DBStoryItem,
)
from kilix_voicebox_backend.database import VoiceProfile as DBVoiceProfile
from kilix_voicebox_backend.main import (
    _owned_streaming_response,
    _read_bounded_upload,
    app,
    configure_session_token,
    generate_speech,
)
from kilix_voicebox_backend.kilix_provider import (
    ProviderClient,
    ProviderError,
    QWEN_ENDPOINT,
    QWEN_SCHEMA,
    TRANSCRIBE_ENDPOINT,
    decode_message,
    encode_message,
    receive_message,
    send_message,
)
from kilix_voicebox_backend.orchestrator import VoiceboxOrchestrator
from kilix_voicebox_backend.private_store import (
    GenerationProvenance,
    PrivateVoiceStore,
    StoreError,
)
from kilix_voicebox_backend.utils.audio import MAX_SOURCE_SAMPLES, load_audio, validate_reference_audio


def request(schema: str) -> dict:
    return {
        "schema": schema,
        "type": "request",
        "request_id": "request-1",
        "op": "status",
        "deadline_ms": 1_000,
        "args": {},
    }


def wav_bytes(frames: int = 72_000, sample_rate: int = 24_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(b"\x01\x00" * frames)
    return output.getvalue()


TEST_SESSION_TOKEN = "1" * 64
configure_session_token(TEST_SESSION_TOKEN)


class AudioBoundaryTests(unittest.TestCase):
    def test_audio_is_bounded_before_decode(self) -> None:
        info = Mock(samplerate=24_000, channels=1, frames=MAX_SOURCE_SAMPLES + 1)
        with (
            patch("kilix_voicebox_backend.utils.audio.sf.info", return_value=info),
            patch("kilix_voicebox_backend.utils.audio.sf.read") as read,
            self.assertRaisesRegex(ValueError, "^AUDIO_DECODE_BOUND$"),
        ):
            load_audio("oversized.wav")
        read.assert_not_called()

    def test_resampling_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            source.write_bytes(wav_bytes(frames=480, sample_rate=48_000))
            first, first_rate = load_audio(source, max_duration_seconds=1.0)
            second, second_rate = load_audio(source, max_duration_seconds=1.0)
        self.assertEqual((first_rate, second_rate), (24_000, 24_000))
        self.assertEqual((len(first), len(second)), (240, 240))
        self.assertEqual(first.tobytes(), second.tobytes())

    def test_decoder_failures_are_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "invalid.wav"
            source.write_bytes(b"private/path/not-audio")
            valid, error = validate_reference_audio(str(source))
        self.assertFalse(valid)
        self.assertEqual(error, "AUDIO_DECODE_FAILED")


class BrowserBoundaryTests(unittest.TestCase):
    def test_upload_reader_is_bounded_before_commit(self) -> None:
        for payload, code in ((b"12345", "INPUT_SIZE"), (b"", "INPUT_EMPTY")):
            upload = UploadFile(file=io.BytesIO(payload), filename="input.bin")
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(_read_bounded_upload(upload, 4))
            self.assertEqual(caught.exception.detail, {"code": code})

    def test_product_origins_are_exact(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("kilix_voicebox_backend.main.config.get_data_dir", return_value=Path(directory)),
            patch("kilix_voicebox_backend.main.config.get_privacy_dir", return_value=Path(directory) / "privacy"),
            patch("kilix_voicebox_backend.main.database.init_db"),
        ):
            with TestClient(app) as client:
                allowed = client.get("/", headers={
                    "Origin": "tauri://localhost",
                    "X-Kilix-Voicebox-Session": TEST_SESSION_TOKEN,
                })
                refused = client.get("/", headers={
                    "Origin": "https://attacker.invalid",
                    "X-Kilix-Voicebox-Session": TEST_SESSION_TOKEN,
                })
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json(), {"detail": {"code": "ORIGIN_FORBIDDEN"}})

    def test_remote_shutdown_route_is_absent(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("kilix_voicebox_backend.main.config.get_data_dir", return_value=Path(directory)),
            patch("kilix_voicebox_backend.main.config.get_privacy_dir", return_value=Path(directory) / "privacy"),
            patch("kilix_voicebox_backend.main.database.init_db"),
        ):
            with TestClient(app) as client:
                response = client.post("/shutdown", headers={
                    "Origin": "tauri://localhost",
                    "X-Kilix-Voicebox-Session": TEST_SESSION_TOKEN,
                })
        self.assertEqual(response.status_code, 404)

    def test_session_credential_is_required(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("kilix_voicebox_backend.main.config.get_data_dir", return_value=Path(directory)),
            patch("kilix_voicebox_backend.main.config.get_privacy_dir", return_value=Path(directory) / "privacy"),
            patch("kilix_voicebox_backend.main.database.init_db"),
        ):
            with TestClient(app) as client:
                response = client.get("/", headers={"Origin": "tauri://localhost"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": {"code": "SESSION_UNAUTHORIZED"}})

    def test_owned_stream_refuses_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.wav"
            target.write_bytes(wav_bytes(240))
            link = root / "link.wav"
            link.symlink_to(target)
            with self.assertRaises(HTTPException) as caught:
                _owned_streaming_response(
                    link,
                    maximum=len(target.read_bytes()) + 1,
                    media_type="audio/wav",
                )
            self.assertEqual(caught.exception.detail, {"code": "DATA_INTEGRITY"})


class CodecTests(unittest.TestCase):
    def test_provider_error_codes_are_ascii(self) -> None:
        self.assertEqual(ProviderError("VALID_CODE_2").code, "VALID_CODE_2")
        self.assertEqual(ProviderError("INVALID_CODE_٢").code, "PROVIDER_ERROR")

    def test_message_round_trip_is_canonical(self) -> None:
        packet = encode_message(request(QWEN_SCHEMA))
        value = decode_message(packet)
        self.assertEqual(value, request(QWEN_SCHEMA))
        self.assertEqual(encode_message(value), packet)

    def test_extreme_depth_and_integer_refuse_stably(self) -> None:
        for payload in (
            b"[" * 10_000 + b"true" + b"]" * 10_000,
            b'{"n":' + b"9" * 50_000 + b"}",
        ):
            packet = len(payload).to_bytes(4, "big") + payload
            with self.assertRaises(ProviderError) as caught:
                decode_message(packet)
            self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")


@unittest.skipUnless(hasattr(socket, "SOCK_SEQPACKET"), "SOCK_SEQPACKET unavailable")
class ClientTests(unittest.TestCase):
    def test_fixed_private_endpoint_and_peer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            os.chmod(runtime, 0o700)
            endpoint = runtime / QWEN_ENDPOINT.socket_name
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            listener.bind(str(endpoint))
            os.chmod(endpoint, 0o600)
            listener.listen(1)

            def serve() -> None:
                channel, _address = listener.accept()
                try:
                    value, descriptors = receive_message(channel)
                    self.assertEqual(descriptors, ())
                    send_message(channel, {
                        "schema": QWEN_SCHEMA,
                        "type": "status",
                        "request_id": value["request_id"],
                        "result": {"provider_state": "RUNTIME_UNSELECTED"},
                    })
                finally:
                    channel.close()

            worker = threading.Thread(target=serve)
            worker.start()
            try:
                client = ProviderClient(QWEN_ENDPOINT, runtime, timeout=2)
                with client.request(request(QWEN_SCHEMA)) as reply:
                    self.assertEqual(reply.payload["result"]["provider_state"],
                                     "RUNTIME_UNSELECTED")
            finally:
                worker.join(timeout=2)
                listener.close()
            self.assertFalse(worker.is_alive())

    def test_untyped_provider_event_is_stably_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            os.chmod(runtime, 0o700)
            endpoint = runtime / QWEN_ENDPOINT.socket_name
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            listener.bind(str(endpoint))
            os.chmod(endpoint, 0o600)
            listener.listen(1)

            def serve() -> None:
                channel, _address = listener.accept()
                try:
                    value, _descriptors = receive_message(channel)
                    send_message(channel, {
                        "schema": QWEN_SCHEMA,
                        "type": [],
                        "request_id": value["request_id"],
                    })
                finally:
                    channel.close()

            worker = threading.Thread(target=serve)
            worker.start()
            try:
                with self.assertRaises(ProviderError) as caught:
                    ProviderClient(QWEN_ENDPOINT, runtime, timeout=2).request(
                        request(QWEN_SCHEMA)
                    )
                self.assertEqual(caught.exception.code, "INVALID_RESPONSE")
            finally:
                worker.join(timeout=2)
                listener.close()
            self.assertFalse(worker.is_alive())

    def test_request_deadline_is_cumulative_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            os.chmod(runtime, 0o700)
            endpoint = runtime / QWEN_ENDPOINT.socket_name
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            listener.bind(str(endpoint))
            os.chmod(endpoint, 0o600)
            listener.listen(1)

            def serve() -> None:
                channel, _address = listener.accept()
                try:
                    receive_message(channel)
                    time.sleep(0.05)
                finally:
                    channel.close()

            worker = threading.Thread(target=serve)
            worker.start()
            value = request(QWEN_SCHEMA)
            value["deadline_ms"] = 10
            try:
                with self.assertRaises(ProviderError) as caught:
                    ProviderClient(QWEN_ENDPOINT, runtime).request(value)
                self.assertEqual(caught.exception.code, "DEADLINE_EXCEEDED")
                self.assertNotIn(str(runtime), str(caught.exception))
            finally:
                worker.join(timeout=2)
                listener.close()
            self.assertFalse(worker.is_alive())

    def test_public_runtime_directory_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            endpoint = runtime / QWEN_ENDPOINT.socket_name
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            listener.bind(str(endpoint))
            os.chmod(endpoint, 0o600)
            os.chmod(runtime, 0o755)
            try:
                with self.assertRaises(ProviderError) as caught:
                    ProviderClient(QWEN_ENDPOINT, runtime)._connect()
                self.assertEqual(caught.exception.code, "UNAUTHORIZED_PEER")
            finally:
                listener.close()


class StoreTests(unittest.TestCase):
    def test_explicit_consent_and_private_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "voicebox"
            sample = Path(directory) / "sample.wav"
            sample.write_bytes(wav_bytes())
            store = PrivateVoiceStore(root)
            with self.assertRaises(StoreError) as caught:
                store.attest_sample(profile_id="profile-1", sample_path=sample,
                                    asserted_by_user=False)
            self.assertEqual(caught.exception.code, "CONSENT_REQUIRED")
            consent = store.attest_sample(
                profile_id="profile-1", sample_path=sample, asserted_by_user=True
            )
            record = root / "consent" / f"{consent.sha256}.json"
            self.assertEqual(record.stat().st_mode & 0o777, 0o600)
            stored = json.loads(record.read_text())
            self.assertEqual(stored["consent"]["source_sha256"], consent.source_sha256)
            self.assertEqual(stored["consent_sha256"], consent.sha256)

    def test_generation_digest_and_recoverable_trash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PrivateVoiceStore(Path(directory) / "voicebox")
            sample = Path(directory) / "sample.wav"
            sample.write_bytes(wav_bytes())
            consent = store.attest_sample(
                profile_id="profile-1", sample_path=sample, asserted_by_user=True
            )
            audio = b"RIFF" + b"\x01" * 64
            digest = hashlib.sha256(audio).hexdigest()
            provenance = GenerationProvenance.create(
                generation_id="generation-1",
                job_id="job-1",
                provider="kilix-qwen-tts",
                model_id="model-not-selected",
                model_revision="revision-not-selected",
                output_sha256=digest,
                output_bytes=len(audio),
                source_sha256=consent.source_sha256,
                consent_sha256=consent.sha256,
            )
            import io
            destination = store.commit_generation(source=io.BytesIO(audio), provenance=provenance)
            self.assertEqual(destination.read_bytes(), audio)
            preview = store.preview_generation_delete("generation-1")
            self.assertEqual(len(preview), 3)
            self.assertTrue(any(name.startswith("consent/") for name in preview))
            receipt = store.trash_generation("generation-1")
            self.assertEqual(receipt["count"], 3)
            self.assertEqual(receipt["files"], list(preview))
            self.assertEqual(store.preview_generation_delete("generation-1"), ())

    def test_generation_collision_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PrivateVoiceStore(Path(directory) / "voicebox")
            sample = Path(directory) / "sample.wav"
            sample.write_bytes(wav_bytes())
            consent = store.attest_sample(
                profile_id="profile-1", sample_path=sample, asserted_by_user=True
            )
            audio = b"RIFF" + b"\x01" * 64
            provenance = GenerationProvenance.create(
                generation_id="generation-1", job_id="job-1",
                provider="kilix-qwen-tts", model_id="model-not-selected",
                model_revision="revision-not-selected",
                output_sha256=hashlib.sha256(audio).hexdigest(),
                output_bytes=len(audio), source_sha256=consent.source_sha256,
                consent_sha256=consent.sha256,
            )
            first = store.commit_generation(source=io.BytesIO(audio), provenance=provenance)
            with self.assertRaises(StoreError):
                store.commit_generation(source=io.BytesIO(audio), provenance=provenance)
            self.assertEqual(first.read_bytes(), audio)
            self.assertEqual(len(store.preview_generation_delete("generation-1")), 3)

    def test_generation_without_stored_consent_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PrivateVoiceStore(Path(directory) / "voicebox")
            audio = b"RIFF" + b"\x01" * 64
            provenance = GenerationProvenance.create(
                generation_id="generation-1", job_id="job-1",
                provider="kilix-qwen-tts", model_id="model-not-selected",
                model_revision="revision-not-selected",
                output_sha256=hashlib.sha256(audio).hexdigest(),
                output_bytes=len(audio), source_sha256="0" * 64,
                consent_sha256="1" * 64,
            )
            with self.assertRaises(StoreError) as caught:
                store.commit_generation(source=io.BytesIO(audio), provenance=provenance)
            self.assertEqual(caught.exception.code, "CONSENT_NOT_FOUND")

    def test_tampered_consent_is_recomputed_before_generation_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PrivateVoiceStore(Path(directory) / "voicebox")
            sample = Path(directory) / "sample.wav"
            sample.write_bytes(wav_bytes())
            consent = store.attest_sample(
                profile_id="profile-1", sample_path=sample, asserted_by_user=True
            )
            record = store.consent_root / f"{consent.sha256}.json"
            stored = json.loads(record.read_text())
            stored["consent"]["asserted_by_peer"] = False
            record.write_text(
                json.dumps(stored, separators=(",", ":"), sort_keys=True) + "\n"
            )
            audio = b"RIFF" + b"\x01" * 64
            provenance = GenerationProvenance.create(
                generation_id="generation-1", job_id="job-1",
                provider="kilix-qwen-tts", model_id="model-not-selected",
                model_revision="revision-not-selected",
                output_sha256=hashlib.sha256(audio).hexdigest(),
                output_bytes=len(audio), source_sha256=consent.source_sha256,
                consent_sha256=consent.sha256,
            )
            with self.assertRaises(StoreError) as caught:
                store.commit_generation(
                    source=io.BytesIO(audio), provenance=provenance
                )
            self.assertEqual(caught.exception.code, "INVALID_RECORD")

    def test_delete_traversal_is_refused_before_move(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            data = parent / "data"
            (data / "profiles").mkdir(mode=0o700, parents=True)
            outside = parent / "outside.txt"
            outside.write_text("preserve")
            store = PrivateVoiceStore(data / "privacy")
            traversal = data / "profiles" / ".." / ".." / outside.name
            with self.assertRaises(StoreError) as caught:
                store.preview_owned_delete((traversal,))
            self.assertEqual(caught.exception.code, "UNSAFE_DELETE")
            self.assertEqual(outside.read_text(), "preserve")

    def test_delete_ancestor_swap_is_refused_before_move(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            data = parent / "data"
            profiles = data / "profiles"
            profile = profiles / "profile-1"
            profile.mkdir(mode=0o700, parents=True)
            (profile / "sample.wav").write_bytes(wav_bytes(240))
            outside = parent / "outside"
            victim = outside / "profile-1"
            victim.mkdir(mode=0o700, parents=True)
            (victim / "preserve.wav").write_bytes(b"preserve")
            store = PrivateVoiceStore(data / "privacy")
            real_population = store._owned_path_population

            def inspect_then_swap(paths):
                population = real_population(paths)
                profiles.rename(data / "profiles-original")
                profiles.symlink_to(outside, target_is_directory=True)
                return population

            with (
                patch.object(
                    store, "_owned_path_population", side_effect=inspect_then_swap,
                ),
                self.assertRaises(StoreError) as caught,
            ):
                store.trash_owned_paths(
                    kind="profile", object_id="profile-1", paths=(profile,), metadata={}
                )
            self.assertEqual(caught.exception.code, "UNSAFE_DELETE")
            self.assertEqual((victim / "preserve.wav").read_bytes(), b"preserve")
            self.assertTrue(
                (data / "profiles-original" / "profile-1" / "sample.wav").is_file()
            )

    def test_incomplete_generic_rollback_preserves_recovery_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            profile = data / "profiles" / "profile-1"
            profile.mkdir(mode=0o700, parents=True)
            (profile / "sample.wav").write_bytes(wav_bytes(240))
            store = PrivateVoiceStore(data / "privacy")
            real_replace = os.replace
            real_atomic_record = store._atomic_record

            move_count = 0

            def replace_with_failed_restore(
                source, destination, *, src_dir_fd=None, dst_dir_fd=None,
            ):
                nonlocal move_count
                move_count += 1
                if move_count > 1:
                    raise OSError("injected restore failure")
                return real_replace(
                    source,
                    destination,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )

            def record_with_failed_receipt(destination, value):
                if Path(destination).name == "delete-receipt.json":
                    raise StoreError("STORE_WRITE", "injected receipt failure")
                return real_atomic_record(destination, value)

            with (
                patch.object(
                    store, "_atomic_record", side_effect=record_with_failed_receipt,
                ),
                patch("kilix_voicebox_backend.private_store.os.replace", side_effect=replace_with_failed_restore),
                self.assertRaises(StoreError) as caught,
            ):
                store.trash_owned_paths(
                    kind="profile", object_id="profile-1", paths=(profile,), metadata={}
                )
            self.assertEqual(caught.exception.code, "ROLLBACK_INCOMPLETE")
            batches = tuple((store.root / "trash").iterdir())
            self.assertEqual(len(batches), 1)
            self.assertFalse(profile.exists())
            self.assertTrue((batches[0] / "profiles" / "profile-1" / "sample.wav").is_file())
            self.assertTrue((batches[0] / "rollback-failure.json").is_file())

    def test_incomplete_generation_rollback_preserves_recovery_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PrivateVoiceStore(Path(directory) / "voicebox")
            sample = Path(directory) / "sample.wav"
            sample.write_bytes(wav_bytes())
            consent = store.attest_sample(
                profile_id="profile-1", sample_path=sample, asserted_by_user=True
            )
            audio = b"RIFF" + b"\x01" * 64
            provenance = GenerationProvenance.create(
                generation_id="generation-1", job_id="job-1",
                provider="kilix-qwen-tts", model_id="model-not-selected",
                model_revision="revision-not-selected",
                output_sha256=hashlib.sha256(audio).hexdigest(),
                output_bytes=len(audio), source_sha256=consent.source_sha256,
                consent_sha256=consent.sha256,
            )
            store.commit_generation(source=io.BytesIO(audio), provenance=provenance)
            real_replace = os.replace
            real_atomic_record = store._atomic_record

            move_count = 0

            def replace_with_failed_restore(
                source, destination, *, src_dir_fd=None, dst_dir_fd=None,
            ):
                nonlocal move_count
                move_count += 1
                if move_count > 3:
                    raise OSError("injected restore failure")
                return real_replace(
                    source,
                    destination,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )

            def record_with_failed_receipt(destination, value):
                if Path(destination).name == "delete-receipt.json":
                    raise StoreError("STORE_WRITE", "injected receipt failure")
                return real_atomic_record(destination, value)

            with (
                patch.object(
                    store, "_atomic_record", side_effect=record_with_failed_receipt,
                ),
                patch("kilix_voicebox_backend.private_store.os.replace", side_effect=replace_with_failed_restore),
                self.assertRaises(StoreError) as caught,
            ):
                store.trash_generation("generation-1")
            self.assertEqual(caught.exception.code, "ROLLBACK_INCOMPLETE")
            batches = tuple((store.root / "trash").iterdir())
            self.assertEqual(len(batches), 1)
            self.assertTrue((batches[0] / "rollback-failure.json").is_file())
            self.assertTrue((batches[0] / "generations" / "generation-1.wav").is_file())

    def test_generic_delete_has_exact_preview_and_private_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            profile = data / "profiles" / "profile-1"
            profile.mkdir(mode=0o700, parents=True)
            sample = profile / "sample.wav"
            sample.write_bytes(wav_bytes(240))
            store = PrivateVoiceStore(data / "privacy")
            preview = store.preview_owned_delete((profile,))
            self.assertEqual(
                preview,
                ("profiles/profile-1/", "profiles/profile-1/sample.wav"),
            )
            receipt = store.trash_owned_paths(
                kind="profile",
                object_id="profile-1",
                paths=(profile,),
                metadata={"sample_count": 1},
            )
            self.assertEqual(receipt["count"], len(preview))
            self.assertFalse(profile.exists())
            receipt_path = data / "privacy" / "trash" / receipt["batch_id"] / "delete-receipt.json"
            self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o600)
            stored = json.loads(receipt_path.read_text())
            self.assertEqual(stored["moved"], list(preview))


class ExportImportTests(unittest.TestCase):
    def _session(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def test_profile_source_samples_are_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = self._session()
            profiles_root = Path(directory) / "profiles"
            sample_path = profiles_root / "profile-1" / "sample-1.wav"
            sample_path.parent.mkdir(mode=0o700, parents=True)
            sample_path.write_bytes(wav_bytes(240))
            db.add(DBVoiceProfile(
                id="profile-1", name="Voice", description=None, language="en"
            ))
            db.add(DBProfileSample(
                id="sample-1", profile_id="profile-1", audio_path=str(sample_path),
                reference_text="sample text",
            ))
            db.commit()
            with patch(
                "kilix_voicebox_backend.profiles.config.get_profiles_dir", return_value=profiles_root
            ):
                without_samples = export_import.export_profile_to_zip("profile-1", db)
            with zipfile.ZipFile(io.BytesIO(without_samples)) as archive:
                self.assertEqual(set(archive.namelist()), {"manifest.json", "samples.json"})
                self.assertEqual(json.loads(archive.read("samples.json")), {})
            with patch(
                "kilix_voicebox_backend.profiles.config.get_profiles_dir", return_value=profiles_root
            ):
                selected = export_import.export_profile_to_zip(
                    "profile-1", db, include_samples=True
                )
            with zipfile.ZipFile(io.BytesIO(selected)) as archive:
                self.assertIn("samples/sample-1.wav", archive.namelist())
                self.assertEqual(
                    json.loads(archive.read("samples.json")),
                    {"sample-1.wav": "sample text"},
                )
            db.close()

    def test_profile_import_refuses_undeclared_entries(self) -> None:
        db = self._session()
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps({
                "schema": "kilix.voicebox.profile-export/candidate-v1",
                "version": "1.0",
                "profile": {"name": "Voice", "description": None, "language": "en"},
                "avatar": None,
                "samples_included": False,
            }))
            archive.writestr("samples.json", "{}")
            archive.writestr("undeclared.txt", "not admitted")
        with self.assertRaises(ValueError):
            asyncio.run(export_import.import_profile_from_zip(output.getvalue(), db))
        self.assertEqual(db.query(DBVoiceProfile).count(), 0)
        db.close()

    def test_profile_import_refuses_duplicate_json_keys(self) -> None:
        db = self._session()
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                '{"schema":"kilix.voicebox.profile-export/candidate-v1",'
                '"schema":"duplicate","version":"1.0","profile":{},'
                '"avatar":null,"samples_included":false}',
            )
            archive.writestr("samples.json", "{}")
        with self.assertRaises(ValueError):
            asyncio.run(export_import.import_profile_from_zip(output.getvalue(), db))
        self.assertEqual(db.query(DBVoiceProfile).count(), 0)
        db.close()

    def test_generation_export_carries_verified_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = self._session()
            data = Path(directory) / "data"
            store = PrivateVoiceStore(data / "privacy")
            sample = Path(directory) / "sample.wav"
            sample.write_bytes(wav_bytes(240))
            consent = store.attest_sample(
                profile_id="profile-1", sample_path=sample, asserted_by_user=True
            )
            audio = wav_bytes(480)
            provenance = GenerationProvenance.create(
                generation_id="generation-1", job_id="job-1",
                provider="kilix-qwen-tts", model_id="candidate-model",
                model_revision="candidate-revision",
                output_sha256=hashlib.sha256(audio).hexdigest(),
                output_bytes=len(audio), source_sha256=consent.source_sha256,
                consent_sha256=consent.sha256,
            )
            destination = store.commit_generation(
                source=io.BytesIO(audio), provenance=provenance
            )
            db.add(DBVoiceProfile(
                id="profile-1", name="Voice", description=None, language="en"
            ))
            db.add(DBGeneration(
                id="generation-1", profile_id="profile-1", text="hello",
                language="en", audio_path=str(destination), duration=0.02,
                seed=7, instruct=None,
            ))
            db.commit()
            with patch.object(export_import.config, "get_privacy_dir", return_value=store.root):
                payload = export_import.export_generation_to_zip("generation-1", db)
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"manifest.json", "audio/generation-1.wav", "provenance.json"},
                )
                stored = json.loads(archive.read("provenance.json"))
                self.assertEqual(stored["model_revision"], "candidate-revision")
                self.assertEqual(stored["output_sha256"], hashlib.sha256(audio).hexdigest())
            db.close()


class StoryRecoveryTests(unittest.TestCase):
    def test_story_delete_archives_exact_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine("sqlite:///:memory:")
            Base.metadata.create_all(engine)
            db = sessionmaker(bind=engine)()
            db.add(DBVoiceProfile(
                id="profile-1", name="Voice", description=None, language="en"
            ))
            db.add(DBGeneration(
                id="generation-1", profile_id="profile-1", text="hello",
                language="en", audio_path="/private/generation-1.wav",
                duration=1.0, seed=7, instruct=None,
            ))
            db.add(DBStory(id="story-1", name="Story", description="recover me"))
            db.add(DBStoryItem(
                id="item-1", story_id="story-1", generation_id="generation-1",
                start_time_ms=0, track=0, trim_start_ms=0, trim_end_ms=0,
            ))
            db.commit()
            privacy = Path(directory) / "data" / "privacy"
            with patch.object(stories.config, "get_privacy_dir", return_value=privacy):
                preview = asyncio.run(stories.preview_story_delete("story-1", db))
                receipt = asyncio.run(stories.delete_story("story-1", db))
            self.assertIsNotNone(preview)
            self.assertIsNotNone(receipt)
            assert preview is not None and receipt is not None
            self.assertEqual(preview["count"], 2)
            self.assertEqual(receipt["files"], preview["files"])
            self.assertEqual(db.query(DBStory).count(), 0)
            self.assertEqual(db.query(DBStoryItem).count(), 0)
            self.assertEqual(db.query(DBGeneration).count(), 1)
            batch = privacy / "trash" / receipt["batch_id"]
            stored = json.loads((batch / "delete-receipt.json").read_text())
            self.assertEqual(stored["archived"], preview["files"])
            self.assertTrue((batch / "records" / preview["files"][0]).is_file())
            db.close()


class StoryBoundaryTests(unittest.TestCase):
    @staticmethod
    def _session():
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    @staticmethod
    def _story_fixture(db) -> None:
        db.add(DBVoiceProfile(
            id="profile-1", name="Voice", description=None, language="en"
        ))
        db.add(DBGeneration(
            id="generation-1", profile_id="profile-1", text="hello",
            language="en", audio_path="/private/generation-1.wav",
            duration=1.0, seed=7, instruct=None,
        ))
        db.add(DBStory(id="story-1", name="Story", description=None))
        db.add(DBStoryItem(
            id="item-1", story_id="story-1", generation_id="generation-1",
            start_time_ms=0, track=0, trim_start_ms=0, trim_end_ms=0,
        ))
        db.commit()

    def test_split_duplicate_reorder_and_update_use_item_ids(self) -> None:
        db = self._session()
        try:
            self._story_fixture(db)
            split = asyncio.run(stories.split_story_item(
                "story-1", "item-1", models.StoryItemSplit(split_time_ms=400), db
            ))
            self.assertIsNotNone(split)
            assert split is not None
            duplicate = asyncio.run(stories.duplicate_story_item(
                "story-1", split[0].id, db
            ))
            self.assertIsNotNone(duplicate)
            assert duplicate is not None
            item_ids = [split[0].id, split[1].id, duplicate.id]
            self.assertEqual(len(set(item_ids)), 3)
            self.assertEqual(
                {item.generation_id for item in (*split, duplicate)},
                {"generation-1"},
            )
            requested = list(reversed(item_ids))
            reordered = asyncio.run(stories.reorder_story_items(
                "story-1", requested, db
            ))
            self.assertIsNotNone(reordered)
            assert reordered is not None
            self.assertEqual([item.id for item in reordered], requested)
            target = item_ids[0]
            updated = asyncio.run(stories.update_story_item_times(
                "story-1",
                models.StoryItemBatchUpdate(updates=[
                    models.StoryItemUpdateTime(item_id=target, start_time_ms=123)
                ]),
                db,
            ))
            self.assertTrue(updated)
            self.assertEqual(db.query(DBStoryItem).filter_by(id=target).one().start_time_ms, 123)
        finally:
            db.close()

    def test_second_profile_sample_is_refused_before_decode(self) -> None:
        db = self._session()
        try:
            db.add(DBVoiceProfile(
                id="profile-1", name="Voice", description=None, language="en"
            ))
            db.add(DBProfileSample(
                id="sample-1", profile_id="profile-1",
                audio_path="/private/sample.wav", reference_text="sample",
            ))
            db.commit()
            with (
                patch("kilix_voicebox_backend.profiles.validate_reference_audio") as validate,
                self.assertRaisesRegex(ValueError, "Exactly one source sample"),
            ):
                asyncio.run(profiles.add_profile_sample(
                    "profile-1", "/unreadable.wav", "second", db
                ))
            validate.assert_not_called()
            self.assertEqual(
                db.query(DBProfileSample).filter_by(profile_id="profile-1").count(), 1
            )
        finally:
            db.close()

    def test_three_timecode_request_models_are_bounded(self) -> None:
        extreme = models.MAX_STORY_TIMELINE_MS + 1
        for value in (-1, extreme):
            constructors = (
                lambda: models.StoryItemCreate(
                    generation_id="generation-1", start_time_ms=value
                ),
                lambda: models.StoryItemUpdateTime(
                    item_id="item-1", start_time_ms=value
                ),
                lambda: models.StoryItemMove(start_time_ms=value),
            )
            for constructor in constructors:
                with self.subTest(value=value, constructor=constructor):
                    with self.assertRaises(ValueError):
                        constructor()

    def test_persisted_extreme_timecode_refuses_before_allocation(self) -> None:
        db = self._session()
        try:
            self._story_fixture(db)
            item = db.query(DBStoryItem).filter_by(id="item-1").one()
            item.start_time_ms = models.MAX_STORY_TIMELINE_MS
            db.commit()
            with (
                patch("kilix_voicebox_backend.stories.np.zeros") as allocate,
                self.assertRaisesRegex(ValueError, "STORY_EXPORT_BOUND"),
            ):
                asyncio.run(stories.export_story_audio("story-1", db))
            allocate.assert_not_called()
        finally:
            db.close()

    def test_story_mutations_refuse_unexportable_windows_and_population(self) -> None:
        db = self._session()
        try:
            self._story_fixture(db)
            db.add(DBGeneration(
                id="generation-2", profile_id="profile-1", text="again",
                language="en", audio_path="/private/generation-2.wav",
                duration=1.0, seed=8, instruct=None,
            ))
            db.commit()

            refused_add = asyncio.run(stories.add_item_to_story(
                "story-1",
                models.StoryItemCreate(
                    generation_id="generation-2",
                    start_time_ms=models.MAX_STORY_TIMELINE_MS,
                ),
                db,
            ))
            self.assertIsNone(refused_add)
            self.assertEqual(db.query(DBStoryItem).count(), 1)

            refused_move = asyncio.run(stories.move_story_item(
                "story-1", "item-1",
                models.StoryItemMove(start_time_ms=models.MAX_STORY_TIMELINE_MS),
                db,
            ))
            self.assertIsNone(refused_move)
            self.assertEqual(
                db.query(DBStoryItem).filter_by(id="item-1").one().start_time_ms,
                0,
            )

            refused_batch = asyncio.run(stories.update_story_item_times(
                "story-1",
                models.StoryItemBatchUpdate(updates=[
                    models.StoryItemUpdateTime(
                        item_id="item-1",
                        start_time_ms=models.MAX_STORY_TIMELINE_MS,
                    )
                ]),
                db,
            ))
            self.assertFalse(refused_batch)
            item = db.query(DBStoryItem).filter_by(id="item-1").one()
            self.assertEqual(item.start_time_ms, 0)

            item.start_time_ms = models.MAX_STORY_TIMELINE_MS - 1_000
            db.commit()
            self.assertIsNone(asyncio.run(stories.duplicate_story_item(
                "story-1", "item-1", db
            )))
            self.assertIsNone(asyncio.run(stories.add_item_to_story(
                "story-1", models.StoryItemCreate(generation_id="generation-2"), db
            )))
            self.assertEqual(db.query(DBStoryItem).count(), 1)

            db.add_all([
                DBStoryItem(
                    id=f"population-{index}", story_id="story-1",
                    generation_id="generation-1", start_time_ms=0, track=0,
                    trim_start_ms=0, trim_end_ms=0,
                )
                for index in range(models.MAX_STORY_ITEMS - 1)
            ])
            db.commit()
            self.assertEqual(
                db.query(DBStoryItem).count(), models.MAX_STORY_ITEMS
            )
            self.assertIsNone(asyncio.run(stories.duplicate_story_item(
                "story-1", "item-1", db
            )))
            self.assertIsNone(asyncio.run(stories.split_story_item(
                "story-1", "item-1", models.StoryItemSplit(split_time_ms=400), db
            )))
            self.assertEqual(
                db.query(DBStoryItem).count(), models.MAX_STORY_ITEMS
            )
        finally:
            db.close()

    def test_story_export_refuses_missing_and_corrupt_clips(self) -> None:
        for source_state in ("missing", "corrupt"):
            with self.subTest(source_state=source_state), tempfile.TemporaryDirectory() as directory:
                db = self._session()
                try:
                    self._story_fixture(db)
                    privacy = Path(directory) / "privacy"
                    generations = privacy / "generations"
                    generations.mkdir(parents=True)
                    first_path = generations / "generation-1.wav"
                    second_path = generations / "generation-2.wav"
                    first_path.write_bytes(wav_bytes(frames=24_000))
                    if source_state == "corrupt":
                        second_path.write_bytes(b"not an audio file")
                    generation = db.query(DBGeneration).filter_by(
                        id="generation-1"
                    ).one()
                    generation.audio_path = str(first_path)
                    db.add(DBGeneration(
                        id="generation-2", profile_id="profile-1", text="again",
                        language="en", audio_path=str(second_path), duration=1.0,
                        seed=8, instruct=None,
                    ))
                    db.add(DBStoryItem(
                        id="item-2", story_id="story-1",
                        generation_id="generation-2", start_time_ms=1_000,
                        track=0, trim_start_ms=0, trim_end_ms=0,
                    ))
                    db.commit()
                    with (
                        patch.object(
                            stories.config, "get_privacy_dir", return_value=privacy
                        ),
                        self.assertRaisesRegex(ValueError, "^STORY_EXPORT_SOURCE$"),
                    ):
                        asyncio.run(stories.export_story_audio("story-1", db))
                finally:
                    db.close()

    def test_story_read_refuses_overpopulation_and_incomplete_joins(self) -> None:
        db = self._session()
        try:
            self._story_fixture(db)
            db.add_all([
                DBStoryItem(
                    id=f"excess-{index}", story_id="story-1",
                    generation_id="generation-1", start_time_ms=0, track=0,
                    trim_start_ms=0, trim_end_ms=0,
                )
                for index in range(models.MAX_STORY_ITEMS)
            ])
            db.commit()
            self.assertEqual(
                db.query(DBStoryItem).count(), models.MAX_STORY_ITEMS + 1
            )
            with self.assertRaisesRegex(ValueError, "^STORY_READ_ITEM_BOUND$"):
                asyncio.run(stories.get_story("story-1", db))
        finally:
            db.close()

        db = self._session()
        try:
            self._story_fixture(db)
            db.add(DBStoryItem(
                id="orphan", story_id="story-1", generation_id="missing",
                start_time_ms=0, track=0, trim_start_ms=0, trim_end_ms=0,
            ))
            db.commit()
            self.assertEqual(db.query(DBStoryItem).count(), 2)
            with self.assertRaisesRegex(ValueError, "^STORY_READ_INTEGRITY$"):
                asyncio.run(stories.get_story("story-1", db))
        finally:
            db.close()

    def test_extreme_finite_story_duration_refuses_stably(self) -> None:
        db = self._session()
        try:
            self._story_fixture(db)
            generation = db.query(DBGeneration).filter_by(
                id="generation-1"
            ).one()
            generation.duration = 1e308
            db.commit()
            self.assertIsNone(stories._generation_duration_ms(generation))
            with self.assertRaisesRegex(ValueError, "^STORY_READ_BOUND$"):
                asyncio.run(stories.get_story("story-1", db))
        finally:
            db.close()

    def test_trimmed_story_playback_uses_effective_duration(self) -> None:
        root = Path(__file__).resolve().parents[2] / "app" / "src"
        sources = (
            root / "components" / "StoriesTab" / "StoryContent.tsx",
            root / "components" / "StoriesTab" / "StoryChatItem.tsx",
            root / "stores" / "storyStore.ts",
        )
        for source in sources:
            with self.subTest(source=source.name):
                text = source.read_text()
                self.assertIn("item.trim_start_ms ?? 0", text)
                self.assertIn("item.trim_end_ms ?? 0", text)

    def test_voice_profile_docs_match_the_one_sample_boundary(self) -> None:
        root = Path(__file__).resolve().parents[2] / "docs"
        developer = (root / "developer" / "voice-profiles.mdx").read_text()
        overview = (root / "overview" / "creating-voice-profiles.mdx").read_text()
        combined = developer + overview
        self.assertIn("enforces the exact 1/1 population", developer)
        self.assertIn("exactly 1/1 source sample", overview)
        for stale_claim in (
            "Multiple Samples",
            "Multiple samples (3-5)",
            "Celebrity/Character Voices",
            "combine_voice_prompts",
            "Add more samples",
        ):
            with self.subTest(stale_claim=stale_claim):
                self.assertNotIn(stale_claim, combined)

    def test_voice_profile_docs_match_consent_and_export_boundaries(self) -> None:
        root = Path(__file__).resolve().parents[2] / "docs"
        developer = (root / "developer" / "voice-profiles.mdx").read_text()
        overview = (root / "overview" / "creating-voice-profiles.mdx").read_text()
        for required in (
            "never supply a destination path",
            "consent_asserted=request.consent_asserted",
            "samples_included: false",
            "include_samples=true",
            "biometric source audio",
        ):
            with self.subTest(required=required):
                self.assertIn(required, developer + overview)
        for stale_claim in (
            "tts_model.create_voice_prompt",
            "Profiles include voice embeddings, not original audio",
            "{sample_id_2}.wav",
        ):
            with self.subTest(stale_claim=stale_claim):
                self.assertNotIn(stale_claim, developer + overview)


class DesktopPolicyTests(unittest.TestCase):
    def test_production_csp_admits_fixed_loopback_images_and_media(self) -> None:
        config_path = (
            Path(__file__).resolve().parents[2]
            / "tauri" / "src-tauri" / "tauri.conf.json"
        )
        csp = json.loads(config_path.read_text())["app"]["security"]["csp"]
        directives = {
            part.strip().split(maxsplit=1)[0]: part.strip()
            for part in csp.split(";") if part.strip()
        }
        for directive in ("img-src", "media-src"):
            self.assertIn("http://127.0.0.1:17493", directives[directive])

    def test_sidecar_start_failure_has_error_and_retry_ui(self) -> None:
        app_source = (
            Path(__file__).resolve().parents[2] / "app" / "src" / "App.tsx"
        ).read_text()
        self.assertIn("VOICEBOX_SIDECAR_START_FAILED", app_source)
        self.assertIn("Retry", app_source)


class GenerationRouteTests(unittest.TestCase):
    def test_generated_seed_is_sent_and_persisted(self) -> None:
        now = datetime.now()
        provider_result = {
            "duration_ms": 100,
            "generation_id": "generation-1",
            "seed": 8123,
            "stored_name": "generation-1.wav",
        }
        orchestrator = Mock()
        orchestrator.synthesize_clone.return_value = provider_result
        created = models.GenerationResponse(
            id="generation-1", profile_id="profile-1", text="hello",
            language="en", audio_path="/private/generation-1.wav", duration=0.1,
            seed=8123, instruct=None, created_at=now,
        )
        with (
            patch("kilix_voicebox_backend.main.profiles.get_profile", new=AsyncMock(return_value=object())),
            patch(
                "kilix_voicebox_backend.main.profiles.create_voice_prompt_for_profile",
                new=AsyncMock(return_value={"audio_path": "/private/sample.wav"}),
            ),
            patch("kilix_voicebox_backend.main._voice_orchestrator", return_value=orchestrator),
            patch("kilix_voicebox_backend.main.config.get_privacy_dir", return_value=Path("/private")),
            patch(
                "kilix_voicebox_backend.main.history.create_generation",
                new=AsyncMock(return_value=created),
            ) as create_generation,
        ):
            result = asyncio.run(generate_speech(
                models.GenerationRequest(
                    profile_id="profile-1", text="hello", language="en",
                    consent_asserted=True,
                ),
                db=object(),
            ))
        self.assertEqual(result.seed, 8123)
        self.assertIsNone(orchestrator.synthesize_clone.call_args.kwargs["seed"])
        self.assertEqual(create_generation.await_args.kwargs["seed"], 8123)


class PublicResponseTests(unittest.TestCase):
    def test_private_paths_serialize_as_loopback_resources(self) -> None:
        now = datetime.now()
        responses = (
            models.VoiceProfileResponse(
                id="profile-1", name="Voice", description=None, language="en",
                avatar_path="/private/avatar.png", created_at=now, updated_at=now,
            ),
            models.ProfileSampleResponse(
                id="sample-1", profile_id="profile-1",
                audio_path="/private/sample.wav", reference_text="sample",
            ),
            models.GenerationResponse(
                id="generation-1", profile_id="profile-1", text="hello",
                language="en", audio_path="/private/generated.wav", duration=1.0,
                seed=None, instruct=None, created_at=now,
            ),
            models.HistoryResponse(
                id="generation-1", profile_id="profile-1", profile_name="Voice",
                text="hello", language="en", audio_path="/private/generated.wav",
                duration=1.0, seed=None, instruct=None, created_at=now,
            ),
            models.StoryItemDetail(
                id="item-1", story_id="story-1", generation_id="generation-1",
                start_time_ms=0, created_at=now, profile_id="profile-1",
                profile_name="Voice", text="hello", language="en",
                audio_path="/private/generated.wav", duration=1.0, seed=None,
                instruct=None, generation_created_at=now,
            ),
        )
        serialized = tuple(response.model_dump(mode="json") for response in responses)
        self.assertFalse(any("/private/" in str(response) for response in serialized))
        self.assertEqual(serialized[0]["avatar_path"], "/profiles/profile-1/avatar")
        self.assertEqual(serialized[1]["audio_path"], "/samples/sample-1")
        self.assertEqual(serialized[2]["audio_path"], "/audio/generation-1")
        self.assertEqual(serialized[3]["audio_path"], "/audio/generation-1")
        self.assertEqual(serialized[4]["audio_path"], "/audio/generation-1")


@unittest.skipUnless(hasattr(socket, "SOCK_SEQPACKET"), "SOCK_SEQPACKET unavailable")
class OrchestratorTests(unittest.TestCase):
    def _runtime(self, parent: Path, endpoint_name: str):
        runtime = parent / endpoint_name
        runtime.mkdir(mode=0o700)
        os.chmod(runtime, 0o700)
        return runtime

    def test_transcription_returns_without_product_retention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            qwen_runtime = self._runtime(parent, "qwen-runtime")
            transcribe_runtime = self._runtime(parent, "transcribe-runtime")
            qwen_socket = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            qwen_socket.bind(str(qwen_runtime / QWEN_ENDPOINT.socket_name))
            os.chmod(qwen_runtime / QWEN_ENDPOINT.socket_name, 0o600)
            qwen_socket.listen(1)
            transcribe_socket = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            transcribe_socket.bind(str(transcribe_runtime / TRANSCRIBE_ENDPOINT.socket_name))
            os.chmod(transcribe_runtime / TRANSCRIBE_ENDPOINT.socket_name, 0o600)
            transcribe_socket.listen(1)
            audio = parent / "input.wav"
            audio.write_bytes(wav_bytes(2_400))

            def serve() -> None:
                channel, _address = transcribe_socket.accept()
                value, descriptors = receive_message(channel)
                try:
                    self.assertEqual(len(descriptors), 1)
                    self.assertEqual(os.read(descriptors[0], 4), b"RIFF")
                    self.assertEqual(value["args"]["audio"]["media_type"], "audio/wav")
                    send_message(channel, {
                        "schema": TRANSCRIBE_ENDPOINT.schema,
                        "type": "result",
                        "request_id": value["request_id"],
                        "job_id": value["job_id"],
                        "result": {"text": "synthetic transcript", "retained": False},
                    })
                finally:
                    for descriptor in descriptors:
                        os.close(descriptor)
                    channel.close()

            worker = threading.Thread(target=serve)
            worker.start()
            try:
                orchestrator = VoiceboxOrchestrator(
                    ProviderClient(QWEN_ENDPOINT, qwen_runtime),
                    ProviderClient(TRANSCRIBE_ENDPOINT, transcribe_runtime),
                    PrivateVoiceStore(parent / "store"),
                )
                result = orchestrator.transcribe_file(audio_path=audio)
                self.assertEqual(result, {"text": "synthetic transcript", "retained": False})
                self.assertEqual(list((parent / "store" / "generations").iterdir()), [])
            finally:
                worker.join(timeout=2)
                qwen_socket.close()
                transcribe_socket.close()
            self.assertFalse(worker.is_alive())

    def test_synthesis_binds_pcm_consent_and_commits_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            qwen_runtime = self._runtime(parent, "qwen-runtime")
            transcribe_runtime = self._runtime(parent, "transcribe-runtime")
            endpoint_path = qwen_runtime / QWEN_ENDPOINT.socket_name
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            listener.bind(str(endpoint_path))
            os.chmod(endpoint_path, 0o600)
            listener.settimeout(2)
            listener.listen(1)
            sample = parent / "sample.wav"
            sample.write_bytes(wav_bytes())
            output = wav_bytes(2_400)

            def serve() -> None:
                channel, _address = listener.accept()
                value, descriptors = receive_message(channel)
                output_file = None
                try:
                    self.assertEqual(len(descriptors), 1)
                    self.assertEqual(
                        fcntl.fcntl(descriptors[0], fcntl.F_GETFL) & os.O_ACCMODE,
                        os.O_RDONLY,
                    )
                    with self.assertRaises(OSError):
                        os.write(descriptors[0], b"mutate")
                    prompt = value["args"]["prompt_audio"]
                    pcm = b""
                    while True:
                        chunk = os.read(descriptors[0], 65_536)
                        if not chunk:
                            break
                        pcm += chunk
                    self.assertEqual(len(pcm), prompt["byte_length"])
                    self.assertEqual(hashlib.sha256(pcm).hexdigest(), prompt["sha256"])
                    consent = value["args"]["consent"]
                    self.assertIs(type(value["args"]["seed"]), int)
                    self.assertGreaterEqual(value["args"]["seed"], 0)
                    consent_encoded = json.dumps(
                        consent, separators=(",", ":"), sort_keys=True
                    ).encode()
                    consent_sha256 = hashlib.sha256(consent_encoded).hexdigest()
                    output_file = tempfile.TemporaryFile(dir=parent)
                    output_file.write(output)
                    output_file.seek(0)
                    send_message(channel, {
                        "schema": QWEN_SCHEMA,
                        "type": "result",
                        "request_id": value["request_id"],
                        "job_id": value["job_id"],
                        "result": {
                            "audio": {
                                "sha256": hashlib.sha256(output).hexdigest(),
                                "byte_length": len(output),
                            },
                            "duration_ms": 100,
                            "model_id": "candidate-clone",
                            "model_revision": "rev-1",
                            "conditioning": {
                                "prompt_sha256": prompt["sha256"],
                                "consent_sha256": consent_sha256,
                            },
                        },
                    }, output_file.fileno())
                finally:
                    for descriptor in descriptors:
                        os.close(descriptor)
                    if output_file is not None:
                        output_file.close()
                    channel.close()

            worker = threading.Thread(target=serve)
            worker.start()
            try:
                store = PrivateVoiceStore(parent / "store")
                orchestrator = VoiceboxOrchestrator(
                    ProviderClient(QWEN_ENDPOINT, qwen_runtime, timeout=2),
                    ProviderClient(TRANSCRIBE_ENDPOINT, transcribe_runtime, timeout=2),
                    store,
                )
                result = orchestrator.synthesize_clone(
                    profile_id="profile-1",
                    sample_path=sample,
                    text="Consent-bound synthesis",
                    language="en",
                    consent_asserted=True,
                )
                self.assertEqual(result["duration_ms"], 100)
                generation = store.generation_root / result["stored_name"]
                self.assertEqual(generation.read_bytes(), output)
                provenance = json.loads(
                    (store.generation_root / f"{result['generation_id']}.provenance.json")
                    .read_text()
                )
                self.assertEqual(provenance["model_revision"], "rev-1")
                self.assertEqual(len(store.preview_generation_delete(result["generation_id"])), 3)
            finally:
                worker.join(timeout=2)
                listener.close()
            self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()

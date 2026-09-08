from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

from kilix_voicebox_backend import models
from kilix_voicebox_backend.kilix_provider import ProviderError, ProviderReply, QWEN_ENDPOINT
from kilix_voicebox_backend.orchestrator import VoiceboxOrchestrator
from kilix_voicebox_backend.private_store import (
    GeneratedVoiceProvenance, PrivateVoiceStore, StoreError,
)


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24_000)
        stream.writeframes(b"\x01\x00" * 240)
    return output.getvalue()


class GeneratedModesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = PrivateVoiceStore(self.root / "store")
        self.qwen = Mock(endpoint=QWEN_ENDPOINT)
        self.orchestrator = VoiceboxOrchestrator(self.qwen, Mock(), self.store)
        self.mutate = lambda result, audio: (result, audio)
        self.sent = []
        self.replies = []
        self.qwen.request.side_effect = self.reply

    def tearDown(self):
        for reply in self.replies:
            reply.close()
        self.temporary.cleanup()

    def reply(self, request, descriptor=None):
        self.assertIsNone(descriptor)
        arguments = request["args"]
        self.sent.append(arguments)
        mode = arguments["mode"]
        identity = {
            "named_voice": ("qwen3-tts-0.6b-customvoice", "85e237c12c027371202489a0ec509ded67b5e4b5"),
            "voice_design": ("qwen3-tts-1.7b-voicedesign", "5ecdb67327fd37bb2e042aab12ff7391903235d3"),
        }[mode]
        audio = wav_bytes()
        result = {"engine_id": "qwen3-tts", "engine_revision": "6cafe5582caea83df269c36b1ce62d953a9cc66b",
                  "model_id": identity[0], "model_revision": identity[1],
                  "duration_ms": 10, "seed": arguments["seed"],
                  "audio": {"sha256": hashlib.sha256(audio).hexdigest(), "byte_length": len(audio)}}
        result, audio = self.mutate(result, audio)
        path = self.root / "result.wav"
        path.write_bytes(audio)
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        path.unlink()
        reply = ProviderReply({"type": "result", "result": result}, (descriptor,))
        self.replies.append(reply)
        return reply

    def synthesize(self, mode="named_voice"):
        return self.orchestrator.synthesize_generated(
            mode=mode, text="Hello there.", language="en", seed=7,
            **({"voice_id": "Vivian"} if mode == "named_voice" else {"description": "A calm adult voice"}),
        )

    def test_both_modes_commit_without_sample_or_consent_and_have_bound_provenance(self):
        with patch.object(self.store, "attest_sample", side_effect=AssertionError("must not attest")):
            for mode in ["named_voice", "voice_design"]:
                with self.subTest(mode=mode):
                    result = self.synthesize(mode)
                    self.assertEqual((self.store.generation_root / result["stored_name"]).read_bytes(), wav_bytes())
                    provenance = self.store.generation_provenance(result["generation_id"])
                    expected = hashlib.sha256(json.dumps(self.sent[-1], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    self.assertEqual(provenance["request_sha256"], expected)
                    self.assertEqual(provenance["mode"], mode)
                    self.assertNotIn("source_sha256", provenance)
                    self.assertNotIn("consent_sha256", provenance)
                    self.assertEqual(list(self.store.consent_root.iterdir()), [])
                    self.assertEqual(len(self.store.preview_generation_delete(result["generation_id"])), 2)
                    self.assertEqual(self.replies[-1].descriptors, ())

    def test_malformed_or_mismatched_results_never_commit(self):
        def changed(field, value):
            return lambda result, audio: ({**result, field: value}, audio)

        mutations = [changed("seed", 8), changed("seed", True), changed("model_id", []),
                     changed("engine_revision", "0" * 40), changed("duration_ms", True),
                     changed("model_revision", "0" * 40),
                     changed("conditioning", {"consent_sha256": "0" * 64}),
                     changed("model_id", "qwen3-tts-1.7b-voicedesign"),
                     changed("audio", {"sha256": "0" * 64, "byte_length": len(wav_bytes())}),
                     lambda result, audio: (result, audio[:28] + (1).to_bytes(4, "little") + audio[32:])]
        for mutation in mutations:
            self.mutate = mutation
            with self.subTest(mutation=mutation), self.assertRaises((ProviderError, StoreError)):
                self.synthesize()
            self.assertEqual(list(self.store.generation_root.iterdir()), [])
            self.assertEqual(list(self.store.consent_root.iterdir()), [])
            self.assertEqual(self.replies[-1].descriptors, ())

    def test_typed_request_refuses_conflicting_conditioning(self):
        invalid = [{"mode": "named_voice"}, {"mode": "voice_design"},
                   {"mode": "prompt_clone", "voice_id": "Vivian"},
                   {"mode": "named_voice", "voice_id": "Vivian", "description": "warm"},
                   {"mode": "voice_design", "description": "warm", "consent_asserted": True},
                   {"mode": "named_voice", "voice_id": "Vivian", "seed": True},
                   {"mode": "named_voice", "voice_id": "Vivian", "seed": 2**53},
                   {"mode": "named_voice", "voice_id": "Vivian", "seed": 2**63}]
        for extra in invalid:
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                models.GenerationRequest(profile_id="profile", text="hello", **extra)
        self.assertEqual(models.GenerationRequest(profile_id="profile", text="hello").mode, "prompt_clone")

    def test_random_and_explicit_seeds_remain_exact_browser_integers(self):
        from unittest.mock import patch
        maximum = 2**53 - 1
        arguments = dict(mode="named_voice", text="Hello there.", language="en", voice_id="Vivian")
        with patch("kilix_voicebox_backend.orchestrator.secrets.randbits", return_value=maximum) as random:
            result = self.orchestrator.synthesize_generated(**arguments)
        random.assert_called_once_with(53)
        self.assertEqual(result["seed"], maximum)
        self.assertEqual(self.store.generation_provenance(result["generation_id"])["seed"], maximum)
        self.assertEqual(int(float(result["seed"])), maximum)
        self.assertEqual(models.GenerationRequest(profile_id="profile", text="hello", seed=maximum).seed, maximum)
        with self.assertRaises(ProviderError) as caught:
            self.orchestrator.synthesize_generated(**arguments, seed=2**53)
        self.assertEqual(caught.exception.code, "INVALID_SEED")

    def test_changed_header_after_inspection_refuses_named_design_and_clone(self):
        good = wav_bytes()
        bad = good[:28] + (1).to_bytes(4, "little") + good[32:]
        for mode in ("named_voice", "voice_design", "prompt_clone"):
            with self.subTest(mode=mode):
                path = self.root / "mutable-result.wav"
                path.write_bytes(good)
                writer = os.open(path, os.O_RDWR | os.O_CLOEXEC)
                reader = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
                path.unlink()
                model, revision = ("qwen3-tts-0.6b-customvoice", "85e237c12c027371202489a0ec509ded67b5e4b5")
                if mode == "voice_design":
                    model, revision = "qwen3-tts-1.7b-voicedesign", "5ecdb67327fd37bb2e042aab12ff7391903235d3"
                result = {"engine_id": "qwen3-tts", "engine_revision": "6cafe5582caea83df269c36b1ce62d953a9cc66b",
                          "model_id": model, "model_revision": revision, "duration_ms": 10, "seed": 7,
                          "audio": {"sha256": hashlib.sha256(bad).hexdigest(), "byte_length": len(bad)}}
                reply = ProviderReply({"type": "result", "result": result}, (reader,))
                self.replies.append(reply)
                commit = self.store.commit_generation
                def change_before_copy(**kwargs):
                    self.assertEqual(os.pwrite(writer, bad, 0), len(bad))
                    return commit(**kwargs)
                try:
                    with patch.object(self.store, "commit_generation", side_effect=change_before_copy):
                        if mode == "prompt_clone":
                            sample = self.root / "sample.wav"
                            sample.write_bytes(good)
                            consent = self.store.attest_sample(profile_id="fixture", sample_path=sample,
                                                              asserted_by_user=True)
                            result["conditioning"] = {"prompt_sha256": consent.source_sha256,
                                                       "consent_sha256": consent.sha256}
                            call = lambda: self.orchestrator._commit_synthesis_reply(
                                reply, generation_id="clone-fixture", job_id="clone-job",
                                source_sha256=consent.source_sha256, consent_sha256=consent.sha256, seed=7)
                        else:
                            self.qwen.request.side_effect = lambda *args: reply
                            call = lambda: self.synthesize(mode)
                        with self.assertRaises(StoreError) as caught:
                            call()
                    self.assertEqual(caught.exception.code, "OUTPUT_FORMAT")
                    self.assertEqual(list(self.store.generation_root.iterdir()), [])
                    self.assertEqual(reply.descriptors, ())
                    self.assertFalse(list(self.store.consent_root.glob("*.derived-output.json")))
                finally:
                    os.close(writer)

    def test_copied_header_validation_handles_short_reads_and_refuses_growth(self):
        good = wav_bytes()
        class ShortSource(io.BytesIO):
            def read(self, count):
                return super().read(min(count, 7))
        for index, payload in enumerate((good, good[:28] + (1).to_bytes(4, "little") + good[32:], good + b"extra")):
            with self.subTest(index=index):
                provenance = GeneratedVoiceProvenance.create(
                    generation_id=f"short-{index}", job_id=f"job-{index}", provider="kilix-qwen-tts",
                    model_id="qwen3-tts-0.6b-customvoice", model_revision="85e237c12c027371202489a0ec509ded67b5e4b5",
                    engine_id="qwen3-tts", engine_revision="6cafe5582caea83df269c36b1ce62d953a9cc66b",
                    output_sha256=hashlib.sha256(payload).hexdigest(), output_bytes=len(good),
                    mode="named_voice", request_sha256="0" * 64, seed=7)
                source = ShortSource(payload)
                if index == 0:
                    destination = self.store.commit_generation(source=source, provenance=provenance,
                                                               expected_prefix=good[:44])
                    self.assertEqual(destination.read_bytes(), good)
                else:
                    with self.assertRaises(StoreError):
                        self.store.commit_generation(source=source, provenance=provenance,
                                                     expected_prefix=good[:44])
                    self.assertFalse((self.store.generation_root / f"short-{index}.wav").exists())
                self.assertLessEqual(source.tell(), len(good) + 1)
                self.assertFalse(list(self.store.generation_root.glob("*.partial")))

    def test_generated_route_never_resolves_or_reads_a_profile_sample(self):
        from kilix_voicebox_backend.main import generate_speech
        for mode in ["named_voice", "voice_design"]:
            data = models.GenerationRequest(profile_id="collection", text="hello", mode=mode,
                                            **({"voice_id": "Vivian"} if mode == "named_voice"
                                               else {"description": "A warm voice"}))
            generated = {"stored_name": "generation.wav", "duration_ms": 10, "seed": 7,
                         "generation_id": "generation"}
            engine = Mock()
            engine.synthesize_generated.return_value = generated
            with patch("kilix_voicebox_backend.main.profiles.get_profile", new=AsyncMock(return_value=Mock())), \
                 patch("kilix_voicebox_backend.main.profiles.create_voice_prompt_for_profile", new=AsyncMock(side_effect=AssertionError("must not read"))), \
                 patch("kilix_voicebox_backend.main._voice_orchestrator", return_value=engine), \
                 patch("kilix_voicebox_backend.main.history.create_generation", new=AsyncMock(return_value={"id": "generation"})):
                self.assertEqual(asyncio.run(generate_speech(data, Mock())), {"id": "generation"})
            engine.synthesize_clone.assert_not_called()
            self.assertEqual(engine.synthesize_generated.call_args.kwargs["mode"], mode)


if __name__ == "__main__":
    unittest.main()

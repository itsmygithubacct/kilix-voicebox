from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest

from kilix_voicebox_backend.kilix_provider import ProviderError, ProviderReply
from kilix_voicebox_backend.orchestrator import _transcript_result


class TranscriptDescriptorTests(unittest.TestCase):
    def document(self):
        return {"engine": {"id": "whisper.cpp"}, "model": {"id": "whisper-tiny"},
                "model_revision": "revision", "duration_ms": 100,
                "text": "speech " * 12000, "segments": [],
                "output_format": "text", "output": "speech " * 12000}

    def reply(self, payload, *, digest=None, writable=False):
        with tempfile.TemporaryFile() as writer:
            writer.write(payload)
            writer.flush()
            flags = os.O_RDWR if writable else os.O_RDONLY
            fd = os.open(f"/proc/self/fd/{writer.fileno()}", flags | os.O_CLOEXEC)
        result = {"transcript": {"fd": 0, "byte_length": len(payload),
                                 "sha256": digest or hashlib.sha256(payload).hexdigest()},
                  "engine_id": "whisper.cpp", "model_id": "whisper-tiny",
                  "model_revision": "revision", "duration_ms": 100}
        return ProviderReply({"type": "result", "result": result}, (fd,))

    def test_large_transcript_is_read_without_storage_and_fd_is_closed(self):
        document = self.document()
        with self.reply(json.dumps(document).encode()) as reply:
            fd = reply.descriptors[0]
            self.assertEqual(_transcript_result(reply), document)
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_mismatched_digest_and_writable_descriptor_refuse(self):
        for options in ({"digest": "0" * 64}, {"writable": True}):
            with self.subTest(options=options):
                with self.reply(json.dumps(self.document()).encode(), **options) as reply:
                    with self.assertRaises(ProviderError) as caught:
                        _transcript_result(reply)
                    self.assertEqual(caught.exception.code, "DESCRIPTOR_MISMATCH")

    def test_control_and_document_identity_must_match(self):
        with self.reply(json.dumps(self.document()).encode()) as reply:
            reply.payload["result"]["model_id"] = "different-model"
            with self.assertRaises(ProviderError) as caught:
                _transcript_result(reply)
            self.assertEqual(caught.exception.code, "INVALID_RESPONSE")

    def test_duplicate_keys_nonfinite_and_deep_json_refuse(self):
        for payload in (b'{"key":1,"key":2}', b'{"key":NaN}',
                        b'{"key":' + b'[' * 17 + b'0' + b']' * 17 + b'}'):
            with self.subTest(payload=payload):
                with self.reply(payload) as reply:
                    with self.assertRaises(ProviderError):
                        _transcript_result(reply)

    def test_missing_and_extra_descriptor_refuse(self):
        with self.reply(json.dumps(self.document()).encode()) as reply:
            fd = reply.descriptors[0]
            for descriptors in ((), (fd, fd)):
                with self.subTest(descriptors=descriptors):
                    invalid = ProviderReply(reply.payload, descriptors)
                    with self.assertRaises(ProviderError) as caught:
                        _transcript_result(invalid)
                    self.assertEqual(caught.exception.code, "DESCRIPTOR_MISMATCH")


if __name__ == "__main__":
    unittest.main()

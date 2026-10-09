"""Small pre-parser controls, including chunked input and real receive polling."""
import asyncio
import json
import unittest
from unittest.mock import patch

from starlette.requests import Request
from kilix_voicebox_backend import main
from kilix_voicebox_backend.upload_limits import BoundedBody, UploadBudget, UploadRefusal


def multipart(data=b'1234', fields=(), files=1):
    parts = []
    for index in range(files):
        parts.append(b'--test\r\nContent-Disposition: form-data; name="file"; filename="x.wav"\r\n'
                     b'Content-Type: audio/wav\r\n\r\n' + data + b'\r\n')
    for name, value in fields:
        parts.append(b'--test\r\nContent-Disposition: form-data; name="' + name + b'"\r\n\r\n' + value + b'\r\n')
    return b''.join(parts) + b'--test--\r\n'


def scope(path, headers=()):
    return {'type': 'http', 'method': 'POST', 'path': path, 'raw_path': path.encode(),
            'query_string': b'', 'scheme': 'http', 'server': ('127.0.0.1', 1),
            'client': ('127.0.0.1', 2), 'http_version': '1.1',
            'headers': [(main.SESSION_HEADER.encode(), b'1' * 64), *headers]}


class UploadBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, path, payload, chunks=(), delay=0, content_length=None):
        headers = [(b'content-type', b'multipart/form-data; boundary=test')]
        if content_length is not None:
            headers.append((b'content-length', str(content_length).encode()))
        messages = ([{'type': 'http.request', 'body': data, 'more_body': index < len(chunks) - 1}
                     for index, data in enumerate(chunks)] if chunks else
                    [{'type': 'http.request', 'body': payload, 'more_body': False}])
        parsed = []
        sent = []
        async def receive():
            if delay:
                await asyncio.sleep(delay)
            return messages.pop(0) if messages else {'type': 'http.disconnect'}
        async def app(actual_scope, actual_receive, send):
            request = Request(actual_scope, actual_receive)
            async with request.form() as form:
                for _name, value in form.multi_items():
                    if hasattr(value, 'read'):
                        parsed.append(await value.read())
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})
        main.configure_session_token('1' * 64)
        with patch.object(main, 'MAX_SAMPLE_BYTES', 8), patch.object(main, 'MAX_PROFILE_ARCHIVE_BYTES', 8), \
             patch.object(main, 'MAX_AVATAR_BYTES', 8), patch.object(main, 'MAX_TRANSCRIPTION_UPLOAD_BYTES', 8):
            await main.ProductGuard(app)(scope(path, headers), receive, self.send_to(sent))
        return parsed, sent

    @staticmethod
    def send_to(items):
        async def send(message):
            items.append(message)
        return send

    async def test_oversized_file_never_reaches_framework_parser_on_any_upload_route(self):
        for path in ('/profiles/import', '/profiles/p/samples', '/profiles/p/avatar', '/transcribe'):
            with self.subTest(path=path):
                parsed, sent = await self.invoke(path, multipart(b'x' * 64))
                self.assertEqual(parsed, [])
                self.assertEqual(sent[0]['status'], 413)

    async def test_chunked_oversize_is_refused_before_framework_spooling(self):
        data = multipart(b'x' * 64)
        parsed, sent = await self.invoke('/transcribe', b'', chunks=(data[:43], data[43:89], data[89:]))
        self.assertEqual(parsed, [])
        self.assertEqual(sent[0]['status'], 413)

    async def test_legitimate_chunked_file_and_metadata_reach_actual_framework_parser(self):
        data = multipart(b'12345678', fields=((b'language', b'en'),))
        parsed, sent = await self.invoke('/transcribe', b'', chunks=tuple(data[i:i+7] for i in range(0, len(data), 7)))
        self.assertEqual(parsed, [b'12345678'])
        self.assertEqual(sent[0]['status'], 200)

    async def test_extra_file_parts_and_metadata_population_refuse_before_parser(self):
        for data in (multipart(files=2), multipart(files=0, fields=tuple((b'x', b'v') for _ in range(17)))):
            parsed, sent = await self.invoke('/profiles/import', data)
            self.assertEqual(parsed, [])
            self.assertEqual(sent[0]['status'], 413)

    async def test_large_field_or_header_refuses_before_framework_parser(self):
        with patch('kilix_voicebox_backend.upload_limits.MAX_FIELD_BYTES', 8):
            parsed, sent = await self.invoke('/transcribe', multipart(files=0, fields=((b'language', b'x' * 64),)))
        self.assertEqual(parsed, [])
        self.assertEqual(sent[0]['status'], 413)
        with patch('kilix_voicebox_backend.upload_limits.MAX_HEADER_BYTES', 8):
            parsed, sent = await self.invoke('/transcribe', multipart())
        self.assertEqual(parsed, [])
        self.assertEqual(sent[0]['status'], 413)

    async def test_elapsed_ingestion_deadline_refuses_before_framework_parser(self):
        with patch.object(main, 'UPLOAD_BODY_SECONDS', .01):
            parsed, sent = await self.invoke('/profiles/import', multipart(), delay=.05)
        self.assertEqual(parsed, [])
        self.assertEqual(sent[0]['status'], 408)

    async def test_declared_oversize_refuses_without_consuming_file(self):
        parsed, sent = await self.invoke('/profiles/import', multipart(), content_length=2_000_000)
        self.assertEqual(parsed, [])
        self.assertEqual(sent[0]['status'], 413)

    async def test_disconnect_polling_uses_original_receive_after_body_replay(self):
        messages = [{'type': 'http.request', 'body': b'{}', 'more_body': False}]
        async def receive():
            return messages.pop(0) if messages else {'type': 'http.disconnect'}
        request_scope = scope('/generate')
        body = BoundedBody(request_scope, receive, UploadBudget(8, 1))
        try:
            await body.ingest()
            request = Request(request_scope, body.receive)
            self.assertEqual(await request.body(), b'{}')
            self.assertTrue(await request.is_disconnected())
        finally:
            body.close()

    async def test_partial_multipart_is_refused_before_parser(self):
        parsed, sent = await self.invoke('/profiles/import', multipart()[:-12])
        self.assertEqual(parsed, [])
        self.assertEqual(sent[0]['status'], 400)

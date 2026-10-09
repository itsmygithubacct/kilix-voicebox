"""Draft regression source: real request entry/parser, no provider or audio work.

Execute only after the storage hold is released, under the unchanged contained
runner with private HOME/XDG/store/TMP. No method has been run in the draft phase.
"""
import asyncio
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from starlette.requests import Request
from kilix_voicebox_backend import main, database, upload_limits as limits

TOKEN = '1' * 64
CONTENT_TYPE = b'multipart/form-data; boundary=test'


def multipart(*, files=1, payload=b'x', fields=(), boundary=b'test', extra_header=b''):
    parts = [(b'--' + boundary + b'\r\nContent-Disposition: form-data; name="file"; filename="x.zip"\r\n'
              b'Content-Type: application/zip\r\n' + extra_header + b'\r\n' + payload + b'\r\n')
             for _ in range(files)]
    parts.extend(b'--' + boundary + b'\r\nContent-Disposition: form-data; name="' + name
                 + b'"\r\n\r\n' + value + b'\r\n' for name, value in fields)
    return b''.join(parts) + b'--' + boundary + b'--\r\n'


def request_scope(path, headers, token=TOKEN):
    return {'type': 'http', 'asgi': {'version': '3.0'}, 'method': 'POST', 'path': path,
        'raw_path': path.encode(), 'root_path': '', 'query_string': b'', 'scheme': 'http',
        'server': ('127.0.0.1', 25417), 'client': ('127.0.0.1', 1), 'http_version': '1.1',
        'headers': [(main.SESSION_HEADER.encode(), token.encode()), *headers]}


def result(messages):
    status = next(message['status'] for message in messages if message['type'] == 'http.response.start')
    body = b''.join(message.get('body', b'') for message in messages if message['type'] == 'http.response.body')
    return status, json.loads(body) if body else {}


class IngestionEntryTests(unittest.IsolatedAsyncioTestCase):
    async def probe(self, headers, body=b'{}', *, path='/profiles/import', chunks=None,
                    token=TOKEN, no_read=False, file_limit=8):
        incoming = list(chunks) if chunks is not None else [body]
        observations = {'reads': 0, 'framework_entries': 0, 'files': [], 'fields': []}
        sent = []
        async def receive():
            observations['reads'] += 1
            if no_read:
                raise AssertionError('refusal consumed a request body')
            if not incoming:
                return {'type': 'http.disconnect'}
            chunk = incoming.pop(0)
            return {'type': 'http.request', 'body': chunk, 'more_body': bool(incoming)}
        async def send(message):
            sent.append(message)
        async def parsed_app(scope, receive, send):
            observations['framework_entries'] += 1
            async with Request(scope, receive).form() as form:
                for name, value in form.multi_items():
                    if hasattr(value, 'read'):
                        observations['files'].append(await value.read())
                    else:
                        observations['fields'].append((name, value))
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'{}'})
        with patch.object(main, '_session_token', TOKEN), \
                patch.object(main, 'MAX_PROFILE_ARCHIVE_BYTES', file_limit), \
                patch.object(main, 'MAX_SAMPLE_BYTES', file_limit), \
                patch.object(main, 'MAX_AVATAR_BYTES', file_limit), \
                patch.object(main, 'MAX_TRANSCRIPTION_UPLOAD_BYTES', file_limit):
            if no_read:
                with patch.object(limits.tempfile, 'SpooledTemporaryFile', side_effect=AssertionError('pre-refusal spool')), \
                        patch.object(limits, 'MultipartParser', side_effect=AssertionError('pre-refusal parser')):
                    await main.ProductGuard(parsed_app)(request_scope(path, headers, token), receive, send)
            else:
                await main.ProductGuard(parsed_app)(request_scope(path, headers, token), receive, send)
        return result(sent), observations

    async def test_duplicate_ingestion_headers_refuse_before_body_parser_or_spool(self):
        cases = [
            [(b'content-type', CONTENT_TYPE), (b'content-type', b'application/json')],
            [(b'content-type', b'application/json'), (b'content-type', CONTENT_TYPE)],
            [(b'content-type', CONTENT_TYPE)] * 2,
            [(b'Content-Type', CONTENT_TYPE)],
            [(b'content-type', CONTENT_TYPE), (b'CONTENT-TYPE', b'application/json')],
            [(b'content-length', b'2')] * 2,
            [(b'content-length', b'2'), (b'Content-Length', b'3')],
            [(b'transfer-encoding', b'chunked')] * 2,
            [(b'transfer-encoding', b'chunked'), (b'content-length', b'2')],
        ]
        for path in ('/profiles/import', '/profiles/p/samples', '/profiles/p/avatar', '/transcribe', '/generate'):
            for headers in cases:
                with self.subTest(path=path, headers=headers):
                    (status, body), observed = await self.probe(headers, no_read=True, path=path)
                    self.assertEqual((status, body['detail']['code']), (400, 'UPLOAD_STRUCTURE'))
                    self.assertEqual(observed['reads'], 0)
                    self.assertEqual(observed['framework_entries'], 0)

    async def test_invalid_content_type_framing_and_length_refuse_before_read(self):
        structure = [b'', b'multipart', b'multipart /form-data', b'application/json, multipart/form-data',
                     b'application/json\r\ncontent-type: multipart/form-data', b'application/\0json']
        cases = [([(b'content-type', value)], 400, 'UPLOAD_STRUCTURE') for value in structure]
        cases += [([(b'transfer-encoding', value)], 400, 'UPLOAD_STRUCTURE')
                  for value in (b'', b'gzip', b'gzip, chunked', b'chunked, chunked')]
        cases += [([(b'content-length', value)], 400, 'INPUT_SIZE')
                  for value in (b'', b'-1', b'+1', b' 1 ', b'1,1', b'1.0', b'1 0', b'\xb2')]
        cases += [([(b'content-length', b'9' * 5000)], 413, 'INPUT_SIZE')]
        cases += [([(b'content-type', CONTENT_TYPE), *headers], status, code)
                  for headers, status, code in tuple(cases)
                  if headers[0][0] == b'content-length']
        for headers, expected, code in cases:
            with self.subTest(headers=headers):
                (status, body), observed = await self.probe(headers, no_read=True)
                self.assertEqual((status, body['detail']['code']), (expected, code))
                self.assertEqual(observed['framework_entries'], 0)

    async def test_valid_media_case_whitespace_quoted_parameters_and_chunking(self):
        data = multipart(payload=b'12345678', fields=((b'language', b'en'),))
        for value in (CONTENT_TYPE, b'MULTIPART/FORM-DATA; BOUNDARY="test"',
                      b' \tMultipart/Form-Data ; boundary="test"; charset=utf-8',
                      b'multipart/form-data; boundary=unused; boundary=test'):
            with self.subTest(content_type=value):
                (status, _), observed = await self.probe(
                    [(b'content-type', value), (b'transfer-encoding', b' CHUNKED ')],
                    chunks=[data[index:index+7] for index in range(0, len(data), 7)])
                self.assertEqual(status, 200)
                self.assertEqual(observed['files'], [b'12345678'])
                self.assertEqual(observed['fields'], [('language', 'en')])
        # The dependency also caps individual lines at 4224 bytes. Keep each
        # line below that ceiling while exercising our combined 8 KiB budget.
        headers = b'X-First: ' + b'a' * 4000 + b'\r\nX-Second: ' + b'b' * 4000 + b'\r\n'
        (status, _), observed = await self.probe([(b'content-type', CONTENT_TYPE)], multipart(extra_header=headers))
        self.assertEqual(status, 200)
        self.assertEqual(observed['files'], [b'x'])

    async def test_population_field_header_and_file_ceilings_precede_framework(self):
        cases = [(multipart(files=2), 'UPLOAD_POPULATION'),
                 (multipart(files=0, fields=tuple((b'x', b'v') for _ in range(17))), 'UPLOAD_POPULATION'),
                 (multipart(files=0, fields=((b'x', b'a' * (64 * 1024 + 1)),)), 'INPUT_SIZE'),
                 (multipart(extra_header=b'X-First: ' + b'a' * 4096 + b'\r\nX-Second: ' + b'b' * 4096 + b'\r\n'), 'UPLOAD_HEADERS'),
                 (multipart(payload=b'123456789'), 'INPUT_SIZE')]
        for data, code in cases:
            with self.subTest(code=code):
                (status, body), observed = await self.probe([(b'content-type', CONTENT_TYPE)],
                    chunks=[data[index:index+19] for index in range(0, len(data), 19)])
                self.assertEqual((status, body['detail']['code']), (413, code))
                self.assertEqual(observed['framework_entries'], 0)

    async def test_part_header_token_case_and_duplicate_part_headers(self):
        data = (b'--test\r\ncOnTeNt-DiSpOsItIoN: FORM-DATA; NAME="file"; FILENAME="x.zip"\r\n'
                b'cOnTeNt-TyPe: application/zip\r\n\r\nx\r\n--test--\r\n')
        (status, _), observed = await self.probe([(b'content-type', CONTENT_TYPE)], data)
        self.assertEqual(status, 200)
        self.assertEqual(observed['files'], [b'x'])
        for extra in (b'Content-Disposition: form-data; name="file"; filename="y.zip"\r\n',
                      b'cOnTeNt-TyPe: application/zip\r\n'):
            (status, body), observed = await self.probe([(b'content-type', CONTENT_TYPE)],
                                                       multipart(extra_header=extra))
            self.assertEqual((status, body['detail']['code']), (400, 'UPLOAD_STRUCTURE'))
            self.assertEqual(observed['framework_entries'], 0)

    async def test_boundary_ceiling_and_exact_token_classification(self):
        for value in (b'multipart/form-data', b'multipart/form-data; boundary=',
                      b'multipart/form-data; boundary=' + b'B' * 201):
            (status, body), _ = await self.probe([(b'content-type', value)], no_read=True)
            self.assertEqual((status, body['detail']['code']), (400, 'UPLOAD_STRUCTURE'))
        boundary = b'B' * 200
        (status, _), observed = await self.probe([(b'content-type', b'multipart/form-data; boundary=' + boundary)],
            multipart(boundary=boundary))
        self.assertEqual(status, 200)
        self.assertEqual(observed['files'], [b'x'])
        # Starlette does not parse this different, valid media subtype as form-data.
        (status, _), observed = await self.probe([(b'content-type', b'multipart/form-data-extra')], b'{}')
        self.assertEqual(status, 200)
        self.assertEqual(observed['files'], [])

    async def test_json_no_content_type_and_leading_zero_lengths_remain_bounded(self):
        for content in ([], [(b'content-type', b'application/json')]):
            (status, _), _ = await self.probe([*content, (b'content-length', b'00000002')], b'{}')
            self.assertEqual(status, 200)
            (status, body), observed = await self.probe(content, b'x' * 9)
            self.assertEqual((status, body['detail']['code']), (413, 'INPUT_SIZE'))
            self.assertEqual(observed['framework_entries'], 0)

    async def test_authentication_and_origin_still_precede_ingestion(self):
        ambiguous = [(b'content-type', CONTENT_TYPE), (b'content-type', b'application/json')]
        for token, extra, expected in [('wrong', [], 401),
                ('wrong', [(main.SESSION_HEADER.encode(), TOKEN.encode())], 401),
                (TOKEN, [(b'origin', b'https://foreign.invalid')], 403)]:
            (status, _), observed = await self.probe([*ambiguous, *extra], token=token, no_read=True)
            self.assertEqual(status, expected)
            self.assertEqual(observed['reads'], 0)

    async def test_deadline_disconnect_and_chunked_aggregate_bytes_are_retained(self):
        async def blocked():
            await asyncio.Future()
        with patch.object(main, '_session_token', TOKEN), patch.object(main, 'UPLOAD_BODY_SECONDS', .01):
            sent = []
            async def send(message):
                sent.append(message)
            async def unreachable(*args):
                raise AssertionError('deadline reached framework')
            await main.ProductGuard(unreachable)(request_scope('/profiles/import', []), blocked, send)
            self.assertEqual(result(sent)[0], 408)
        messages = [{'type': 'http.request', 'body': b'{}', 'more_body': False}]
        async def receive():
            return messages.pop(0) if messages else {'type': 'http.disconnect'}
        body = limits.BoundedBody(request_scope('/generate', [(b'content-type', b'application/json')]),
                                  receive, limits.UploadBudget(8, 1))
        try:
            await body.ingest()
            request = Request(request_scope('/generate', []), body.receive)
            self.assertEqual(await request.body(), b'{}')
            self.assertTrue(await request.is_disconnected())
        finally:
            body.close()
        (status, payload), observed = await self.probe([(b'transfer-encoding', b'chunked')],
            chunks=[b'12345', b'6789'])
        self.assertEqual((status, payload['detail']['code']), (413, 'INPUT_SIZE'))
        self.assertEqual(observed['framework_entries'], 0)


class ActualImportHTTPTests(unittest.TestCase):
    """Actual loopback Uvicorn/FastAPI, literal bytes, private SQLite; no Popen.

    The exact owned server thread/listener are joined/closed, with no process
    discovery, signalling or native launch. Lifecycle/provider startup is off.
    """
    def setUp(self):
        import uvicorn
        self.fixture = Path(tempfile.mkdtemp(prefix='fix2-http-'))
        # Retain the private input/SQLite root for the future evidence recorder.
        print(json.dumps({'fixture': str(self.fixture), 'test': self.id(), 'native_launch': False}))
        self.contexts = [patch.object(main, '_session_token', TOKEN),
            patch.object(main.config, '_data_dir', self.fixture),
            patch.multiple(database, engine=None, SessionLocal=None, _db_path=None)]
        for context in self.contexts:
            context.start()
            self.addCleanup(context.stop)
        database.init_db()
        self.addCleanup(database.engine.dispose)
        self.listener = socket.socket()
        self.addCleanup(self.listener.close)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(8)
        self.port = self.listener.getsockname()[1]
        (self.fixture / 'HTTP-INPUTS.json').write_text(json.dumps({'port': self.port,
            'private_loopback': True, 'actual_app': True, 'no_process_or_signal_dispatch': True}) + '\n')
        self.errors = []
        self.server = uvicorn.Server(uvicorn.Config(main.app, host='127.0.0.1', port=self.port,
            lifespan='off', loop='asyncio', http='h11', access_log=False, log_config=None))
        def serve():
            try:
                asyncio.run(self.server.serve(sockets=[self.listener]))
            except BaseException as error:
                self.errors.append(error)
        self.thread = threading.Thread(target=serve, name='fix2-owned-http')
        self.thread.start()
        self.addCleanup(self.stop_server)
        deadline = time.monotonic() + 5
        while not self.server.started:
            if self.errors:
                raise self.errors[0]
            if not self.thread.is_alive() or time.monotonic() >= deadline:
                self.fail('owned private HTTP readiness failed')
            time.sleep(.01)
        (self.fixture / 'READINESS.json').write_text(json.dumps({'started': True,
            'thread': self.thread.name, 'limit_seconds': 5}) + '\n')

    def stop_server(self):
        self.server.should_exit = True
        self.thread.join(5)
        (self.fixture / 'CLEANUP.json').write_text(json.dumps({'owned_thread_stopped': not self.thread.is_alive(),
            'errors': [type(error).__name__ for error in self.errors]}) + '\n')
        self.assertFalse(self.thread.is_alive(), 'owned HTTP thread did not stop')
        self.assertEqual(self.errors, [])

    def post(self, content_types, body, *, chunked=False):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            connection.putrequest('POST', '/profiles/import')
            connection.putheader('X-Kilix-Voicebox-Session', TOKEN)
            for name, value in content_types:
                connection.putheader(name, value)
            connection.putheader('Transfer-Encoding' if chunked else 'Content-Length',
                                 'chunked' if chunked else str(len(body)))
            connection.putheader('Connection', 'close')
            connection.endheaders()
            if chunked:
                data = b''.join(f'{len(body[i:i+13]):x}\r\n'.encode() + body[i:i+13] + b'\r\n'
                                for i in range(0, len(body), 13)) + b'0\r\n\r\n'
            else:
                data = body
            connection.send(data)
            response = connection.getresponse()
            payload = response.read(8193)
            self.assertLessEqual(len(payload), 8192)
            with (self.fixture / 'HTTP-OBSERVATIONS.jsonl').open('a') as stream:
                stream.write(json.dumps({'content_types': content_types, 'body_bytes': len(body),
                    'chunked': chunked, 'status': response.status, 'body': payload.decode()}) + '\n')
            return response.status, json.loads(payload)
        finally:
            connection.close()

    def test_actual_224_byte_two_file_refusal_and_duplicate_header_entry_refusal(self):
        body = multipart(files=2)
        self.assertEqual(len(body), 224)
        original = Request._get_form
        parsed = []
        async def observe(request, *args, **kwargs):
            parsed.append(True)
            return await original(request, *args, **kwargs)
        with patch.object(Request, '_get_form', observe):
            for chunked in (False, True):
                status, data = self.post([('Content-Type', CONTENT_TYPE.decode())], body, chunked=chunked)
                self.assertEqual((status, data['detail']['code']), (413, 'UPLOAD_POPULATION'))
                for names in (('Content-Type', 'Content-Type'), ('Content-Type', 'cOnTeNt-TyPe')):
                    for values in ((CONTENT_TYPE.decode(), 'application/json'),
                                   ('application/json', CONTENT_TYPE.decode()),
                                   (CONTENT_TYPE.decode(), CONTENT_TYPE.decode())):
                        status, data = self.post(list(zip(names, values)), body, chunked=chunked)
                        self.assertEqual((status, data['detail']['code']), (400, 'UPLOAD_STRUCTURE'))
            self.assertEqual(parsed, [], 'framework parsed a refused ingestion')
            # This format failure is only a positive parser reachability control.
            # It is never counted as evidence of a pre-parser security refusal.
            status, data = self.post([('cOnTeNt-TyPe', 'MULTIPART/FORM-DATA; boundary="test"')], multipart())
            self.assertEqual((status, data['detail']['code']), (400, 'PROFILE_ARCHIVE_INVALID'))
            self.assertEqual(len(parsed), 1)


if __name__ == '__main__':
    unittest.main()

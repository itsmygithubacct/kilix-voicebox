"""Bound ingestion before framework parsing; retain the actual disconnect channel."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
import tempfile
import time

from python_multipart import MultipartParser
from python_multipart.multipart import parse_options_header

MAX_PARTS = 16
MAX_FILES = 1
MAX_FIELD_BYTES = 64 * 1024
MAX_HEADER_BYTES = 8 * 1024
MAX_BOUNDARY_BYTES = 200
MAX_METADATA_BYTES = MAX_PARTS * (MAX_FIELD_BYTES + MAX_HEADER_BYTES + MAX_BOUNDARY_BYTES)
MEDIA_TYPE = re.compile(rb"[!#$%&'*+\-.^_`|~0-9A-Za-z]+/[!#$%&'*+\-.^_`|~0-9A-Za-z]+")


class UploadRefusal(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


@dataclass(frozen=True)
class UploadBudget:
    file_bytes: int
    seconds: float


def ingestion_headers(scope):
    """Reject ambiguity before either our spool or the framework sees a body.

    ASGI requires lowercase header names. Starlette reads the first matching
    entry without normalizing raw names, so silently folding names or choosing
    a duplicate here would give the two parsers different authorities. Wire
    header casing is normalized by the HTTP server before this boundary.
    """
    headers = {}
    for key, value in scope.get('headers', []):
        lower = key.lower()
        if lower not in {b'content-type', b'content-length', b'transfer-encoding'}:
            continue
        if key != lower or lower in headers or b'\r' in value or b'\n' in value or b'\0' in value:
            raise UploadRefusal(400, 'UPLOAD_STRUCTURE')
        headers[lower] = value
    if b'transfer-encoding' in headers:
        if b'content-length' in headers or headers[b'transfer-encoding'].strip(b' \t').lower() != b'chunked':
            raise UploadRefusal(400, 'UPLOAD_STRUCTURE')
    content_type = headers.get(b'content-type', b'')
    kind, _options = parse_options_header(content_type)
    kind = kind.lower()
    if b'content-type' in headers and not MEDIA_TYPE.fullmatch(kind):
        raise UploadRefusal(400, 'UPLOAD_STRUCTURE')
    if b'content-type' in headers:
        # The retained multipart parser preserves MIME-token case when options
        # are present. Normalize the token once for both consumers, retaining
        # the exact parameter bytes (boundary values remain case-sensitive).
        _token, separator, parameters = content_type.partition(b';')
        canonical = kind + separator + parameters
        headers[b'content-type'] = canonical
        scope['headers'] = [(key, canonical if key == b'content-type' else value)
                            for key, value in scope['headers']]
    return headers, kind


class MultipartPopulation:
    """Inspect parts without materializing file payloads or unbounded headers."""
    def __init__(self, content_type, maximum):
        self.maximum = maximum
        self.parts = self.files = 0
        self.ended = False
        try:
            kind, options = parse_options_header(content_type)
            boundary = options.get(b'boundary', b'')
            if kind != b'multipart/form-data' or not 1 <= len(boundary) <= MAX_BOUNDARY_BYTES:
                raise ValueError('invalid multipart boundary')
            self.parser = MultipartParser(boundary, callbacks={
                'on_part_begin': self.begin,
                'on_header_field': self.header_field,
                'on_header_value': self.header_value,
                'on_header_end': self.header_end,
                'on_headers_finished': self.headers_finished,
                'on_part_data': self.data,
                'on_end': self.end,
            })
        except (ValueError, TypeError):
            raise UploadRefusal(400, 'UPLOAD_STRUCTURE') from None

    def begin(self):
        self.parts += 1
        if self.parts > MAX_PARTS:
            raise UploadRefusal(413, 'UPLOAD_POPULATION')
        self.header_size = self.part_size = 0
        self.name, self.value = bytearray(), bytearray()
        self.headers = {}
        self.is_file = False

    def header_field(self, data, start, end):
        self.header_size += end - start
        if self.header_size > MAX_HEADER_BYTES:
            raise UploadRefusal(413, 'UPLOAD_HEADERS')
        self.name.extend(data[start:end])

    def header_value(self, data, start, end):
        self.header_size += end - start
        if self.header_size > MAX_HEADER_BYTES:
            raise UploadRefusal(413, 'UPLOAD_HEADERS')
        self.value.extend(data[start:end])

    def header_end(self):
        key = bytes(self.name).lower()
        if key in self.headers:
            raise UploadRefusal(400, 'UPLOAD_STRUCTURE')
        self.headers[key] = bytes(self.value)
        self.name.clear()
        self.value.clear()

    def headers_finished(self):
        kind, options = parse_options_header(self.headers.get(b'content-disposition', b''))
        if kind.lower() != b'form-data' or not 1 <= len(options.get(b'name', b'')) <= 128:
            raise UploadRefusal(400, 'UPLOAD_STRUCTURE')
        self.is_file = b'filename' in options
        if self.is_file:
            self.files += 1
            if self.files > MAX_FILES:
                raise UploadRefusal(413, 'UPLOAD_POPULATION')

    def data(self, data, start, end):
        self.part_size += end - start
        if self.part_size > (self.maximum if self.is_file else MAX_FIELD_BYTES):
            raise UploadRefusal(413, 'INPUT_SIZE')

    def end(self):
        self.ended = True

    def write(self, data):
        try:
            self.parser.write(data)
        except UploadRefusal:
            raise
        except (ValueError, TypeError):
            raise UploadRefusal(400, 'UPLOAD_STRUCTURE') from None

    def finish(self):
        self.parser.finalize()
        if not self.ended:
            raise UploadRefusal(400, 'UPLOAD_STRUCTURE')


class BoundedBody:
    """One bounded private spool before FastAPI can create its upload spools.

    The spool is limited by the route's file allowance plus bounded metadata.
    After replaying the final body message, receive awaits the original channel
    directly, without a timeout/task wrapper that could conceal disconnects.
    """
    def __init__(self, scope, receive, budget):
        self.original_receive = receive
        self.started = time.monotonic()
        self.deadline = self.started + budget.seconds
        scope['kilix.body_started_at'] = self.started
        headers, kind = ingestion_headers(scope)
        content_type = headers.get(b'content-type', b'')
        is_multipart = kind == b'multipart/form-data'
        self.maximum = budget.file_bytes + (MAX_METADATA_BYTES if is_multipart else 0)
        if b'content-length' in headers:
            length = headers[b'content-length']
            if not length or any(byte < 48 or byte > 57 for byte in length):
                raise UploadRefusal(400, 'INPUT_SIZE') from None
            # Compare decimal bytes, avoiding int's digit limit on hostile
            # lengths while accepting ordinary decimal values/leading zeros.
            length = length.lstrip(b'0') or b'0'
            maximum = str(self.maximum).encode('ascii')
            if len(length) > len(maximum) or (len(length) == len(maximum) and length > maximum):
                raise UploadRefusal(413, 'INPUT_SIZE')
        self.multipart = MultipartPopulation(content_type, budget.file_bytes) if is_multipart else None
        self.spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
        self.total = self.position = 0
        self.body_delivered = False
        self.disconnected = False

    def check_deadline(self):
        if time.monotonic() >= self.deadline:
            raise UploadRefusal(408, 'UPLOAD_DEADLINE_EXCEEDED')

    async def ingest(self):
        while True:
            self.check_deadline()
            try:
                async with asyncio.timeout(self.deadline - time.monotonic()):
                    message = await self.original_receive()
            except TimeoutError:
                raise UploadRefusal(408, 'UPLOAD_DEADLINE_EXCEEDED') from None
            if message['type'] == 'http.disconnect':
                self.disconnected = True
                break
            if message['type'] != 'http.request':
                raise UploadRefusal(400, 'UPLOAD_STRUCTURE')
            data = message.get('body', b'')
            self.total += len(data)
            if self.total > self.maximum:
                raise UploadRefusal(413, 'INPUT_SIZE')
            if self.multipart:
                self.multipart.write(data)
            self.check_deadline()
            self.spool.write(data)
            if not message.get('more_body', False):
                if self.multipart:
                    self.multipart.finish()
                break
        self.spool.seek(0)

    async def receive(self):
        if self.body_delivered:
            return await self.original_receive()
        if self.disconnected:
            self.body_delivered = True
            return {'type': 'http.disconnect'}
        self.check_deadline()
        data = self.spool.read(64 * 1024)
        self.position += len(data)
        more = self.position < self.total
        if not more:
            self.body_delivered = True
        return {'type': 'http.request', 'body': data, 'more_body': more}

    def close(self):
        self.spool.close()

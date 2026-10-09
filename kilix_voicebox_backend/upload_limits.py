"""Bound ingestion before framework parsing; retain the actual disconnect channel."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
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


class UploadRefusal(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


@dataclass(frozen=True)
class UploadBudget:
    file_bytes: int
    seconds: float


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
        if kind != b'form-data' or not 1 <= len(options.get(b'name', b'')) <= 128:
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
        headers = {key.lower(): value for key, value in scope.get('headers', [])}
        content_type = headers.get(b'content-type', b'')
        self.multipart = (MultipartPopulation(content_type, budget.file_bytes)
                          if content_type.lower().startswith(b'multipart/form-data') else None)
        self.maximum = budget.file_bytes + (MAX_METADATA_BYTES if self.multipart else 0)
        if b'content-length' in headers:
            try:
                length = int(headers[b'content-length'])
            except ValueError:
                raise UploadRefusal(400, 'INPUT_SIZE') from None
            if not 0 <= length <= self.maximum:
                raise UploadRefusal(413, 'INPUT_SIZE')
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

"""Actual authenticated HTTP disconnect/control over a separate synthetic backend."""
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid

from .test_managed_capture import PEER, wait_for


class CaptureHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fd_before = len(os.listdir('/proc/self/fd'))
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.token = secrets.token_hex(32)
        with socket.socket() as port:
            port.bind(('127.0.0.1', 0))
            self.port = port.getsockname()[1]
        environment = {**os.environ, 'KILIX_VOICEBOX_SESSION_TOKEN': self.token,
                       'XDG_RUNTIME_DIR': str(self.root)}
        self.log = tempfile.TemporaryFile()
        self.server = subprocess.Popen([sys.executable, '-B', PEER, 'server', str(self.root), str(self.port)],
            env=environment, stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log)
        self.sockets = []
        def ready():
            if self.server.poll() is not None:
                self.log.seek(0)
                self.fail(self.log.read().decode())
            try:
                return self.request('/recording/status')[0] == 200
            except OSError:
                return False
        wait_for(ready, 6)

    def tearDown(self):
        for connection in self.sockets:
            connection.close()
        self.server.terminate()
        self.server.wait(6)
        self.log.close()
        self.temp.cleanup()
        self.assertEqual(len(os.listdir('/proc/self/fd')), self.fd_before)

    def request(self, path, *, method='GET', token=True, origin=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        headers = {'X-Kilix-Voicebox-Session': self.token} if token else {}
        if origin:
            headers['Origin'] = origin
        try:
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def begin(self):
        identifier = uuid.uuid4().hex
        body = json.dumps({'id': identifier, 'seconds': 3}).encode()
        connection = socket.create_connection(('127.0.0.1', self.port), timeout=4)
        self.sockets.append(connection)
        connection.sendall((f'POST /recording HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'
            f'X-Kilix-Voicebox-Session: {self.token}\r\nContent-Type: application/json\r\n'
            f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode() + body)
        wait_for(lambda: (self.root / 'tree.json').exists())
        tree = json.loads((self.root / 'tree.json').read_text())
        wait_for(lambda: self.request('/recording/status')[1]['duration'] > 0)
        return identifier, connection, tree

    def completed(self, identifier, tree):
        wait_for(lambda: self.request('/recording/status')[1]['last_id'] == identifier)
        self.assertEqual(self.request('/recording/status')[1]['phase'], 'inactive')
        for pid in tree.values():
            self.assertFalse(Path(f'/proc/{pid}').exists())

    def response(self, connection):
        response = http.client.HTTPResponse(connection)
        try:
            response.begin()
            return response.status, response.read()
        finally:
            response.close()

    def test_auth_origin_and_idle_status_never_open_microphone(self):
        self.assertEqual(self.request('/recording/status', token=False)[0], 401)
        self.assertEqual(self.request('/recording/status', origin='https://invalid.example')[0], 403)
        self.assertEqual(self.request('/recording/status')[1]['phase'], 'inactive')
        self.assertFalse((self.root / 'tree.json').exists())
        self.assertFalse((self.root / 'leases').exists())

    def test_stop_proof_precedes_wav_and_stale_id_refuses(self):
        identifier, connection, tree = self.begin()
        self.assertEqual(self.request('/recording/status')[1]['id'], identifier)
        self.assertEqual(self.request('/recording/' + 'f' * 32 + '/cancel', method='POST')[0], 409)
        code, state = self.request(f'/recording/{identifier}/stop', method='POST')
        self.assertEqual((code, state['phase']), (200, 'stopping'))
        code, payload = self.response(connection)
        self.assertEqual(code, 200)
        self.assertEqual(payload[:4], b'RIFF')
        self.completed(identifier, tree)
        self.assertFalse(list((self.root / 'data/privacy').glob('profiles/*/samples/*')))

    def test_original_http_disconnect_reaps_and_discards(self):
        identifier, connection, tree = self.begin()
        connection.close()
        self.completed(identifier, tree)

    def test_cancel_does_not_deliver_recorded_audio(self):
        identifier, connection, tree = self.begin()
        self.assertEqual(self.request(f'/recording/{identifier}/cancel', method='POST')[0], 200)
        code, payload = self.response(connection)
        self.assertEqual(code, 409)
        self.assertNotIn(b'RIFF', payload)
        self.assertNotIn(str(self.root).encode(), payload)
        self.completed(identifier, tree)

    def test_actual_lock_authority_change_stops_existing_request(self):
        identifier, connection, tree = self.begin()
        (self.root / 'locked').touch()
        self.assertEqual(self.response(connection)[0], 409)
        self.completed(identifier, tree)

    def test_natural_limit_stops_without_a_control_client(self):
        identifier, connection, tree = self.begin()
        self.assertEqual(self.response(connection)[0], 200)
        self.completed(identifier, tree)


if __name__ == '__main__':
    unittest.main()

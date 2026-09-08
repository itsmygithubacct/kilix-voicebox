"""Actual authenticated HTTP disconnect/control over a separate synthetic backend.

Fixture correction (C24, independently confirmed defect): `setUp` created a
temporary tree, a log descriptor and a server child and only then waited for
readiness. `unittest` does not run `tearDown` after a `setUp` failure and no
`addCleanup` was registered, so a readiness timeout left the server, its
descendants, the log descriptor and the tree alive -- the surviving tasks the
C21 full run observed. The timeout also retained nothing: `ready` surfaces the
log only when the server has already exited.

Each resource is now registered for cleanup **as it is created**, so the same
teardown runs whether `setUp` finishes or fails. Registration order is the
reverse of the original teardown order, and cleanups run last-in-first-out, so
success performs exactly the original sequence -- sockets, server, log, tree,
then the descriptor-count assertion.

The six-second readiness limit, every assertion, the request timeouts and the
skip behaviour are unchanged. Nothing about the original timeout's cause is
claimed here; on failure the fixture now merely keeps what it saw.
"""
import http.client
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from .test_managed_capture import PEER, wait_for

# Bounded retained diagnostics. A readiness failure writes one JSON file here
# and names it on stderr; the directory is outside the fixture's own temporary
# tree, because that tree is exactly what cleanup removes.
DIAGNOSTIC_DIR = os.environ.get('VOICEBOX_HTTP_DIAGNOSTIC_DIR')
MAX_LOG_BYTES = 64 * 1024


class CaptureHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fd_before = len(os.listdir('/proc/self/fd'))
        # Registered first, so it runs last: the original invariant, asserted
        # after every other resource has been released. It asserts only when
        # setUp completed, so a readiness failure reports its own cause rather
        # than a secondary descriptor difference measured mid-failure.
        self.setup_complete = False
        self.addCleanup(self._assert_descriptor_balance)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.token = secrets.token_hex(32)
        with socket.socket() as port:
            port.bind(('127.0.0.1', 0))
            self.port = port.getsockname()[1]
        environment = {**os.environ, 'KILIX_VOICEBOX_SESSION_TOKEN': self.token,
                       'XDG_RUNTIME_DIR': str(self.root)}
        self.log = tempfile.TemporaryFile()
        self.addCleanup(self.log.close)
        self.server = subprocess.Popen([sys.executable, '-B', PEER, 'server', str(self.root), str(self.port)],
            env=environment, stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log)
        self.addCleanup(self._stop_server)
        self.sockets = []
        self.addCleanup(self._close_sockets)
        self.readiness = {'attempts': [], 'seconds': None, 'limit_seconds': 6}
        started = time.monotonic()
        def ready():
            if self.server.poll() is not None:
                # F1: the early-exit failure message surfaces the same bytes the
                # retained record does, so it goes through the same bounded,
                # redacting reader. A failure message is what gets pasted into a
                # report, so it is the louder of the two paths, not the safer.
                self.fail(self.read_server_log().decode('utf-8', 'replace'))
            try:
                status = self.request('/recording/status')[0]
            except OSError as error:
                self.readiness['attempts'].append(
                    {'at': round(time.monotonic() - started, 6),
                     'error': type(error).__name__})
                return False
            self.readiness['attempts'].append(
                {'at': round(time.monotonic() - started, 6), 'status': status})
            return status == 200
        try:
            wait_for(ready, 6)
        except BaseException:
            # Keep what this failure actually saw. The exception is re-raised
            # unchanged: nothing is suppressed, converted or retried.
            self.readiness['seconds'] = time.monotonic() - started
            # F2: secondary diagnostics are best effort and must never displace
            # the failure they exist to explain. The helper is written not to
            # raise, and this guard is the second line of that promise: a
            # fallback may fail too, and it must not win either. What happened
            # is recorded rather than hidden.
            try:
                self.readiness['diagnostics'] = self._retain_readiness_context()
            except BaseException as error:
                self.readiness['diagnostics'] = {
                    'written': False, 'error': type(error).__name__,
                    'stage': 'retain_readiness_context'}
                try:
                    print('readiness diagnostics failed: %s' % type(error).__name__,
                          file=sys.stderr)
                except BaseException:
                    pass
            raise
        self.readiness['seconds'] = time.monotonic() - started
        self.setup_complete = True

    def _close_sockets(self):
        for connection in self.sockets:
            connection.close()

    def _owned_descendants(self):
        """Live descendants of this fixture's own server, by /proc parentage.

        Only processes reachable from this fixture's own child are considered,
        so an unrelated process is never signalled.
        """
        parents = {}
        for entry in os.listdir('/proc'):
            if not entry.isdigit():
                continue
            try:
                with open(f'/proc/{entry}/stat', 'rb') as handle:
                    fields = handle.read().rpartition(b') ')[2].split()
                parents[int(entry)] = int(fields[1])
            except (OSError, IndexError, ValueError):
                continue
        owned, frontier = [], [self.server.pid]
        while frontier:
            current = frontier.pop()
            for pid, parent in parents.items():
                if parent == current and pid not in owned:
                    owned.append(pid)
                    frontier.append(pid)
        return owned

    def _stop_server(self):
        descendants = self._owned_descendants()
        self.server.terminate()
        try:
            self.server.wait(6)
        except subprocess.TimeoutExpired:
            self.server.kill()
            self.server.wait(6)
        # A setUp failure can leave the server's own children behind, because
        # the ordinary stop path that reaps them never ran. Only pids descended
        # from this fixture's server are touched.
        for pid in descendants:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                continue
        deadline = time.monotonic() + 6
        for pid in descendants:
            while time.monotonic() < deadline and Path(f'/proc/{pid}').exists():
                time.sleep(0.01)
            if Path(f'/proc/{pid}').exists():
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        self.stopped_descendants = descendants

    def _assert_descriptor_balance(self):
        after = len(os.listdir('/proc/self/fd'))
        self.descriptor_balance = (self.fd_before, after)
        if self.setup_complete:
            self.assertEqual(after, self.fd_before)

    def read_server_log(self, limit=MAX_LOG_BYTES):
        """The server log, bounded and token-redacted, for every caller.

        F1: the failure message and the retained record surface the same bytes,
        so the bound and the redaction belong to the log itself rather than to
        one of its readers. Slightly more than the bound is read and redacted
        before trimming, so a token lying across the boundary is still removed.
        Replacements keep their original width: shortening an earlier token
        must not shift a partially read later token into the retained prefix.
        Arbitrary bytes are returned as bytes; callers decode with `replace`.
        """
        marker = self.token.encode('ascii')
        try:
            self.log.seek(0)
            data = self.log.read(limit + len(marker))
        except (OSError, ValueError):
            return b''
        replacement = b'<redacted-session-token>'.ljust(len(marker), b' ')
        return data.replace(marker, replacement)[:limit]

    def _retain_readiness_context(self):
        """Best effort: write one bounded, token-free record. Never raises.

        F2: this runs while the real setup failure is in flight, so every step
        is guarded and the return value reports honestly what was achieved. A
        caller must be able to ignore the result without losing its exception.
        """
        status = {'written': False}
        try:
            log_bytes = self.read_server_log()
            try:
                descendants = self._owned_descendants()
            except BaseException as error:
                descendants = None
                status['descendants_error'] = type(error).__name__
            context = {
                'test': self.id(),
                'readiness_limit_seconds': self.readiness['limit_seconds'],
                'readiness_seconds': round(self.readiness['seconds'], 6),
                'request_attempts': self.readiness['attempts'][-64:],
                'request_attempt_count': len(self.readiness['attempts']),
                'server_pid': self.server.pid,
                'server_returncode': self.server.poll(),
                'server_alive': self.server.poll() is None,
                'owned_descendants': descendants,
                'port': self.port,
                'server_log_bytes': len(log_bytes),
                'server_log': log_bytes.decode('utf-8', 'replace'),
                'descriptors_at_setup_start': self.fd_before,
                'descriptors_now': len(os.listdir('/proc/self/fd')),
            }
            payload = json.dumps(context, indent=2, sort_keys=True, default=repr) + '\n'
            directory = DIAGNOSTIC_DIR or tempfile.mkdtemp(prefix='c24-http-readiness-')
            os.makedirs(directory, exist_ok=True)
            name = self.id().rsplit('.', 1)[-1]
            path = Path(directory) / f'readiness-{name}-{os.getpid()}.json'
            path.write_text(payload, encoding='utf-8')
            self.readiness_context_path = path
            status.update(written=True, path=str(path))
        except BaseException as error:
            status.update(written=False, error=type(error).__name__)
        # Even saying so is best effort; a failing stream must not win either.
        try:
            if status['written']:
                print('retained readiness diagnostics: %s' % status['path'],
                      file=sys.stderr)
            else:
                print('readiness diagnostics not retained: %s'
                      % status.get('error', 'unknown'), file=sys.stderr)
        except BaseException as error:
            status['report_error'] = type(error).__name__
        return status

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

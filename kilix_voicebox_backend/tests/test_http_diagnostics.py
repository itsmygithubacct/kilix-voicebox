"""Deterministic controls for the HTTP fixture's diagnostics (C26 F1/F2).

These drive the **actual** `CaptureHTTPTests` class from its own unmodified
source. Nothing here runs a real backend, a listening peer, completed HTTP, a
provider, a model or a timed replay, and nothing installs, resolves or
materialises an environment: only the existing standard library, a private
HOME/TMPDIR, and owned subprocesses of a few stdlib lines.
The fixture still binds a loopback port and attempts loopback readiness
connections. Refused connections are not a claim of zero networking.

**Disclosed substitutions**, both narrow and both outside the behaviour under
test:

1. The reviewed file's only sibling import is
   ``from .test_managed_capture import PEER, wait_for``. That sibling imports
   ``voicelib``, which standalone controls need not import or install. Rather
   than fabricate it, the sibling's **own** ``PEER`` assignment
   and ``wait_for`` function are lifted verbatim from its unmodified source by
   AST extraction and executed in a private, nonregistered module, with ``__file__`` set to
   the real sibling so ``PEER`` resolves as it really does. Neither is
   reimplemented or given different behaviour. The unchanged actual fixture
   source then executes in another private, nonregistered module. Its private
   builtins dictionary intercepts only that exact relative sibling import;
   every other import delegates unchanged. This is not a canonical import and
   creates/replaces no canonical module-cache entry or package attribute.
2. ``PEER`` is then pointed at an owned peer of a few stdlib lines that never
   listens, so readiness is decided by this control rather than by traffic.

The class, its ``setUp``, its ``ready``, its cleanups and its diagnostics run
untouched. The six real test bodies are never executed: each control runs the
fixture for its setup behaviour only, which is where both findings live.
"""

from __future__ import annotations

import ast
import builtins
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest


TESTS = Path(__file__).resolve().parent
SIBLING = TESTS / 'test_managed_capture.py'
SIBLING_MODULE = 'kilix_voicebox_backend.tests.test_managed_capture'
HTTP_FIXTURE = TESTS / 'test_capture_http.py'

# A peer that echoes its session token and exits: the early-server-exit path.
PEER_ECHO_TOKEN = '''import os, sys
sys.stdout.write("token=" + os.environ["KILIX_VOICEBOX_SESSION_TOKEN"] + "\\n")
sys.stdout.flush()
raise SystemExit(3)
'''

# A peer that floods far past the read bound with undecodable bytes and the
# token, then exits: the oversized/malformed path.
PEER_FLOOD = '''import os, sys
token = os.environ["KILIX_VOICEBOX_SESSION_TOKEN"].encode()
out = sys.stdout.buffer
out.write(b"\\xff\\xfe not utf-8 " + token + b"\\n")
out.write(b"\\xc3\\x28" * 4096)
out.write(b"A" * (256 * 1024))
out.write(token + b"\\n")
out.flush()
raise SystemExit(3)
'''

# A peer that stays alive and never listens: the readiness-timeout path.
PEER_SILENT = '''import time
time.sleep(120)
'''


def load_actual_fixture():
    """Execute the unchanged fixture privately; never register a fake sibling."""
    tree = ast.parse(SIBLING.read_text(encoding='utf-8'))
    wanted = [node for node in tree.body
              if (isinstance(node, ast.Assign) and len(node.targets) == 1
                  and isinstance(node.targets[0], ast.Name)
                  and node.targets[0].id == 'PEER')
              or (isinstance(node, ast.FunctionDef) and node.name == 'wait_for')]
    if (len(wanted) != 2
            or sum(isinstance(node, ast.Assign) for node in wanted) != 1
            or sum(isinstance(node, ast.FunctionDef) for node in wanted) != 1):
        raise AssertionError('expected exactly PEER and wait_for from the sibling')
    helpers = types.ModuleType(__name__ + '._private_helpers')
    helpers.__file__ = str(SIBLING)
    helpers.__dict__.update({'Path': Path, 'time': time})
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(SIBLING), 'exec', dont_inherit=True),
         helpers.__dict__)

    actual = types.ModuleType(__name__ + '._private_http_fixture')
    actual.__file__ = str(HTTP_FIXTURE)
    actual.__package__ = __package__
    original_import = builtins.__import__

    def private_import(name, globals=None, locals=None, fromlist=(), level=0):
        if (globals is actual.__dict__ and name == 'test_managed_capture'
                and level == 1 and fromlist == ('PEER', 'wait_for')):
            return helpers
        return original_import(name, globals, locals, fromlist, level)

    actual.__dict__['__builtins__'] = dict(vars(builtins), __import__=private_import)
    exec(compile(HTTP_FIXTURE.read_text(encoding='utf-8'), str(HTTP_FIXTURE), 'exec', dont_inherit=True),
         actual.__dict__)
    return actual


fixture = load_actual_fixture()


class LogPrefixBoundaryTests(unittest.TestCase):
    def test_earlier_redactions_cannot_pull_in_a_later_token_fragment(self):
        # No setup or peer: exercise the actual reader on a controlled log.
        # A token beginning AFTER the output boundary can be only partly read.
        # Shortening earlier tokens must not pull that raw fragment back in.
        token = b'0123456789abcdef' * 4
        limit = fixture.MAX_LOG_BYTES
        for earlier_tokens in (0, 1, 2, 4):
            for offset in (-20, -1, 0, 1, 20, 63, 64):
                with self.subTest(earlier_tokens=earlier_tokens, offset=offset):
                    start = limit + offset
                    payload = (token * earlier_tokens
                               + b'Z' * (start - len(token) * earlier_tokens)
                               + token + b'END')
                    case = fixture.CaptureHTTPTests(
                        'test_auth_origin_and_idle_status_never_open_microphone')
                    case.token = token.decode('ascii')
                    with io.BytesIO(payload) as log:
                        case.log = log
                        observed = case.read_server_log()
                    self.assertLessEqual(len(observed), limit)
                    for size in (1, 8, 16, 24, 32, 44, 64):
                        self.assertNotIn(token[:size], observed)


class ActualFixtureControl(unittest.TestCase):
    """Run the real class for its setup behaviour and read the outcome."""

    def setUp(self):
        self.workdir = tempfile.TemporaryDirectory(prefix='c26-http-')
        self.addCleanup(self.workdir.cleanup)
        self.root = Path(self.workdir.name)
        self.original_peer = fixture.PEER
        self.original_dir = fixture.DIAGNOSTIC_DIR
        self.addCleanup(setattr, fixture, 'PEER', self.original_peer)
        self.addCleanup(setattr, fixture, 'DIAGNOSTIC_DIR', self.original_dir)

    def peer(self, source):
        path = self.root / 'peer.py'
        path.write_text(source, encoding='utf-8')
        fixture.PEER = str(path)
        return path

    def drive(self, method='test_auth_origin_and_idle_status_never_open_microphone'):
        """Run the actual fixture once; return (case, result)."""
        case = fixture.CaptureHTTPTests(method)
        result = unittest.TestResult()
        case.run(result)
        self.addCleanup(self.reap, case)
        return case, result

    def reap(self, case):
        server = getattr(case, 'server', None)
        if server is not None and server.poll() is None:
            server.kill()
            try:
                server.wait(6)
            except subprocess.TimeoutExpired:
                pass

    def outcome(self, result):
        rows = list(result.failures) + list(result.errors)
        self.assertEqual(len(rows), 1, 'expected exactly one reported problem')
        return rows[0][1]

    def assertReleased(self, case):
        """Every owned resource is gone, whichever way setup ended."""
        self.assertFalse(Path(case.temp.name).exists(), 'the temporary tree survived')
        self.assertTrue(case.log.closed, 'the log descriptor survived')
        self.assertIsNotNone(case.server.poll(), 'the server child survived')


class EarlyExitRedactionTests(ActualFixtureControl):
    """F1 -- the early-exit failure message goes through the bounded reader."""

    def test_the_token_never_reaches_the_failure_message(self):
        self.peer(PEER_ECHO_TOKEN)
        case, result = self.drive()
        text = self.outcome(result)
        self.assertIn('token=', text, 'the useful diagnostic was lost')
        self.assertNotIn(case.token, text, 'the session token reached the failure text')
        self.assertIn('<redacted-session-token>', text)
        self.assertReleased(case)

    def test_oversized_and_undecodable_log_is_bounded_and_safe(self):
        self.peer(PEER_FLOOD)
        case, result = self.drive()
        text = self.outcome(result)
        # Bounded: decoding N bytes with 'replace' yields at most N characters,
        # so the character count is the honest bound. Comparing re-encoded
        # bytes would not be: one undecodable byte becomes a three-byte
        # replacement character, which inflates the total without any extra log
        # content reaching the message.
        self.assertLessEqual(len(text), fixture.MAX_LOG_BYTES + 4096)
        # The peer wrote far past the bound, so the bound did something.
        self.assertLess(text.count('A'), 256 * 1024)
        # Safe: undecodable bytes did not raise; they were replaced.
        self.assertIn('�', text)
        self.assertNotIn(case.token, text)
        self.assertIn('<redacted-session-token>', text)
        self.assertReleased(case)

    def test_both_paths_use_one_reader(self):
        # The retained record and the failure message must agree, so a future
        # change cannot redact one and not the other.
        self.peer(PEER_ECHO_TOKEN)
        directory = self.root / 'diagnostics'
        fixture.DIAGNOSTIC_DIR = str(directory)
        case, result = self.drive()
        self.assertNotIn(case.token, self.outcome(result))
        # The early-exit path fails before the readiness guard, so no record is
        # written; what this binds is that neither path can expose the token.
        for path in directory.glob('*.json') if directory.exists() else []:
            self.assertNotIn(case.token, path.read_text(encoding='utf-8'))


class DiagnosticFailureTests(ActualFixtureControl):
    """F2 -- no diagnostics failure may displace the primary setup failure."""

    PRIMARY = 'owned observation timed out'

    def assertPrimarySurvived(self, result):
        text = self.outcome(result)
        self.assertIn(self.PRIMARY, text)
        # The primary must be the raised exception, not a chained context.
        self.assertNotIn('During handling of the above exception', text)
        return text

    def test_normal_retention_is_bounded_and_redacted(self):
        self.peer(PEER_SILENT)
        directory = self.root / 'diagnostics'
        fixture.DIAGNOSTIC_DIR = str(directory)
        case, result = self.drive()
        self.assertPrimarySurvived(result)
        self.assertTrue(case.readiness['diagnostics']['written'])
        records = list(directory.glob('*.json'))
        self.assertEqual(len(records), 1)
        record = json.loads(records[0].read_text(encoding='utf-8'))
        self.assertLessEqual(record['server_log_bytes'], fixture.MAX_LOG_BYTES)
        self.assertLessEqual(len(record['request_attempts']), 64)
        self.assertNotIn(case.token, records[0].read_text(encoding='utf-8'))
        self.assertReleased(case)

    def test_a_diagnostic_directory_that_is_a_file_does_not_displace(self):
        self.peer(PEER_SILENT)
        blocker = self.root / 'not-a-directory'
        blocker.write_text('regular file', encoding='utf-8')
        fixture.DIAGNOSTIC_DIR = str(blocker)
        case, result = self.drive()
        self.assertPrimarySurvived(result)
        self.assertFalse(case.readiness['diagnostics']['written'])
        self.assertIn(case.readiness['diagnostics']['error'],
                      ('FileExistsError', 'NotADirectoryError'))
        self.assertReleased(case)

    def test_a_serialization_failure_does_not_displace(self):
        self.peer(PEER_SILENT)
        fixture.DIAGNOSTIC_DIR = str(self.root / 'diagnostics')
        original = fixture.json.dumps

        def refuse(*_args, **_kwargs):
            raise TypeError('controlled serialization failure')

        fixture.json.dumps = refuse
        self.addCleanup(setattr, fixture.json, 'dumps', original)
        case, result = self.drive()
        self.assertPrimarySurvived(result)
        self.assertFalse(case.readiness['diagnostics']['written'])
        self.assertEqual(case.readiness['diagnostics']['error'], 'TypeError')
        self.assertReleased(case)

    def test_a_failing_stderr_does_not_displace(self):
        self.peer(PEER_SILENT)
        fixture.DIAGNOSTIC_DIR = str(self.root / 'diagnostics')

        class Broken:
            def write(self, _text):
                raise OSError('controlled stderr failure')

            def flush(self):
                raise OSError('controlled stderr failure')

        original = sys.stderr
        sys.stderr = Broken()
        self.addCleanup(setattr, sys, 'stderr', original)
        case, result = self.drive()
        sys.stderr = original
        self.assertPrimarySurvived(result)
        # The write succeeded; only saying so failed, and it was recorded.
        self.assertTrue(case.readiness['diagnostics']['written'])
        self.assertEqual(case.readiness['diagnostics']['report_error'], 'OSError')
        self.assertReleased(case)

    def test_a_helper_that_raises_outright_does_not_displace(self):
        # The guard of last resort: even if the helper's own promise not to
        # raise is broken by a later change, the primary failure still wins.
        self.peer(PEER_SILENT)
        original = fixture.CaptureHTTPTests._retain_readiness_context

        def explode(_self):
            raise RuntimeError('controlled diagnostics failure')

        fixture.CaptureHTTPTests._retain_readiness_context = explode
        self.addCleanup(setattr, fixture.CaptureHTTPTests,
                        '_retain_readiness_context', original)
        case, result = self.drive()
        self.assertPrimarySurvived(result)
        self.assertEqual(case.readiness['diagnostics'],
                         {'written': False, 'error': 'RuntimeError',
                          'stage': 'retain_readiness_context'})
        self.assertReleased(case)

    def test_cleanup_still_runs_after_every_diagnostic_failure(self):
        self.peer(PEER_SILENT)
        blocker = self.root / 'blocker'
        blocker.write_text('x', encoding='utf-8')
        fixture.DIAGNOSTIC_DIR = str(blocker)
        case, result = self.drive()
        self.assertPrimarySurvived(result)
        self.assertReleased(case)
        # The descriptor count was recorded, and no owned descendant survives.
        self.assertEqual(len(case.descriptor_balance), 2)
        for pid in getattr(case, 'stopped_descendants', []):
            self.assertFalse(Path(f'/proc/{pid}').exists())


class SourceBindingTests(unittest.TestCase):
    """Bind exactly what was substituted, so the disclosure stays true."""

    def test_the_sibling_helpers_are_the_real_ones(self):
        actual = load_actual_fixture()
        source = SIBLING.read_text(encoding='utf-8')
        tree = ast.parse(source)
        real = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == 'wait_for')
        lifted = ast.parse(''.join(source.splitlines(keepends=True)[
            real.lineno - 1:real.end_lineno])).body[0]
        self.assertEqual(ast.dump(real), ast.dump(lifted))
        expected = {'__file__': str(SIBLING), 'Path': Path, 'time': time}
        exec(compile(ast.Module(body=[real], type_ignores=[]), str(SIBLING), 'exec', dont_inherit=True),
             expected)
        self.assertEqual(actual.wait_for.__code__, expected['wait_for'].__code__)
        self.assertEqual(actual.wait_for.__code__.co_filename, str(SIBLING))
        self.assertEqual(actual.wait_for.__globals__['__file__'], str(SIBLING))
        # PEER really is the sibling's own expression, resolved against it.
        self.assertEqual(actual.PEER, str(SIBLING.with_name('capture_peer.py')))
        self.assertEqual(actual.wait_for.__globals__['PEER'], actual.PEER)
        observed = []
        actual.wait_for(lambda: (observed.append(True), True)[1])
        self.assertEqual(observed, [True])

    def test_no_voicelib_was_fabricated(self):
        before = {name: value for name, value in sys.modules.copy().items()
                  if name == 'voicelib' or name.startswith('voicelib.')}
        load_actual_fixture()
        after = {name: value for name, value in sys.modules.copy().items()
                 if name == 'voicelib' or name.startswith('voicelib.')}
        self.assertEqual(set(before), set(after))
        for name, value in before.items():
            self.assertIs(after[name], value)


if __name__ == '__main__':
    unittest.main()

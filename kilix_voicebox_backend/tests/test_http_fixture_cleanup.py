"""Paired controls for the HTTP fixture's failure-safe cleanup (C24).

The confirmed defect is a *fixture* defect: `unittest` does not run `tearDown`
after a `setUp` failure, so a fixture that creates a child process and then
waits for readiness leaks that child when the wait fails. These controls prove
the mechanism and the correction as a pair, on the same construction sequence.

They deliberately need no `voicelib`, no HTTP peer, no provider and no model:
the leak is caused by cleanup registration, not by anything the real server
does. The real file is bound separately by a source-ordering control, so the
pair cannot pass while the real fixture still has the defect.

Nothing here claims a cause for the original six-second readiness timeouts.
"""
import ast
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


HTTP_FIXTURE = Path(__file__).with_name('test_capture_http.py')

# One fixture shape, run in a child interpreter, in two variants. The readiness
# wait always fails, so `setUp` always raises; the only difference is whether
# cleanup was registered as each resource was created.
HARNESS = '''
import json, os, subprocess, sys, tempfile, time, unittest

REGISTER = os.environ["REGISTER_CLEANUP"] == "1"
REPORT = os.environ["REPORT_PATH"]
state = {}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.fd_before = len(os.listdir("/proc/self/fd"))
        self.temp = tempfile.TemporaryDirectory()
        if REGISTER:
            self.addCleanup(self.temp.cleanup)
        self.log = tempfile.TemporaryFile()
        if REGISTER:
            self.addCleanup(self.log.close)
        # A child that outlives an unclean fixture exactly as the server would.
        self.server = subprocess.Popen(
            [sys.executable, "-c",
             "import signal,time\\nsignal.signal(signal.SIGTERM, signal.SIG_DFL)\\n"
             "time.sleep(120)"],
            stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log)
        if REGISTER:
            self.addCleanup(self._stop)
        state["pid"] = self.server.pid
        state["temp"] = self.temp.name
        # Readiness never succeeds: this is the injected setup failure.
        raise AssertionError("owned observation timed out")

    def _stop(self):
        self.server.terminate()
        try:
            self.server.wait(6)
        except subprocess.TimeoutExpired:
            self.server.kill()
            self.server.wait(6)

    def tearDown(self):
        state["teardown_ran"] = True

    def test_case(self):
        state["body_ran"] = True


result = unittest.TextTestRunner(stream=open(os.devnull, "w")).run(
    unittest.defaultTestLoader.loadTestsFromTestCase(Fixture))
state["failures"] = len(result.failures) + len(result.errors)
state["alive_after"] = os.path.exists("/proc/%d" % state["pid"])
state["temp_exists"] = os.path.exists(state["temp"])
json.dump(state, open(REPORT, "w"))
'''


class FixtureCleanupPairTests(unittest.TestCase):
    """The same setup failure, with and without cleanup registration."""

    def arm(self, register):
        workdir = tempfile.TemporaryDirectory(prefix='c24-cleanup-')
        self.addCleanup(workdir.cleanup)
        report = Path(workdir.name) / 'state.json'
        script = Path(workdir.name) / 'harness.py'
        script.write_text(HARNESS, encoding='utf-8')
        environment = dict(os.environ, REGISTER_CLEANUP='1' if register else '0',
                           REPORT_PATH=str(report), PYTHONDONTWRITEBYTECODE='1')
        completed = subprocess.run([sys.executable, '-B', str(script)], env=environment,
                                   capture_output=True, text=True, timeout=120)
        self.assertTrue(report.exists(), completed.stderr)
        import json as _json
        state = _json.loads(report.read_text())
        self.addCleanup(self.reap, state['pid'])
        return state

    def reap(self, pid):
        """Leave nothing of this control's own behind, whatever it proved."""
        for attempt in (signal.SIGTERM, signal.SIGKILL):
            if not Path(f'/proc/{pid}').exists():
                return
            try:
                os.kill(pid, attempt)
            except OSError:
                return
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and Path(f'/proc/{pid}').exists():
                time.sleep(0.01)

    def test_unregistered_cleanup_leaks_the_child_and_the_tree(self):
        state = self.arm(register=False)
        # The setup failure is reported and the body never runs -- both shapes.
        self.assertEqual(state['failures'], 1)
        self.assertNotIn('body_ran', state)
        # unittest skipped tearDown, so nothing released the child or the tree.
        self.assertNotIn('teardown_ran', state)
        self.assertTrue(state['alive_after'],
                        'the defect did not reproduce: the child did not survive')
        self.assertTrue(state['temp_exists'])

    def test_registered_cleanup_releases_the_child_and_the_tree(self):
        state = self.arm(register=True)
        self.assertEqual(state['failures'], 1)
        self.assertNotIn('body_ran', state)
        self.assertNotIn('teardown_ran', state)
        # The correction: cleanups registered at creation run anyway.
        self.assertFalse(state['alive_after'],
                         'the corrected shape still leaked its child')
        self.assertFalse(state['temp_exists'])


class HTTPFixtureRegistrationTests(unittest.TestCase):
    """Bind the real file: every resource is registered before the next one.

    This reads the actual `test_capture_http.py` source, so the pair above
    cannot pass while the real fixture still creates a resource without
    registering its release.
    """

    def setUp(self):
        self.tree = ast.parse(HTTP_FIXTURE.read_text(encoding='utf-8'))
        self.setup = next(
            node for klass in self.tree.body
            if isinstance(klass, ast.ClassDef) and klass.name == 'CaptureHTTPTests'
            for node in klass.body
            if isinstance(node, ast.FunctionDef) and node.name == 'setUp')

    def registrations(self):
        return [node.lineno for node in ast.walk(self.setup)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'addCleanup']

    def assignment_line(self, attribute):
        for node in ast.walk(self.setup):
            if (isinstance(node, ast.Assign) and node.targets
                    and isinstance(node.targets[0], ast.Attribute)
                    and node.targets[0].attr == attribute):
                return node.lineno
        self.fail('no assignment to self.' + attribute)

    def test_every_owned_resource_registers_cleanup_before_the_next(self):
        registrations = self.registrations()
        self.assertGreaterEqual(len(registrations), 5, 'cleanups are not registered')
        for attribute in ('temp', 'log', 'server', 'sockets'):
            created = self.assignment_line(attribute)
            self.assertTrue(any(line > created for line in registrations),
                            'self.%s is created with no cleanup after it' % attribute)

    def test_the_readiness_limit_and_wait_are_unchanged(self):
        waits = [node for node in ast.walk(self.setup)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == 'wait_for']
        self.assertEqual(len(waits), 1)
        self.assertEqual([arg.value for arg in waits[0].args
                          if isinstance(arg, ast.Constant)], [6],
                         'the original six-second readiness limit changed')

    def test_the_failure_path_reraises_and_suppresses_nothing(self):
        # Only the guard around the readiness wait itself. The original
        # `except OSError: return False` inside `ready` is unchanged behaviour
        # and must not be caught by this control.
        guards = [node for node in ast.walk(self.setup)
                  if isinstance(node, ast.Try)
                  and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                          and call.func.id == 'wait_for'
                          for statement in node.body for call in ast.walk(statement))]
        self.assertEqual(len(guards), 1, 'the readiness wait is not guarded exactly once')
        for clause in guards[0].handlers:
            self.assertTrue(
                any(isinstance(node, ast.Raise) and node.exc is None
                    for node in ast.walk(clause)),
                'a readiness failure is not re-raised unchanged')


if __name__ == '__main__':
    unittest.main()

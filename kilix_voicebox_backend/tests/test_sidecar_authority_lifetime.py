"""Exited issuers and non-finite timestamps cannot authorize packaging."""
import json
import os
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest import mock

if __package__:
    from .test_sidecar_authority import Tree, authority, current_gate_bytes, PLATFORM
else:
    from test_sidecar_authority import Tree, authority, current_gate_bytes, PLATFORM


class AuthorityLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='authority-lifetime-')
        self.addCleanup(self.temp.cleanup)
        self.tree = Tree(self.temp.name, gate_source=current_gate_bytes())
        self.tree.refresh_receipt_commit()
        self.counter = 0

    def arguments(self):
        return dict(build_id=self.tree.build_id, source_commit=self.tree.commit,
                    source_tree=self.tree.tree, platform=PLATFORM,
                    binary=self.tree.binary, provenance=self.tree.provenance,
                    receipt=self.tree.receipt, resources=self.tree.resource_rows(),
                    root=self.tree.root)

    def issue(self, **overrides):
        self.counter += 1
        directory = Path(self.temp.name) / ('authority-%d' % self.counter)
        return authority.issue(directory, **dict(self.arguments(), **overrides))

    def consume(self, path, **overrides):
        arguments = dict(root=self.tree.root, source_commit=self.tree.commit,
                         source_tree=self.tree.tree, platform=PLATFORM)
        arguments.update(overrides)
        return authority.consume(path, **arguments)

    def test_live_finite_issuer_still_authorizes_exactly_once(self):
        path = self.issue()
        result = self.tree.run_gate({authority.VARIABLE: str(path)})
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.tree.run_gate({authority.VARIABLE: str(path)})
        self.assertEqual(result.returncode, 2, result.stderr)

    def check_exited_issuer(self, *, terminate):
        directory = Path(self.temp.name) / 'exited-issuer'
        child_pid = os.fork()
        if child_pid == 0:
            try:
                authority.issue(directory, **self.arguments())
                if terminate:
                    os.kill(os.getpid(), signal.SIGTERM)
            except BaseException:
                os._exit(1)
            os._exit(0)
        reaped = False
        try:
            deadline = time.monotonic() + 5
            while True:
                raw = Path('/proc/%d/stat' % child_pid).read_text()
                state = raw.rsplit(')', 1)[1].split()[0]
                if state == 'Z':
                    break
                self.assertLess(time.monotonic(), deadline, 'owned issuer did not exit')
                time.sleep(0.01)
            path = directory / ('authority-' + self.tree.build_id + '.json')
            self.assertTrue(path.is_file(), 'owned issuer must mint before it exits')
            self.assertIsNone(authority.process_start_ticks(child_pid))
            before = (self.tree.binary.read_bytes(), self.tree.provenance.read_bytes(),
                      self.tree.receipt.read_bytes())
            result = self.tree.run_gate({authority.VARIABLE: str(path)})
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('no longer running', result.stderr)
            self.assertNotIn('honoured a live capability', result.stdout)
            self.assertEqual(before, (self.tree.binary.read_bytes(),
                                     self.tree.provenance.read_bytes(),
                                     self.tree.receipt.read_bytes()))
            self.assertFalse(path.exists())
            self.assertFalse(list(directory.glob('*.claimed-*')))
            waited_pid, status = os.waitpid(child_pid, 0)
            reaped = True
            self.assertEqual(waited_pid, child_pid)
            self.assertEqual(os.waitstatus_to_exitcode(status), -signal.SIGTERM if terminate else 0)
            self.assertFalse(Path('/proc/%d' % child_pid).exists())
        finally:
            if not reaped:
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(child_pid, 0)

    def test_exited_unreaped_issuer_is_not_live_authority(self):
        self.check_exited_issuer(terminate=False)

    def test_terminated_unreaped_issuer_is_not_live_authority(self):
        self.check_exited_issuer(terminate=True)

    def test_nonfinite_or_nonnumeric_lifetime_refuses_before_creating_authority(self):
        for value in (float('inf'), float('-inf'), float('nan'), True, '1', 10 ** 1000):
            with self.subTest(value_type=type(value).__name__, value=str(value)[:20]):
                with self.assertRaises(authority.AuthorityError):
                    self.issue(lifetime_seconds=value)
                self.assertFalse((Path(self.temp.name) / ('authority-%d' % self.counter)).exists())

    def test_nonfinite_issue_clock_refuses_before_creating_authority(self):
        for value in (float('inf'), float('-inf'), float('nan')):
            with self.subTest(clock=str(value)), mock.patch.object(authority.time, 'time', return_value=value):
                with self.assertRaises(authority.AuthorityError):
                    self.issue()
                self.assertFalse((Path(self.temp.name) / ('authority-%d' % self.counter)).exists())

    def test_nonfinite_record_times_refuse_and_burn_the_capability(self):
        for key in ('issued_at', 'expires_at'):
            for value in (float('inf'), float('-inf'), float('nan'), 10 ** 1000):
                with self.subTest(field=key, value=str(value)[:20]):
                    path = self.issue()
                    record = json.loads(path.read_text())
                    record[key] = value
                    path.write_text(json.dumps(record) + '\n')
                    with self.assertRaises(authority.AuthorityError):
                        self.consume(path)
                    self.assertFalse(path.exists())
                    self.assertFalse(list(path.parent.glob('*.claimed-*')))

    def test_nonfinite_validation_clock_refuses_and_burns_the_capability(self):
        for value in (float('inf'), float('-inf'), float('nan'), True, 10 ** 1000):
            with self.subTest(clock=str(value)[:20]):
                path = self.issue()
                with self.assertRaises(authority.AuthorityError):
                    self.consume(path, now=value)
                self.assertFalse(path.exists())

    def test_finite_expired_capability_still_refuses(self):
        path = self.issue(lifetime_seconds=-1)
        with self.assertRaises(authority.AuthorityError):
            self.consume(path)


if __name__ == '__main__':
    unittest.main()

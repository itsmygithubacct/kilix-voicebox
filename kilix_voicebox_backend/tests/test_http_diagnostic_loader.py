"""Nonlaunching controls for the private diagnostic fixture loader.

No fake canonical modules, voicelib or real provider dependencies are installed
or imported. Cache checks bracket each loader call itself; already-present real
objects are preserved when these tests are collected in a broader environment.
"""
import ast
import builtins
import sys
import types
import unittest
from unittest.mock import patch

from . import test_http_diagnostics as diagnostics


CANONICAL = ('kilix_voicebox_backend.tests.test_managed_capture',
             'kilix_voicebox_backend.tests.test_capture_http')


def cache_state():
    modules = sys.modules.copy()
    selected = {name: value for name, value in modules.items()
                if name in CANONICAL or name == 'voicelib' or name.startswith('voicelib.')}
    package = modules['kilix_voicebox_backend.tests']
    return selected, dict(vars(package))


class LoaderIsolationTests(unittest.TestCase):
    def assertState(self, before):
        after = cache_state()
        for previous, current in zip(before, after):
            self.assertEqual(set(previous), set(current))
            for name, value in previous.items():
                self.assertIs(current[name], value, name)

    def test_repeated_loads_have_private_modules_classes_and_helpers(self):
        before = cache_state()
        first = diagnostics.load_actual_fixture()
        second = diagnostics.load_actual_fixture()
        self.assertState(before)
        self.assertIsNot(first, second)
        self.assertIsNot(first.CaptureHTTPTests, second.CaptureHTTPTests)
        self.assertIsNot(first.wait_for.__globals__, second.wait_for.__globals__)
        for actual in (first, second):
            self.assertNotIn(actual, sys.modules.values())
            self.assertEqual(actual.__file__, str(diagnostics.HTTP_FIXTURE))
            self.assertEqual(actual.__package__, diagnostics.__package__)
            self.assertEqual(actual.wait_for.__globals__['__file__'], str(diagnostics.SIBLING))

    def test_private_mutation_cannot_modify_canonical_fixture_or_package(self):
        before = cache_state()
        actual = diagnostics.load_actual_fixture()
        canonical_values = {name: dict(vars(module)) for name, module in before[0].items()
                            if name in CANONICAL}
        actual.PEER = 'private-control-only'
        actual.DIAGNOSTIC_DIR = 'private-diagnostics-only'
        actual.CaptureHTTPTests.private_control_marker = True
        self.assertState(before)
        for name, attributes in canonical_values.items():
            self.assertEqual(vars(sys.modules[name]), attributes)
        again = diagnostics.load_actual_fixture()
        self.assertNotEqual(again.PEER, actual.PEER)
        self.assertFalse(hasattr(again.CaptureHTTPTests, 'private_control_marker'))

    def test_only_exact_relative_import_is_intercepted(self):
        original = builtins.__import__
        calls = []
        sentinel = object()

        def delegate(name, globals=None, locals=None, fromlist=(), level=0):
            calls.append((name, globals, locals, fromlist, level))
            if name == 'test_managed_capture':
                return sentinel  # direct callback probe only, no module/cache write
            return original(name, globals, locals, fromlist, level)

        before = cache_state()
        with patch.object(builtins, '__import__', delegate):
            actual = diagnostics.load_actual_fixture()
        callback = actual.__dict__['__builtins__']['__import__']
        local = {}
        selected = callback('test_managed_capture', actual.__dict__, local,
                            ('PEER', 'wait_for'), 1)
        self.assertIs(selected.wait_for, actual.wait_for)
        self.assertFalse(any(row[0] == 'test_managed_capture' for row in calls))
        for globals, names, level in (({}, ('PEER', 'wait_for'), 1),
                                      (actual.__dict__, ('PEER',), 1),
                                      (actual.__dict__, ('PEER', 'wait_for'), 0)):
            self.assertIs(callback('test_managed_capture', globals, local, names, level), sentinel)
            self.assertEqual(calls[-1][0], 'test_managed_capture')
            self.assertIs(calls[-1][1], globals)
            self.assertIs(calls[-1][2], local)
            self.assertIs(calls[-1][3], names)
            self.assertEqual(calls[-1][4], level)
        self.assertIn('http.client', [row[0] for row in calls])
        self.assertState(before)

    def test_builtins_are_private_and_global_importer_is_unchanged(self):
        original = builtins.__import__
        first = diagnostics.load_actual_fixture()
        second = diagnostics.load_actual_fixture()
        self.assertIs(builtins.__import__, original)
        self.assertIsNot(first.__dict__['__builtins__'], vars(builtins))
        self.assertIsNot(first.__dict__['__builtins__'], second.__dict__['__builtins__'])
        for key, value in vars(builtins).items():
            if key != '__import__':
                self.assertIs(first.__dict__['__builtins__'][key], value)

    def test_missing_helper_refuses_without_cache_or_package_changes(self):
        original = diagnostics.Path.read_text
        before = cache_state()

        def read(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            return value.replace('PEER =', 'OTHER =', 1) if path == diagnostics.SIBLING else value

        with patch.object(diagnostics.Path, 'read_text', read):
            with self.assertRaisesRegex(AssertionError, 'exactly PEER and wait_for'):
                diagnostics.load_actual_fixture()
        self.assertState(before)

    def test_fixture_syntax_failure_leaves_cache_and_package_unchanged(self):
        original = diagnostics.Path.read_text
        before = cache_state()

        def read(path, *args, **kwargs):
            if path == diagnostics.HTTP_FIXTURE:
                return 'def invalid syntax\n'
            return original(path, *args, **kwargs)

        with patch.object(diagnostics.Path, 'read_text', read):
            with self.assertRaises(SyntaxError):
                diagnostics.load_actual_fixture()
        self.assertState(before)

    def test_delegated_import_exception_is_preserved_without_cache_changes(self):
        original = builtins.__import__
        failure = ImportError('controlled stdlib import boundary')
        before = cache_state()

        def delegate(name, globals=None, locals=None, fromlist=(), level=0):
            if name == 'http.client':
                raise failure
            return original(name, globals, locals, fromlist, level)

        with patch.object(builtins, '__import__', delegate):
            with self.assertRaises(ImportError) as caught:
                diagnostics.load_actual_fixture()
        self.assertIs(caught.exception, failure)
        self.assertState(before)

    def test_lifted_wait_preserves_timeout_and_predicate_exception(self):
        actual = diagnostics.load_actual_fixture()
        times = iter((10, 10, 11))
        slept = []
        previous = actual.wait_for.__globals__['time']
        actual.wait_for.__globals__['time'] = types.SimpleNamespace(
            monotonic=lambda: next(times), sleep=slept.append)
        try:
            with self.assertRaisesRegex(AssertionError, '^owned observation timed out$'):
                actual.wait_for(lambda: False, 0.5)
        finally:
            actual.wait_for.__globals__['time'] = previous
        self.assertEqual(slept, [0.01])
        failure = ValueError('controlled predicate')

        def predicate():
            raise failure

        with self.assertRaises(ValueError) as caught:
            actual.wait_for(predicate)
        self.assertIs(caught.exception, failure)

    def test_diagnostic_discovery_cannot_collect_six_private_http_bodies(self):
        before = cache_state()
        actual = diagnostics.load_actual_fixture()
        self.assertNotIn('CaptureHTTPTests', vars(diagnostics))
        self.assertEqual(unittest.defaultTestLoader.loadTestsFromModule(diagnostics).countTestCases(), 12)
        self.assertEqual(len(unittest.defaultTestLoader.getTestCaseNames(actual.CaptureHTTPTests)), 6)
        self.assertTrue(all(value is not actual.CaptureHTTPTests for value in vars(diagnostics).values()))
        self.assertState(before)

    def test_real_sibling_methods_are_not_hidden_by_helper_cache(self):
        source = diagnostics.SIBLING.read_bytes()
        tree = ast.parse(source)
        names = {node.name for klass in tree.body
                 if isinstance(klass, ast.ClassDef) and klass.name == 'ManagedCaptureTests'
                 for node in klass.body if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')}
        self.assertEqual(len(names), 13)
        before = cache_state()
        diagnostics.load_actual_fixture()
        self.assertState(before)
        self.assertEqual(diagnostics.SIBLING.read_bytes(), source)
        if diagnostics.SIBLING_MODULE in before[0]:
            # Only inspect a genuinely preexisting sibling, never create a fake
            # canonical module or import its real dependencies for this control.
            existing = before[0][diagnostics.SIBLING_MODULE]
            self.assertEqual(set(unittest.defaultTestLoader.getTestCaseNames(existing.ManagedCaptureTests)), names)


if __name__ == '__main__':
    unittest.main()

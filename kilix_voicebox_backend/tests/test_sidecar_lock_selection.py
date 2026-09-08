"""Consumer lock-selection controls; no target imports, archives or actual builds."""
from __future__ import annotations

import ast
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

if __package__:
    from .test_sidecar_gate import Fixture, ROOT, UV_LOCK, MANIFEST, BUILD_ID
    from .test_sidecar_gate import SOURCE_COMMIT, SOURCE_TREE, canonical, sha, snapshot_row, verify
else:
    from test_sidecar_gate import Fixture, ROOT, UV_LOCK, MANIFEST, BUILD_ID
    from test_sidecar_gate import SOURCE_COMMIT, SOURCE_TREE, canonical, sha, snapshot_row, verify


TARGET = {
    'implementation_name': 'cpython',
    'platform_python_implementation': 'CPython',
    'platform_machine': 'x86_64',
    'sys_platform': 'linux',
    'python_full_version': '3.12.8',
}
FROZEN_LOCK_SHA256 = 'c32006e700bde9801f6e427d2c1370f6ff3a1bb6b7259e2468c414345de89aa6'


def marker_matches(expression):
    """Evaluate only the small marker syntax present in this frozen test input."""
    def value(node):
        if isinstance(node, ast.Name):
            return TARGET[node.id]
        if isinstance(node, ast.Constant) and type(node.value) is str:
            return node.value
        raise AssertionError('unexpected marker operand')

    def check(node):
        if isinstance(node, ast.BoolOp):
            outcomes = [check(item) for item in node.values]
            if isinstance(node.op, ast.And):
                return all(outcomes)
            if isinstance(node.op, ast.Or):
                return any(outcomes)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = value(node.left), value(node.comparators[0])
            if any(isinstance(item, ast.Name) and item.id == 'python_full_version'
                   for item in (node.left, node.comparators[0])):
                left, right = tuple(map(int, left.split('.'))), tuple(map(int, right.split('.')))
            op = node.ops[0]
            if isinstance(op, ast.Eq):
                return left == right
            if isinstance(op, ast.NotEq):
                return left != right
            if isinstance(op, ast.Lt):
                return left < right
        raise AssertionError('unexpected frozen marker syntax')
    return check(ast.parse(expression, mode='eval').body)


def derive_selection():
    """Independently traverse actual uv.lock edges, never the consumer's table."""
    raw = UV_LOCK.read_bytes()
    assert sha(raw) == FROZEN_LOCK_SHA256
    lock = tomllib.loads(raw.decode('utf-8'))
    assert (lock['version'], lock['revision']) == (1, 3)
    packages = {row['name']: row for row in lock['package']}
    assert len(packages) == len(lock['package']) == 48
    root = packages['kilix-voicebox-backend']
    assert root['source'] == {'virtual': '.'}
    assert len(root['dependencies']) == 9
    assert root['dev-dependencies']['build'] == [{'name': 'pyinstaller'}]
    queue = [(root['name'], '')]
    seen, edges, selected_extras = set(), [], {}
    while queue:
        name, extra = queue.pop(0)
        if (name, extra) in seen:
            continue
        seen.add((name, extra))
        package = packages[name]
        if name != root['name']:
            assert package['source'] == {'registry': 'https://pypi.org/simple'}
            selected_extras.setdefault(name, set())
            if extra:
                selected_extras[name].add(extra)
        if extra:
            dependencies = [('extra:' + extra, d)
                            for d in package['optional-dependencies'][extra]]
        else:
            dependencies = [('runtime', d) for d in package.get('dependencies', [])]
            if name == root['name']:
                dependencies += [('group:build', d)
                                 for d in root['dev-dependencies']['build']]
        for origin, dependency in dependencies:
            assert set(dependency) <= {'name', 'marker', 'extra'}
            marker = dependency.get('marker')
            active = marker_matches(marker) if marker else True
            edges.append({'parent': name, 'origin': origin, 'dependency': dependency,
                          'marker_active': active})
            if active:
                target = dependency['name']
                queue.append((target, ''))
                queue.extend((target, choice) for choice in dependency.get('extra', []))
    selected = [{'name': name, 'version': packages[name]['version'],
                 'extras': sorted(extras)}
                for name, extras in sorted(selected_extras.items())]
    return {'lock_sha256': sha(raw), 'target': dict(TARGET),
            'lock_records': len(packages), 'selected': selected, 'edges': edges,
            'excluded': sorted(set(packages) - set(selected_extras))}


def remove_distribution(record, name):
    build = record['build']
    row = next(row for row in build['distributions'] if row['name'] == name)
    build['distributions'].remove(row)
    owned = set(row['members'])
    build['snapshot']['files'] = [r for r in build['snapshot']['files']
                                  if r['path'] not in owned]


def add_distribution(record, name, version, suffix=''):
    path = 'env/' + name + '/extra' + suffix + '.py'
    record['build']['snapshot']['files'].append(snapshot_row(path, b'controlled extra'))
    record['build']['snapshot']['files'].sort(key=lambda r: r['path'])
    record['build']['distributions'].append({
        'name': name, 'version': version, 'record_sha256': sha(b'controlled RECORD'),
        'members': [path]})
    record['build']['distributions'].sort(key=lambda r: r['name'])


class LockedDistributionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()

    def validate(self, record=None, lock_sha256=None):
        return verify.validate(
            provenance_bytes=canonical(self.fixture.record if record is None else record),
            binary_bytes_length=len(self.fixture.binary), binary_sha256=sha(self.fixture.binary),
            build_id=BUILD_ID, source_commit=SOURCE_COMMIT, source_tree=SOURCE_TREE,
            manifest_bytes=self.fixture.manifest_bytes,
            uv_lock_sha256=sha(UV_LOCK.read_bytes()) if lock_sha256 is None else lock_sha256)

    def refuses_selection(self, record):
        with self.assertRaisesRegex(verify.Refused, 'selected uv.lock name/version population'):
            self.validate(record)

    def test_complete_lock_derivation_equals_the_consumer_selection(self):
        proof = derive_selection()
        expected = {r['name']: r['version'] for r in proof['selected']}
        self.assertEqual(len(expected), 35)
        self.assertEqual(expected, dict(verify.LOCKED_DISTRIBUTIONS))
        self.assertEqual(len(verify.LOCKED_DISTRIBUTIONS), 35)
        self.assertEqual(tuple(sorted(verify.LOCKED_DISTRIBUTIONS)), verify.LOCKED_DISTRIBUTIONS)
        self.assertEqual(verify.UV_LOCK_SHA256, proof['lock_sha256'])
        self.assertEqual(len(proof['excluded']), 13)

    def test_target_markers_and_standard_extras_are_explicit(self):
        proof = derive_selection()
        selected = {r['name']: r for r in proof['selected']}
        self.assertEqual(selected['uvicorn']['extras'], ['standard'])
        self.assertEqual([n for n, r in selected.items() if r['extras']], ['uvicorn'])
        active = {e['dependency']['name'] for e in proof['edges']
                  if e['origin'] == 'extra:standard' and e['marker_active']}
        self.assertEqual(active, {'httptools', 'python-dotenv', 'pyyaml',
                                  'uvloop', 'watchfiles', 'websockets'})
        self.assertTrue(any(e['dependency']['name'] == 'greenlet'
                            and e['marker_active'] for e in proof['edges']))
        self.assertTrue(any(e['dependency'].get('marker') == "python_full_version < '3.13'"
                            and e['marker_active'] for e in proof['edges']))
        inactive = {e['dependency']['name'] for e in proof['edges'] if not e['marker_active']}
        self.assertEqual(inactive, {'macholib', 'pefile', 'pywin32-ctypes'})

    def test_the_complete_shaped_population_is_accepted(self):
        self.assertEqual(self.validate()['distributions'], 35)

    def test_every_missing_selected_package_refuses(self):
        for name, _ in verify.LOCKED_DISTRIBUTIONS:
            with self.subTest(name=name):
                record = copy.deepcopy(self.fixture.record)
                remove_distribution(record, name)
                self.refuses_selection(record)

    def test_every_wrong_selected_version_refuses(self):
        for name, _ in verify.LOCKED_DISTRIBUTIONS:
            with self.subTest(name=name):
                record = copy.deepcopy(self.fixture.record)
                next(r for r in record['build']['distributions']
                     if r['name'] == name)['version'] = '0.0.0'
                self.refuses_selection(record)

    def test_all_excluded_lock_packages_refuse(self):
        packages = {r['name']: r['version']
                    for r in tomllib.loads(UV_LOCK.read_text())['package']}
        for name in derive_selection()['excluded']:
            with self.subTest(name=name):
                record = copy.deepcopy(self.fixture.record)
                add_distribution(record, name, packages[name])
                self.refuses_selection(record)

    def test_unlocked_provider_and_model_packages_refuse(self):
        for name in ('torch', 'qwen-tts', 'kilix-transcribe'):
            with self.subTest(name=name):
                record = copy.deepcopy(self.fixture.record)
                add_distribution(record, name, '99.0.0')
                self.refuses_selection(record)

    def test_one_package_population_refuses(self):
        record = copy.deepcopy(self.fixture.record)
        for name, _ in verify.LOCKED_DISTRIBUTIONS:
            if name != 'pyinstaller':
                remove_distribution(record, name)
        self.assertEqual(len(record['build']['distributions']), 1)
        self.refuses_selection(record)

    def test_conditional_and_standard_dependencies_cannot_be_omitted(self):
        for name in ('greenlet', 'typing-extensions', 'httptools', 'python-dotenv',
                     'pyyaml', 'uvloop', 'watchfiles', 'websockets'):
            with self.subTest(name=name):
                record = copy.deepcopy(self.fixture.record)
                remove_distribution(record, name)
                self.refuses_selection(record)

    def test_duplicate_name_with_disjoint_members_refuses(self):
        record = copy.deepcopy(self.fixture.record)
        add_distribution(record, 'fastapi', '0.141.1', '-duplicate')
        with self.assertRaisesRegex(verify.Refused, 'duplicate name'):
            self.validate(record)

    def test_wrong_target_declarations_refuse(self):
        for key, value in (('implementation', 'PyPy'), ('version', '3.13.0'),
                           ('architecture', 'aarch64'), ('platform', 'win32')):
            with self.subTest(key=key):
                record = copy.deepcopy(self.fixture.record)
                record['build']['python'][key] = value
                with self.assertRaises(verify.Refused):
                    self.validate(record)

    def test_unknown_actual_lock_refuses_even_when_the_record_agrees(self):
        for raw in (b'not a lock', UV_LOCK.read_bytes() + b'\n',
                    UV_LOCK.read_bytes().replace(b'version = "0.141.1"',
                                                b'version = "0.141.2"')):
            with self.subTest(lock=sha(raw)):
                record = copy.deepcopy(self.fixture.record)
                record['uv_lock_sha256'] = sha(raw)
                with self.assertRaisesRegex(verify.Refused, 'no supported distribution selection'):
                    self.validate(record, lock_sha256=sha(raw))

    def test_cli_binds_real_lock_bytes_and_never_executes_the_sidecar(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as temp:
            root = Path(temp)
            binary, provenance, lock = (root / n for n in ('sidecar', 'record.json', 'uv.lock'))
            binary.write_bytes(self.fixture.binary)
            binary.chmod(0o600)
            arguments = [sys.executable, '-I', '-B',
                         str(ROOT / 'tools/verify_sidecar_provenance.py'),
                         '--binary', str(binary), '--provenance', str(provenance),
                         '--input-manifest', str(MANIFEST), '--uv-lock', str(lock),
                         '--build-id', BUILD_ID, '--source-commit', SOURCE_COMMIT,
                         '--source-tree', SOURCE_TREE]
            for altered in (False, True):
                with self.subTest(altered=altered):
                    raw = UV_LOCK.read_bytes() + (b'\n' if altered else b'')
                    lock.write_bytes(raw)
                    record = copy.deepcopy(self.fixture.record)
                    record['uv_lock_sha256'] = sha(raw)
                    provenance.write_bytes(canonical(record))
                    result = subprocess.run(arguments, cwd=root, capture_output=True,
                                            text=True, timeout=10)
                    self.assertEqual(result.returncode, 2 if altered else 0, result.stderr)
                    if altered:
                        self.assertIn('no supported distribution selection', result.stderr)
                    else:
                        self.assertEqual(json.loads(result.stdout)['status'], 'verified')

    def test_consumer_does_not_import_ambient_packaging_or_target_packages(self):
        self.validate()
        for name in ('packaging', 'torch', 'onnxruntime', 'PyInstaller', 'fastapi'):
            self.assertNotIn(name, sys.modules)


if __name__ == '__main__':
    unittest.main()

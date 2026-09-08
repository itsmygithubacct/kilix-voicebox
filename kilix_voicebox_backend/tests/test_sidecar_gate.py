"""Controls for the strict sidecar consumer, resource gate and build ordering.

Every provenance record here is a controlled fixture built from the checked-in
input manifest. Nothing in this module runs PyInstaller, opens a real archive,
compiles Rust or a frontend, packages a bundle, executes a target binary,
acquires a package or touches a model: these are observer and consumer tests,
and a passing run is not acceptance of any actual build.

The ordering control drives the real Makefile with controlled stand-ins for the
producer and for `bun`, and it decides by observation rather than by waiting:
the controlled producer records whether the packaging step had already started
alongside it, which is a fact, not a race.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / '.kilix-import/sidecar-inputs.json'
UV_LOCK = ROOT / 'uv.lock'
MAKEFILE = ROOT / 'Makefile'
SOURCE_COMMIT = 'a' * 40
SOURCE_TREE = 'b' * 40
BUILD_ID = '0123456789abcdef0123456789abcdef'


# Imported the way the tools import each other, so both share one module object
# and one Refused class; loading them separately would make the refusal types
# distinct and quietly weaken every assertRaises below.
sys.path.insert(0, str(ROOT / 'tools'))
import prepare_sidecar_resources as prepare  # noqa: E402
import verify_sidecar_provenance as verify  # noqa: E402


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(record):
    return (json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
            + '\n').encode('utf-8')


def snapshot_row(path, data, mode='0600'):
    return {'path': path, 'bytes': len(data), 'sha256': sha(data), 'mode': mode}


class Fixture:
    """A compliant candidate-v2 record built from the real input manifest."""

    def __init__(self):
        self.manifest_bytes = MANIFEST.read_bytes()
        manifest = json.loads(self.manifest_bytes)
        self.binary = b'controlled sidecar payload'
        rows = []
        self.by_output = {}
        for spec in manifest['sources'].values():
            for member in spec['members']:
                row = {'path': member['output'], 'bytes': member['bytes'],
                       'sha256': member['sha256'], 'mode': member['output_mode'],
                       'role': member['role']}
                rows.append(row)
                self.by_output[member['output']] = row
        rows.sort(key=lambda row: row['path'])
        modes = {key + '/' + member['path']: member['output_mode']
                 for key, spec in manifest['sources'].items() for member in spec['members']}
        completion = {
            'schema': 'kilix.voicebox.sidecar-stage/v1',
            'input_manifest_sha256': sha(self.manifest_bytes),
            'sources': {key: {k: spec[k] for k in ('repository', 'commit', 'tree')}
                        for key, spec in manifest['sources'].items()},
            'wheel': {key: manifest['wheel'][key] for key in ('filename', 'bytes', 'sha256')},
            'observed_source_modes': modes,
            'observed_wheel_mode': '0600',
            'files': rows,
        }
        plaintext = []
        for output in ('python/VERSION', 'licenses/kilix-transcribe/LICENSE',
                       'licenses/kilix-voice/LICENSE',
                       'python/voicelib/microphone_supervisor.py'):
            row = self.by_output[output]
            path = output[len('python/'):] if output.startswith('python/') else output
            plaintext.append({'path': path, 'bytes': row['bytes'], 'sha256': row['sha256']})
        plaintext.sort(key=lambda row: row['path'])
        self.plaintext = plaintext

        snapshot_files = [
            snapshot_row('env/pyinstaller/__init__.py', b'pyinstaller package'),
            snapshot_row('env/pyinstaller-6.15.0.dist-info/RECORD', b'record bytes'),
            snapshot_row('host/bin/ld', b'linker', '0700'),
            snapshot_row('host/lib/libz.so.1', b'compression library'),
            snapshot_row('projection/voicelib/audio.py', b'projected module'),
            snapshot_row('python/bin/python3.12', b'interpreter', '0700'),
            snapshot_row('python/lib/python3.12/os.py', b'standard library'),
            snapshot_row('source/kilix_voicebox_backend/server.py', b'product source'),
        ]
        snapshot_files.sort(key=lambda row: row['path'])
        modules = sorted(
            ({'name': verify.module_name(row['path']),
              'source_sha256': row['sha256'],
              'code_sha256': sha(('code:' + row['path']).encode())}
             for row in rows if row['role'] == 'module'),
            key=lambda row: row['name'])
        self.record = {
            'schema': verify.SCHEMA,
            'build_id': BUILD_ID,
            'source_commit': SOURCE_COMMIT,
            'source_tree': SOURCE_TREE,
            'uv_lock_sha256': sha(UV_LOCK.read_bytes()),
            'sidecar_sha256': sha(self.binary),
            'sidecar_bytes': len(self.binary),
            'contains_model_runtime': False,
            'provider_endpoints': list(verify.PROVIDER_ENDPOINTS),
            'inputs': {
                'completion_sha256': sha(
                    (json.dumps(completion, sort_keys=True, indent=2) + '\n').encode('utf-8')),
                'completion': completion,
                'plaintext': plaintext,
            },
            'build': {
                'python': {
                    'implementation': 'CPython', 'version': verify.PYTHON_VERSION,
                    'executable': verify.PYTHON_EXECUTABLE,
                    'members': sorted(row['path'] for row in snapshot_files
                                      if row['path'].startswith('python/')),
                },
                'pyinstaller': {'distribution': 'pyinstaller', 'version': verify.PYINSTALLER_VERSION},
                'distributions': [{
                    'name': 'pyinstaller', 'version': verify.PYINSTALLER_VERSION,
                    'record_sha256': sha(b'record bytes'),
                    'members': sorted(row['path'] for row in snapshot_files
                                      if row['path'].startswith('env/')),
                }],
                'tools': [
                    {'name': 'host/lib/libz.so.1', 'member': 'host/lib/libz.so.1'},
                    {'name': 'ld', 'member': 'host/bin/ld'},
                ],
                'snapshot': {'sha256': sha(b'sealed bundle'), 'files': snapshot_files},
                'archive': {
                    'selected_modules': modules,
                    'plaintext': plaintext,
                    'entries': [
                        {'path': 'PYZ-00.pyz', 'kind': 'z', 'bytes': 4096,
                         'sha256': sha(b'pyz')},
                        {'path': 'VERSION', 'kind': '1', 'bytes': 6, 'sha256': sha(b'version')},
                    ],
                    'options': ['pyi-disable-windowed-traceback'],
                },
            },
        }
        self.record['build']['tools'].sort(key=lambda row: row['name'])

    def bytes(self, record=None):
        return canonical(self.record if record is None else record)

    def mutate(self, change):
        record = copy.deepcopy(self.record)
        change(record)
        return record


class ProvenanceContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.tmp = tempfile.TemporaryDirectory(prefix='sidecar-gate-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def validate(self, record=None, *, raw=None, build_id=BUILD_ID, binary=None):
        payload = self.fixture.binary if binary is None else binary
        return verify.validate(
            provenance_bytes=self.fixture.bytes(record) if raw is None else raw,
            binary_bytes_length=len(payload), binary_sha256=sha(payload),
            build_id=build_id, source_commit=SOURCE_COMMIT, source_tree=SOURCE_TREE,
            manifest_bytes=self.fixture.manifest_bytes,
            uv_lock_sha256=sha(UV_LOCK.read_bytes()))

    def refuses(self, change, fragment):
        with self.assertRaises(verify.Refused) as caught:
            self.validate(self.fixture.mutate(change))
        self.assertIn(fragment, str(caught.exception))

    def test_the_compliant_fixture_validates(self):
        report = self.validate()
        self.assertEqual(report['build_id'], BUILD_ID)
        self.assertEqual(len(report['selected_modules']), verify.SELECTED_MODULE_COUNT)
        self.assertEqual(len(report['plaintext']), 4)
        # The report separates what real bytes proved from what the producer
        # still only claims.
        self.assertTrue(report['verified_from_actual_files'])
        self.assertTrue(report['producer_bound_claims'])

    def test_envelope_and_encoding_families(self):
        cases = [
            (b'{}', 'missing keys'),
            (b'not json', 'not valid JSON'),
            (self.fixture.bytes() + b'{}', 'trailing data'),
            (canonical(self.fixture.record)[:-1], 'canonical'),
            (json.dumps(self.fixture.record, sort_keys=True, indent=1).encode() + b'\n',
             'canonical'),
            (b'{"schema": 1.5}', 'floating point'),
            ('{"schema":"\udcff"}'.encode('utf-8', 'surrogateescape'), 'not valid UTF-8'),
            (b'{"schema":"a","schema":"b"}', 'duplicate object key'),
        ]
        for raw, fragment in cases:
            with self.subTest(fragment=fragment):
                with self.assertRaises(verify.Refused) as caught:
                    self.validate(raw=raw)
                self.assertIn(fragment, str(caught.exception))

    def test_schema_families(self):
        def downgrade(record):
            record['schema'] = 'kilix.voicebox.sidecar-provenance/candidate-v1'
        self.refuses(downgrade, 'no downgrade')

        def future(record):
            record['schema'] = 'kilix.voicebox.sidecar-provenance/candidate-v3'
        self.refuses(future, 'no downgrade')

        def unknown(record):
            record['extra'] = 1
        self.refuses(unknown, 'unknown keys')

        def missing(record):
            del record['build']
        self.refuses(missing, 'missing keys')

    def test_identity_families(self):
        self.refuses(lambda r: r.__setitem__('build_id', 'f' * 32), 'not this invocation')
        self.refuses(lambda r: r.__setitem__('source_commit', 'c' * 40),
                     'not the expected clean product commit')
        self.refuses(lambda r: r.__setitem__('source_tree', 'd' * 40), 'not that commit tree')
        self.refuses(lambda r: r.__setitem__('uv_lock_sha256', '0' * 64),
                     'actual product uv.lock')
        self.refuses(lambda r: r.__setitem__('sidecar_sha256', '0' * 64), 'actual sidecar bytes')
        self.refuses(lambda r: r.__setitem__('sidecar_bytes', 1), 'actual sidecar length')
        self.refuses(lambda r: r.__setitem__('contains_model_runtime', 0), 'JSON boolean false')
        self.refuses(lambda r: r.__setitem__('provider_endpoints',
                                             list(reversed(verify.PROVIDER_ENDPOINTS))),
                     'exact selected ordered pair')

    def test_a_stale_pair_and_a_mixed_pair_refuse(self):
        # A previous invocation's record beside this build's binary.
        stale = self.fixture.mutate(lambda r: r.__setitem__('build_id', 'e' * 32))
        with self.assertRaises(verify.Refused):
            self.validate(stale, build_id=BUILD_ID)
        # This build's record beside a different binary.
        with self.assertRaises(verify.Refused) as caught:
            self.validate(binary=b'another sidecar entirely')
        self.assertIn('actual sidecar bytes', str(caught.exception))

    def test_staged_input_membership_families(self):
        def drop(record):
            record['inputs']['completion']['files'].pop()
        self.refuses(drop, '19 rows')

        def foreign(record):
            row = dict(record['inputs']['completion']['files'][0])
            row['path'] = 'python/qwen/engine.py'
            record['inputs']['completion']['files'][0] = row
        self.refuses(foreign, 'outside the manifest')

        def changed(record):
            record['inputs']['completion']['files'][0]['sha256'] = '0' * 64
        self.refuses(changed, 'differs from the manifest')

        def manifest(record):
            record['inputs']['completion']['input_manifest_sha256'] = '0' * 64
        self.refuses(manifest, 'selected input manifest')

        def commit(record):
            record['inputs']['completion']['sources']['voice']['commit'] = 'f' * 40
        self.refuses(commit, 'not the selected commit')

        def wheel(record):
            record['inputs']['completion']['wheel']['bytes'] = 1
        self.refuses(wheel, 'wheel length')

        def completion_digest(record):
            record['inputs']['completion_sha256'] = '0' * 64
        self.refuses(completion_digest, 'C19 canonical completion encoding')

        def mode(record):
            record['inputs']['completion']['files'][0]['mode'] = '0644'
        self.refuses(mode, 'unsupported mode')

    def test_plaintext_families(self):
        self.refuses(lambda r: r['inputs']['plaintext'].pop(), '4 rows')

        def renamed(record):
            record['inputs']['plaintext'][0]['path'] = 'VERSION.txt'
        self.refuses(renamed, 'four selected plaintext files')

        def altered(record):
            record['inputs']['plaintext'][0]['sha256'] = '0' * 64
        self.refuses(altered, 'differs from the staged input')

        def divergent(record):
            record['build']['archive']['plaintext'] = record['inputs']['plaintext'][:3] + [
                dict(record['inputs']['plaintext'][3], bytes=1)]
        self.refuses(divergent, 'differs from the staged input')

    def test_build_population_families(self):
        self.refuses(lambda r: r['build']['snapshot']['files'].append(
            snapshot_row('elsewhere/x', b'x')), 'declared prefixes')
        self.refuses(lambda r: r['build']['python'].__setitem__('version', '3.13.0'),
                     'selected version')
        self.refuses(lambda r: r['build']['python'].__setitem__('members', []),
                     'omits the interpreter')
        self.refuses(lambda r: r['build']['pyinstaller'].__setitem__('version', '6.16.0'),
                     'selected version')
        self.refuses(lambda r: r['build']['distributions'][0].__setitem__('name', 'PyInstaller'),
                     'normalized lowercase name')
        self.refuses(lambda r: r['build']['distributions'][0]['members'].append(
            'env/pyinstaller/__init__.py'), 'duplicate members')
        self.refuses(lambda r: r['build']['distributions'][0]['members'].append('env/absent.py'),
                     'no snapshot row')
        self.refuses(lambda r: r['build']['distributions'][0]['members'].pop(),
                     'claimed by no distribution')
        self.refuses(lambda r: r['build']['tools'].pop(), 'no tool row')
        self.refuses(lambda r: r['build']['snapshot']['files'][0].__setitem__('mode', '0755'),
                     'unsupported mode')

    def test_archive_families(self):
        self.refuses(lambda r: r['build']['archive']['selected_modules'].pop(), '15 modules')

        def unselected(record):
            record['build']['archive']['selected_modules'][0]['name'] = 'qwen.engine'
        self.refuses(unselected, 'unselected module')

        def wrong_source(record):
            record['build']['archive']['selected_modules'][0]['source_sha256'] = '0' * 64
        self.refuses(wrong_source, 'source digest differs')

        def unsorted(record):
            record['build']['archive']['entries'].reverse()
        self.refuses(unsorted, 'not sorted')

        def kind(record):
            record['build']['archive']['entries'][0]['kind'] = 'zz'
        self.refuses(kind, 'one printable ASCII character')

        def duplicated(record):
            record['build']['archive']['options'].append(
                record['build']['archive']['options'][0])
        self.refuses(duplicated, 'duplicates')

    def test_the_cli_refuses_without_explicit_authority(self):
        binary = self.root / 'voicebox-server'
        binary.write_bytes(self.fixture.binary)
        provenance = self.root / 'voicebox-server.provenance.json'
        provenance.write_bytes(self.fixture.bytes())
        arguments = ['--binary', str(binary), '--provenance', str(provenance),
                     '--input-manifest', str(MANIFEST), '--uv-lock', str(UV_LOCK),
                     '--source-commit', SOURCE_COMMIT, '--source-tree', SOURCE_TREE]
        self.assertEqual(verify.main(arguments), 3)
        self.assertEqual(verify.main(arguments + ['--build-id', BUILD_ID]), 0)
        self.assertEqual(verify.main(arguments + ['--build-id', 'f' * 32]), 2)

    def test_the_cli_binds_a_collector_handoff(self):
        binary = self.root / 'voicebox-server'
        binary.write_bytes(self.fixture.binary)
        provenance = self.root / 'voicebox-server.provenance.json'
        provenance.write_bytes(self.fixture.bytes())
        handoff = self.root / 'sidecar-outputs'
        fields = {'schema': verify.HANDOFF_SCHEMA, 'build_id': BUILD_ID,
                  'binary': 'dist/voicebox-server', 'binary_sha256': sha(self.fixture.binary),
                  'binary_bytes': str(len(self.fixture.binary)),
                  'provenance': 'dist/voicebox-server.provenance.json',
                  'provenance_sha256': sha(self.fixture.bytes())}
        handoff.write_text(''.join('%s=%s\n' % item for item in fields.items()), encoding='ascii')
        arguments = ['--binary', str(binary), '--provenance', str(provenance),
                     '--input-manifest', str(MANIFEST), '--uv-lock', str(UV_LOCK),
                     '--source-commit', SOURCE_COMMIT, '--source-tree', SOURCE_TREE,
                     '--handoff', str(handoff)]
        self.assertEqual(verify.main(arguments), 0)
        binary.write_bytes(b'a different sidecar')
        self.assertEqual(verify.main(arguments), 2)

    def test_a_symlinked_input_is_refused(self):
        binary = self.root / 'voicebox-server'
        binary.write_bytes(self.fixture.binary)
        link = self.root / 'linked-server'
        link.symlink_to(binary)
        with self.assertRaises(verify.Refused) as caught:
            verify.read_regular(link, 'the sidecar binary', limit=1024)
        self.assertIn('symbolic link', str(caught.exception))
        nested = self.root / 'through' / 'voicebox-server'
        (self.root / 'real').mkdir()
        (self.root / 'through').symlink_to(self.root / 'real')
        (self.root / 'real' / 'voicebox-server').write_bytes(self.fixture.binary)
        with self.assertRaises(verify.Refused) as caught:
            verify.read_regular(nested, 'the sidecar binary', limit=1024)
        self.assertIn('symbolic link ancestor', str(caught.exception))


class ResourcePreparationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.tmp = tempfile.TemporaryDirectory(prefix='sidecar-res-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binary = self.root / 'voicebox-server'
        self.binary.write_bytes(self.fixture.binary)
        self.provenance = self.root / 'voicebox-server.provenance.json'
        self.provenance.write_bytes(self.fixture.bytes())
        self.staged = self.root / 'staged'
        # DISCLOSED: the selected Voice and transcribe licence bytes are not in
        # this checkout, so these arms drive the publishing half of the tool
        # with a validated-shaped report over controlled notice payloads. The
        # digest comparison they exercise is the real one; the manifest-bound
        # validation half is covered against the real manifest above.
        self.notices = {name: ('controlled %s notice\n' % name).encode() * 3
                        for name in prepare.NOTICES}
        for name, data in self.notices.items():
            path = self.staged / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.report = {
            'build_id': BUILD_ID, 'source_commit': SOURCE_COMMIT,
            'sidecar_sha256': sha(self.fixture.binary),
            'sidecar_bytes': len(self.fixture.binary),
            'plaintext': sorted(
                ({'path': name, 'bytes': len(data), 'sha256': sha(data)}
                 for name, data in self.notices.items()), key=lambda row: row['path']),
        }
        self.resources = self.root / 'resources'

    def prepare(self, **overrides):
        arguments = {'report': self.report, 'provenance_bytes': self.provenance.read_bytes(),
                     'staged_inputs': self.staged, 'resource_root': self.resources}
        arguments.update(overrides)
        return prepare.publish(**arguments)

    def published(self):
        return self.resources / prepare.PUBLISHED

    def test_a_verified_build_publishes_exactly_the_declared_resources(self):
        receipt = self.prepare()
        self.assertEqual(receipt['build_id'], BUILD_ID)
        published = sorted(str(path.relative_to(self.published()))
                           for path in self.published().rglob('*') if path.is_file())
        self.assertEqual(published, sorted(
            [prepare.RECEIPT, prepare.PROVENANCE_NAME, *prepare.NOTICES]))
        self.assertEqual((self.published() / prepare.PROVENANCE_NAME).read_bytes(),
                         self.provenance.read_bytes())
        for name, data in self.notices.items():
            self.assertEqual((self.published() / name).read_bytes(), data)

    def test_a_changed_notice_refuses_and_publishes_nothing(self):
        (self.staged / prepare.NOTICES[0]).write_bytes(b'a different licence')
        with self.assertRaises(verify.Refused) as caught:
            self.prepare()
        self.assertIn('differ from the verified provenance', str(caught.exception))
        self.assertFalse(self.published().exists())

    def test_a_missing_notice_refuses(self):
        (self.staged / prepare.NOTICES[1]).unlink()
        with self.assertRaises(verify.Refused) as caught:
            self.prepare()
        self.assertIn('not a regular file', str(caught.exception))

    def test_an_extra_staged_file_is_never_published(self):
        (self.staged / 'licenses' / 'unexpected.txt').write_bytes(b'not selected')
        self.prepare()
        published = sorted(str(path.relative_to(self.published()))
                           for path in self.published().rglob('*') if path.is_file())
        self.assertNotIn('licenses/unexpected.txt', published)

    def test_a_symlinked_notice_is_refused(self):
        target = self.root / 'outside-licence'
        target.write_bytes(self.notices[prepare.NOTICES[0]])
        path = self.staged / prepare.NOTICES[0]
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(verify.Refused) as caught:
            self.prepare()
        self.assertIn('symbolic link', str(caught.exception))

    def test_a_failed_preparation_retains_the_earlier_set_and_leaves_none_publishable(self):
        self.prepare()
        earlier = {str(path.relative_to(self.published())): path.read_bytes()
                   for path in self.published().rglob('*') if path.is_file()}
        self.assertTrue(earlier)
        (self.staged / prepare.NOTICES[0]).write_bytes(b'a different licence')
        with self.assertRaises(verify.Refused):
            self.prepare()
        # Nothing remains where Tauri's resource list points.
        self.assertFalse(self.published().exists())
        # The earlier complete set survives, intact, out of the packaging path.
        retained = sorted((self.resources / prepare.RETAINED).iterdir())
        self.assertEqual(len(retained), 1)
        recovered = {str(path.relative_to(retained[0])): path.read_bytes()
                     for path in retained[0].rglob('*') if path.is_file()}
        self.assertEqual(recovered, earlier)

    def test_the_cli_refuses_without_authority_and_on_unselected_notices(self):
        arguments = ['--binary', str(self.binary), '--provenance', str(self.provenance),
                     '--staged-inputs', str(self.staged), '--input-manifest', str(MANIFEST),
                     '--uv-lock', str(UV_LOCK), '--resource-root', str(self.resources),
                     '--source-commit', SOURCE_COMMIT, '--source-tree', SOURCE_TREE]
        self.assertEqual(prepare.main(arguments), 3)
        self.assertFalse(self.published().exists())
        # With authority the CLI reaches validation and then the notice
        # comparison, which refuses here because the controlled payloads are not
        # the selected licence bytes the manifest binds. That is the correct
        # verdict for this checkout, and it proves the CLI wires the whole path.
        self.assertEqual(prepare.main(arguments + ['--build-id', BUILD_ID]), 2)
        self.assertFalse(self.published().exists())


class BuildOrderingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sidecar-order-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_the_tauri_hook_refuses_before_the_frontend_is_invoked(self):
        marker = self.root / 'frontend-was-invoked'
        tools = self.root / 'tools'
        tools.mkdir()
        bun = tools / 'bun'
        bun.write_text('#!/bin/sh\ntouch "$FRONTEND_MARKER"\n', encoding='utf-8')
        bun.chmod(0o755)
        config = json.loads((ROOT / 'tauri/src-tauri/tauri.conf.json').read_text())
        command = config['build']['beforeBuildCommand']
        self.assertIn('tauri_build_gate.py', command)
        environment = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'],
                           FRONTEND_MARKER=str(marker))
        for name in list(environment):
            if name.startswith('VOICEBOX_SIDECAR_'):
                environment.pop(name)
        result = subprocess.run(command, shell=True, cwd=ROOT / 'tauri', env=environment,
                                capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('sidecar gate refused before the frontend build', result.stderr)
        self.assertIn('bridge required', result.stderr)
        self.assertFalse(marker.exists(), 'the frontend build ran despite a refused gate')

    # The orchestrator stand-in reads the route it was actually given instead of
    # succeeding blindly: it records the requested script and --package value,
    # runs the single producer-bearing step, and only then runs the packaging
    # command the Makefile handed it. `$FAIL_PRODUCER` makes that one step fail
    # so the failure control can prove packaging is never reached.
    CONTROLLED_ORCHESTRATOR = (
        '#!/bin/sh\n'
        'set -eu\n'
        'script=""\n'
        'package=""\n'
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    --package) package="$2"; shift 2 ;;\n'
        '    -*) shift ;;\n'
        '    *) [ -n "$script" ] || script="$1"; shift ;;\n'
        '  esac\n'
        'done\n'
        'printf "%s\\n" "$script" > "$ROUTE_SCRIPT"\n'
        'printf "%s\\n" "$package" > "$ROUTE_PACKAGE"\n'
        'echo server-start >> "$ORDER_LOG"\n'
        'i=0\n'
        'while [ $i -lt 200 ]; do\n'
        '  if [ -e "$PACKAGING_MARKER" ]; then echo concurrent >> "$ORDER_LOG"; break; fi\n'
        '  i=$((i + 1))\n'
        'done\n'
        'if [ -n "${FAIL_PRODUCER:-}" ]; then\n'
        '  echo server-failed >> "$ORDER_LOG"\n'
        '  exit 3\n'
        'fi\n'
        'echo server-end >> "$ORDER_LOG"\n'
        '[ -z "$package" ] || sh -c "$package"\n')

    CONTROLLED_BUN = ('#!/bin/sh\nset -eu\ntouch "$PACKAGING_MARKER"\n'
                      'echo tauri-start >> "$ORDER_LOG"\necho tauri-end >> "$ORDER_LOG"\n')

    def build_order_fixture(self):
        """The real Makefile bytes with the producer-bearing step controlled.

        MIGRATION: the obsolete fixture wrote scripts/build-server.sh, but the
        unchanged Makefile now routes `build` through
        `$(PYTHON) -I tools/build_sidecar.py --package '<tauri command>'`. The
        stand-in is therefore supplied through the Makefile's own PYTHON
        variable, exactly as the bridge module's route control does. The recipe,
        the route, the ordering and the assertions under test stay real; only
        the expensive producer/consumer/artifact work is controlled.
        """
        fixture = self.root / 'repository'
        (fixture / 'scripts').mkdir(parents=True)
        (fixture / 'tauri').mkdir()
        (fixture / 'Makefile').write_bytes(MAKEFILE.read_bytes())
        orchestrator = self.root / 'controlled-orchestrator'
        orchestrator.write_text(self.CONTROLLED_ORCHESTRATOR, encoding='utf-8')
        orchestrator.chmod(0o755)
        tools = self.root / 'tools'
        tools.mkdir()
        bun = tools / 'bun'
        bun.write_text(self.CONTROLLED_BUN, encoding='utf-8')
        bun.chmod(0o755)
        paths = {name: fixture / name for name in
                 ('order.log', 'packaging-started', 'route-script', 'route-package')}
        environment = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'],
                           ORDER_LOG=str(paths['order.log']),
                           PACKAGING_MARKER=str(paths['packaging-started']),
                           ROUTE_SCRIPT=str(paths['route-script']),
                           ROUTE_PACKAGE=str(paths['route-package']))
        return fixture, orchestrator, paths, environment

    def run_make_build(self, fixture, orchestrator, environment):
        return subprocess.run(['/usr/bin/make', '-j4', 'build', 'PYTHON=' + str(orchestrator)],
                              cwd=fixture, env=environment, capture_output=True,
                              text=True, timeout=120)

    def test_make_j_cannot_package_while_the_sidecar_is_being_published(self):
        """The real Makefile with a controlled orchestrator and bun.

        The controlled orchestrator records whether the packaging step had
        already started beside it. That is an observation, not a wait: a
        serialized build simply never creates the packaging marker while it
        runs. It also records the route it was actually asked for, so this
        cannot pass with a stand-in that ignores its arguments.
        """
        fixture, orchestrator, paths, environment = self.build_order_fixture()
        result = self.run_make_build(fixture, orchestrator, environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = paths['order.log'].read_text().split()
        self.assertNotIn('concurrent', recorded)
        self.assertEqual(recorded, ['server-start', 'server-end', 'tauri-start', 'tauri-end'])
        # The requested route is the current one, and exactly one
        # producer-bearing operation ran under it.
        self.assertEqual(paths['route-script'].read_text().strip(), 'tools/build_sidecar.py')
        self.assertEqual(paths['route-package'].read_text().strip(),
                         'cd tauri && bun run tauri build')
        self.assertEqual(recorded.count('server-start'), 1)

    def test_a_failed_producer_bearing_step_never_reaches_packaging(self):
        """A failed producer-bearing step must stop the route before packaging.

        Same real Makefile, same route and same controlled stand-ins; only the
        single producer-bearing step fails. Nothing waits on a timer: the
        packaging marker is created by the controlled bun, which must never run.
        """
        fixture, orchestrator, paths, environment = self.build_order_fixture()
        environment['FAIL_PRODUCER'] = '1'
        result = self.run_make_build(fixture, orchestrator, environment)
        self.assertNotEqual(result.returncode, 0)
        recorded = paths['order.log'].read_text().split()
        self.assertEqual(recorded, ['server-start', 'server-failed'])
        self.assertNotIn('tauri-start', recorded)
        self.assertNotIn('tauri-end', recorded)
        self.assertFalse(paths['packaging-started'].exists(),
                         'packaging started despite a failed producer-bearing step')
        # The route was still the real one, and it was requested exactly once.
        self.assertEqual(paths['route-script'].read_text().strip(), 'tools/build_sidecar.py')
        self.assertEqual(recorded.count('server-start'), 1)


if __name__ == '__main__':
    unittest.main()

"""Shaped v3 consumer controls; no selected package, archive or target runs."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

if __package__:
    from .test_sidecar_gate import (Fixture, MANIFEST, UV_LOCK, BUILD_ID, SOURCE_COMMIT,
                                  SOURCE_TREE, canonical, sha, verify)
else:
    from test_sidecar_gate import (Fixture, MANIFEST, UV_LOCK, BUILD_ID, SOURCE_COMMIT,
                                 SOURCE_TREE, canonical, sha, verify)


OLD_MANIFEST_SHA256 = '63a8498f47b1c8c1daf78a4d62b3f98bb6ba53a29f8caeb9929395163b5f2d8e'
OUTPUT_PATH = 'python/kilix_transcribe/output.py'
OUTPUT_MODE_KEY = 'transcribe/src/kilix_transcribe/output.py'
OUTPUT_MODULE = 'kilix_transcribe.output'
OUTPUT_SHA256 = '7a97d97cceb8e7b8f3bf7daa427e81778ec27dedfdaeecb506ccbafb81257f43'


def old_manifest_bytes():
    """Reconstruct only historical metadata, never selected executable bytes."""
    manifest = json.loads(MANIFEST.read_bytes())
    manifest['schema'] = 'kilix.voicebox.sidecar-inputs/v1'
    manifest['sources']['transcribe']['members'] = [
        row for row in manifest['sources']['transcribe']['members']
        if row['output'] != OUTPUT_PATH]
    return (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode('utf-8')


def reseal(record):
    record['inputs']['completion_sha256'] = sha(
        verify.canonical_completion(record['inputs']['completion']))


def historical_record(fixture):
    record = copy.deepcopy(fixture.record)
    record['schema'] = 'kilix.voicebox.sidecar-provenance/candidate-v2'
    completion = record['inputs']['completion']
    completion['schema'] = 'kilix.voicebox.sidecar-stage/v1'
    completion['input_manifest_sha256'] = OLD_MANIFEST_SHA256
    completion['files'] = [row for row in completion['files'] if row['path'] != OUTPUT_PATH]
    del completion['observed_source_modes'][OUTPUT_MODE_KEY]
    record['build']['archive']['selected_modules'] = [
        row for row in record['build']['archive']['selected_modules']
        if row['name'] != OUTPUT_MODULE]
    reseal(record)
    return record


class V3ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()

    def validate(self, record=None, *, manifest=None, raw=None):
        return verify.validate(
            provenance_bytes=self.fixture.bytes(record) if raw is None else raw,
            binary_bytes_length=len(self.fixture.binary), binary_sha256=sha(self.fixture.binary),
            build_id=BUILD_ID, source_commit=SOURCE_COMMIT, source_tree=SOURCE_TREE,
            manifest_bytes=self.fixture.manifest_bytes if manifest is None else manifest,
            uv_lock_sha256=sha(UV_LOCK.read_bytes()))

    def refuses(self, change, fragment, *, reseal_completion=True):
        record = self.fixture.mutate(change)
        if reseal_completion:
            reseal(record)
        with self.assertRaises(verify.Refused) as error:
            self.validate(record)
        self.assertIn(fragment, str(error.exception))

    def test_exact_v3_population_and_output_identity_pass(self):
        report = self.validate()
        self.assertEqual(report['schema'], 'kilix.voicebox.sidecar-provenance/candidate-v3')
        completion = self.fixture.record['inputs']['completion']
        self.assertEqual(completion['schema'], 'kilix.voicebox.sidecar-stage/v2')
        self.assertEqual(len(completion['files']), 20)
        self.assertEqual(len(completion['observed_source_modes']), 20)
        self.assertEqual(len(report['selected_modules']), 16)
        self.assertEqual(report['distributions'], 35)
        self.assertIn(OUTPUT_MODULE, report['selected_modules'])
        row = next(row for row in completion['files'] if row['path'] == OUTPUT_PATH)
        self.assertEqual(row, dict(path=OUTPUT_PATH, bytes=3293, sha256=OUTPUT_SHA256,
                                   mode='0600', role='module'))
        self.assertEqual([row['path'] for row in report['plaintext']], sorted(verify.PLAINTEXT_PATHS))
        self.assertEqual(len(report['plaintext']), 4)

    def test_manifest_adoption_changes_only_schema_and_output_selection(self):
        raw = MANIFEST.read_bytes()
        self.assertEqual(sha(raw),
                         'fa26ea1c27217be6f9eb4509bd84e3c0b8f4c02a95913d5cd2cadd0c352b59d2')
        self.assertEqual(json.loads(raw)['schema'], 'kilix.voicebox.sidecar-inputs/v2')
        self.assertEqual(sha(old_manifest_bytes()), OLD_MANIFEST_SHA256)

    def test_formerly_accepted_complete_v2_record_refuses(self):
        record = historical_record(self.fixture)
        self.assertEqual(len(record['inputs']['completion']['files']), 19)
        self.assertEqual(len(record['build']['archive']['selected_modules']), 15)
        with self.assertRaisesRegex(verify.Refused, 'no downgrade'):
            self.validate(record, manifest=old_manifest_bytes())

    def test_old_and_unknown_outer_schemas_refuse(self):
        for version in (1, 2, 4):
            with self.subTest(version=version):
                self.refuses(lambda r, v=version: r.__setitem__(
                    'schema', 'kilix.voicebox.sidecar-provenance/candidate-v'+str(v)),
                    'no downgrade')

    def test_old_and_unknown_completion_schemas_refuse_even_with_new_digest(self):
        for version in (1, 3):
            with self.subTest(version=version):
                self.refuses(lambda r, v=version: r['inputs']['completion'].__setitem__(
                    'schema', 'kilix.voicebox.sidecar-stage/v'+str(v)), 'wrong schema')

    def test_old_actual_manifest_or_old_self_reported_manifest_refuses(self):
        with self.assertRaisesRegex(verify.Refused, 'not the selected one'):
            self.validate(manifest=old_manifest_bytes())
        self.refuses(lambda r: r['inputs']['completion'].__setitem__(
            'input_manifest_sha256', OLD_MANIFEST_SHA256), 'selected input manifest')
        with self.assertRaisesRegex(verify.Refused, 'not the selected one'):
            self.validate(manifest=MANIFEST.read_bytes()+b'\n')

    def test_missing_output_completion_module_and_mode_refuse(self):
        changes = [
            ('files', lambda r: r['inputs']['completion'].__setitem__('files', [
                row for row in r['inputs']['completion']['files'] if row['path'] != OUTPUT_PATH]),
             '20 rows'),
            ('module', lambda r: r['build']['archive'].__setitem__('selected_modules', [
                row for row in r['build']['archive']['selected_modules']
                if row['name'] != OUTPUT_MODULE]), '16 modules'),
            ('observed-mode', lambda r: r['inputs']['completion']['observed_source_modes'].pop(
                OUTPUT_MODE_KEY), 'missing keys'),
        ]
        for name, change, fragment in changes:
            with self.subTest(location=name):
                self.refuses(change, fragment)

    def test_consistent_old_population_with_new_schema_labels_refuses(self):
        record = historical_record(self.fixture)
        record['schema'] = verify.SCHEMA
        record['inputs']['completion']['schema'] = verify.STAGE_SCHEMA
        record['inputs']['completion']['input_manifest_sha256'] = verify.MANIFEST_SHA256
        reseal(record)
        with self.assertRaises(verify.Refused) as error:
            self.validate(record)
        self.assertIn('missing keys', str(error.exception))

    def test_output_selected_byte_identity_refuses_changes(self):
        for field, value in [('bytes', 3294), ('sha256', 'f'*64), ('mode', '0644'),
                             ('role', 'helper')]:
            with self.subTest(field=field):
                def change(record):
                    row = next(row for row in record['inputs']['completion']['files']
                               if row['path'] == OUTPUT_PATH)
                    row[field] = value
                self.refuses(change, 'unsupported mode' if field == 'mode'
                             else 'differs from the manifest')

    def test_output_archive_source_and_code_digest_controls(self):
        for field, value, fragment in [('source_sha256', 'f'*64, 'source digest differs'),
                                       ('code_sha256', 'not-a-digest', 'lowercase sha256')]:
            with self.subTest(field=field):
                def change(record):
                    row = next(row for row in record['build']['archive']['selected_modules']
                               if row['name'] == OUTPUT_MODULE)
                    row[field] = value
                self.refuses(change, fragment)

    def test_extra_provider_and_module_populations_refuse(self):
        self.refuses(lambda r: r['provider_endpoints'].append('another-provider.sock'),
                     'exact selected ordered pair')
        for name in ('kilix_transcribe.worker', 'kilix_qwen_tts.engine', 'kilix_content'):
            with self.subTest(module=name):
                def change(record):
                    row = next(row for row in record['build']['archive']['selected_modules']
                               if row['name'] == OUTPUT_MODULE)
                    row['name'] = name
                self.refuses(change, 'unselected module')
        self.refuses(lambda r: r['build']['archive']['selected_modules'].append(
            dict(name='extra', source_sha256='a'*64, code_sha256='b'*64)), 'member bound')
        self.refuses(lambda r: r['inputs']['completion']['files'].append(
            dict(path='python/kilix_transcribe/worker.py', bytes=1, sha256='a'*64,
                 mode='0600', role='module')), 'member bound')

    def test_inner_completion_hash_and_outer_encoding_remain_distinct(self):
        completion = self.fixture.record['inputs']['completion']
        self.assertNotEqual(sha(canonical(completion)), sha(verify.canonical_completion(completion)))
        self.refuses(lambda r: r['inputs'].__setitem__(
            'completion_sha256', sha(canonical(r['inputs']['completion']))),
            'C19 canonical completion encoding', reseal_completion=False)
        old_digest = historical_record(self.fixture)['inputs']['completion_sha256']
        self.refuses(lambda r: r['inputs'].__setitem__('completion_sha256', old_digest),
                     'C19 canonical completion encoding', reseal_completion=False)
        indented = (json.dumps(self.fixture.record, sort_keys=True, indent=2)+'\n').encode()
        with self.assertRaisesRegex(verify.Refused, 'canonical sorted compact'):
            self.validate(raw=indented)
        self.assertEqual(self.validate(raw=canonical(self.fixture.record))['schema'], verify.SCHEMA)

    def test_complete_selected_source_identity_is_bound(self):
        for source in ('voice', 'transcribe'):
            for field, value in [('repository', 'different-source'), ('commit', 'f'*40),
                                 ('tree', 'f'*40)]:
                with self.subTest(source=source, field=field):
                    self.refuses(lambda r, s=source, f=field, v=value:
                                 r['inputs']['completion']['sources'][s].__setitem__(f, v),
                                 'not the selected commit' if field == 'commit'
                                 else 'source identity differs from the selected manifest')

    def test_unchanged_seven_field_handoff_binds_v3_and_actual_bytes(self):
        with tempfile.TemporaryDirectory(prefix='v3-handoff-', dir=os.environ.get('TMPDIR')) as temp:
            root = Path(temp)
            binary = root/'sidecar'; binary.write_bytes(self.fixture.binary)
            provenance = root/'provenance.json'; provenance.write_bytes(self.fixture.bytes())
            handoff = root/'handoff'
            fields = dict(schema='kilix.voicebox.sidecar-output/candidate-v1', build_id=BUILD_ID,
                          binary='dist/sidecar', binary_sha256=sha(binary.read_bytes()),
                          binary_bytes=str(binary.stat().st_size), provenance='dist/provenance.json',
                          provenance_sha256=sha(provenance.read_bytes()))
            handoff.write_text(''.join(k+'='+v+'\n' for k, v in fields.items()), encoding='ascii')
            self.assertEqual(len(handoff.read_text().splitlines()), 7)
            self.assertEqual(verify.load_handoff(handoff), fields)
            argv = ['--binary', str(binary), '--provenance', str(provenance),
                    '--input-manifest', str(MANIFEST), '--uv-lock', str(UV_LOCK),
                    '--source-commit', SOURCE_COMMIT, '--source-tree', SOURCE_TREE,
                    '--handoff', str(handoff)]
            self.assertEqual(verify.main(argv), 0)
            binary.write_bytes(b'changed supplied bytes')
            self.assertEqual(verify.main(argv), 2)
            binary.write_bytes(self.fixture.binary)
            provenance.write_bytes(self.fixture.bytes(historical_record(self.fixture)))
            self.assertEqual(verify.main(argv), 2)


if __name__ == '__main__':
    unittest.main()

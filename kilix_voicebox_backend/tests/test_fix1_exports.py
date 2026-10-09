"""Real empty/metadata/populated profile roundtrips and permission controls."""
import asyncio
import io
import json
import math
from pathlib import Path
import tempfile
import struct
import unittest
from unittest.mock import patch
import zipfile
import wave

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from kilix_voicebox_backend import export_import
from kilix_voicebox_backend.database import Base, VoiceProfile, ProfileSample
from kilix_voicebox_backend.tests.test_kilix_boundary import wav_bytes


class ProfileRoundtripTests(unittest.TestCase):
    def session(self):
        engine = create_engine('sqlite:///:memory:')
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        self.addCleanup(db.close)
        return db

    def test_empty_profile_roundtrips_with_and_without_explicit_sample_permission(self):
        for permission in (False, True):
            with self.subTest(permission=permission), tempfile.TemporaryDirectory() as directory:
                db = self.session()
                db.add(VoiceProfile(id='empty', name='Named voice group', description=None, language='en'))
                db.commit()
                raw = export_import.export_profile_to_zip('empty', db, include_samples=permission)
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    self.assertEqual(json.loads(archive.read('manifest.json'))['samples_included'], permission)
                    self.assertEqual(json.loads(archive.read('samples.json')), {})
                with patch('kilix_voicebox_backend.profiles.config.get_profiles_dir', return_value=Path(directory)/'profiles'):
                    result = asyncio.run(export_import.import_profile_from_zip(raw, db))
                self.assertEqual(result.name, 'Named voice group (1)')
                self.assertEqual(db.query(VoiceProfile).count(), 2)
                self.assertEqual(db.query(ProfileSample).count(), 0)

    def test_populated_profile_explicit_opt_in_roundtrips_and_metadata_only_omits_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'profiles'
            sample = root/'old/sample.wav'
            sample.parent.mkdir(mode=0o700, parents=True)
            output = io.BytesIO()
            with wave.open(output, 'wb') as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(24_000)
                stream.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(i * math.tau * 440 / 24000)))
                                           for i in range(48_000)))
            sample.write_bytes(output.getvalue())
            db = self.session()
            db.add(VoiceProfile(id='old', name='Fixture voice', description=None, language='en'))
            db.add(ProfileSample(id='sample', profile_id='old', audio_path=str(sample), reference_text='Synthetic sample'))
            db.commit()
            with patch('kilix_voicebox_backend.profiles.config.get_profiles_dir', return_value=root):
                for permission in (False, True):
                    raw = export_import.export_profile_to_zip('old', db, include_samples=permission)
                    result = asyncio.run(export_import.import_profile_from_zip(raw, db))
                    self.assertEqual(db.query(ProfileSample).filter_by(profile_id=result.id).count(), int(permission))

    def test_archive_samples_without_permission_and_missing_payloads_refuse(self):
        for permission, payload in ((False, True), (True, False)):
            with self.subTest(permission=permission, payload=payload):
                db = self.session()
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, 'w') as archive:
                    archive.writestr('manifest.json', json.dumps({'schema':'kilix.voicebox.profile-export/candidate-v1',
                        'version':'1.0','profile':{'name':'Fixture','description':None,'language':'en'},
                        'avatar':None,'samples_included':permission}))
                    archive.writestr('samples.json', json.dumps({'sample.wav':'Synthetic sample'}))
                    if payload:
                        archive.writestr('samples/sample.wav', wav_bytes(240))
                with self.assertRaises((ValueError, KeyError)):
                    asyncio.run(export_import.import_profile_from_zip(stream.getvalue(), db))
                self.assertEqual(db.query(VoiceProfile).count(), 0)

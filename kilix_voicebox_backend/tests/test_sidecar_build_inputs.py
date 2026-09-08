"""Producer input/immutable consumption controls; no PyInstaller build fixture."""
from __future__ import annotations

import base64
import copy
import csv
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock
import zipfile

from kilix_voicebox_backend import build_binary as build


class BuildInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sidecar-build-control-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fd_before = len(os.listdir("/proc/self/fd"))

    def tearDown(self):
        self.assertEqual(len(os.listdir("/proc/self/fd")), self.fd_before)

    def test_missing_explicit_inputs_never_allocates_or_imports_builder(self):
        with self.assertRaisesRegex(build.BuildError, "explicit absolute"):
            build.build_server(self.root / "build", "01234567")
        self.assertFalse((self.root / "build").exists())

    def test_existing_directory_and_dangling_link_are_preserved(self):
        output = self.root / "build"
        output.mkdir()
        (output / "sentinel").write_bytes(b"preserve")
        with self.assertRaises(FileExistsError):
            build.build_server(output, "01234567")
        self.assertEqual((output / "sentinel").read_bytes(), b"preserve")
        alias = self.root / "link"
        alias.symlink_to(self.root / "missing")
        with self.assertRaises(FileExistsError):
            build.build_server(alias, "01234567")
        self.assertTrue(alias.is_symlink())
        self.assertFalse(alias.exists())

    def test_invalid_identity_refuses_before_allocation(self):
        for value in ("ABCDEF01", "short", 10, False, "0" * 65):
            with self.subTest(value=value), self.assertRaises(build.BuildError):
                build.build_server(self.root / "build", value)
        self.assertFalse((self.root / "build").exists())

    def test_strict_metadata_rejects_duplicates_numbers_encoding_depth(self):
        cases = (b'{"a":1,"a":2}', b'{"a":{"b":1,"b":2}}', b'{"a":NaN}', b'{"a":1.0}',
                 b'{"a":-1}', b'{"a":9007199254740992}', b'"\xff"', b'{} {}', b'[' * 35 + b'0' + b']' * 35)
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(build.BuildError):
                build._strict_json(raw)
        self.assertEqual(build._strict_json(b'{"a":false,"b":0}'), {"a": False, "b": 0})

    def test_real_sealed_bundle_resists_original_and_reopened_writes(self):
        population = build.Population(self.root / "snapshot")
        population.add("source/test.py", b"original")
        descriptor, digest = population.seal({"control": True})
        try:
            raw = os.read(descriptor, 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            (population.root / "source/test.py").write_bytes(b"mutation")
            os.lseek(descriptor, 0, os.SEEK_SET)
            self.assertEqual(os.read(descriptor, 65536), raw)
            self.assertEqual(fcntl.fcntl(descriptor, 1034), 15)  # Linux UAPI GET_SEALS
            with self.assertRaises(PermissionError):
                os.write(descriptor, b"x")
            reopened = os.open(f"/proc/self/fd/{descriptor}", os.O_RDWR)
            try:
                with self.assertRaises(PermissionError):
                    os.pwrite(reopened, b"x", 0)
            finally:
                os.close(reopened)
            with self.assertRaises(build.BuildError):
                population.verify()
        finally:
            os.close(descriptor)

    def test_population_extra_duplicate_and_path_refusals(self):
        population = build.Population(self.root / "snapshot")
        population.add("source/a", b"a")
        for name in ("source/a", "../x", "/x", "a//b", "a/./b", "a\\b", "a\0b"):
            with self.subTest(name=name), self.assertRaises(build.BuildError):
                population.add(name, b"b")
        (population.root / "extra").write_bytes(b"x")
        with self.assertRaises(build.BuildError):
            population.verify()

    def test_read_refuses_link_fifo_and_changed_original(self):
        original = self.root / "input"
        original.write_bytes(b"original")
        watches = {}
        build._read(original, watches)
        original.write_bytes(b"mutated!")
        with self.assertRaises(build.BuildError):
            build._watch(watches, hashes=True)
        linked = self.root / "alias"
        linked.symlink_to(original)
        with self.assertRaises(OSError):
            build._read(linked)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(build.BuildError):
            build._read(fifo)

    def test_code_binding_excludes_only_filename(self):
        first = compile("def f():\n return 1\n", "/first", "exec")
        moved = compile("def f():\n return 1\n", "/other", "exec")
        changed = compile("def f():\n return 2\n", "/first", "exec")
        lines = compile("\ndef f():\n return 1\n", "/first", "exec")
        self.assertEqual(build._code_digest(first), build._code_digest(moved))
        self.assertNotEqual(build._code_digest(first), build._code_digest(changed))
        self.assertNotEqual(build._code_digest(first), build._code_digest(lines))

    def test_exact_git_source_refuses_dirty_untracked_and_hidden_index(self):
        repository = self.root / "repository"
        repository.mkdir()
        environment = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        def git(*args):
            return subprocess.run(["/usr/bin/git", "-C", str(repository), *args], env=environment, check=True, capture_output=True)
        git("init", "-q")
        (repository / "uv.lock").write_bytes(b"version = 1\n")
        git("add", "uv.lock")
        git("-c", "user.name=itsmygithubacct", "-c", "user.email=itsmygithubacct@users.noreply.github.com", "commit", "-qm", "fixture")
        with mock.patch.object(build, "ROOT", repository):
            population = build.Population(self.root / "exact")
            result = build._source(population, {})
            self.assertEqual(result["source_commit"], git("rev-parse", "HEAD").stdout.decode().strip())
            (repository / "extra").write_bytes(b"x")
            with self.assertRaises(build.BuildError):
                build._source(build.Population(self.root / "untracked"), {})
            (repository / "extra").unlink()
            git("update-index", "--assume-unchanged", "uv.lock")
            with self.assertRaises(build.BuildError):
                build._source(build.Population(self.root / "hidden"), {})
            git("update-index", "--no-assume-unchanged", "uv.lock")
            (repository / "uv.lock").write_bytes(b"changed")
            with self.assertRaises(build.BuildError):
                build._source(build.Population(self.root / "dirty"), {})

    def test_wheel_record_covers_every_original_member(self):
        files = {"sample/__init__.py": b"answer = 1\n", "sample-1.dist-info/METADATA": b"Name: sample\nVersion: 1\n"}
        rows = [[name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode(), str(len(raw))]
                for name, raw in files.items()]
        rows.append(["sample-1.dist-info/RECORD", "", ""])
        stream = io.StringIO()
        csv.writer(stream).writerows(rows)
        files[rows[-1][0]] = stream.getvalue().encode()
        def archive():
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, "w") as zipped:
                for name, content in files.items():
                    zipped.writestr(name, content)
            return raw.getvalue()
        members, record = build._wheel(archive(), "sample", "1")
        self.assertEqual(len(members), 3)
        self.assertEqual(record, hashlib.sha256(files[rows[-1][0]]).hexdigest())
        files["sample/__init__.py"] = b"answer = 2\n"
        with self.assertRaises(build.BuildError):
            build._wheel(archive(), "sample", "1")

    def test_archive_inspection_refuses_unknown_missing_duplicate_and_mutated_rows(self):
        digest = "a" * 64
        modules = [{"name": f"module{i:02}", "sha256": digest} for i in range(15)]
        plain = [{"path": f"plain{i}", "bytes": 1, "sha256": digest} for i in range(4)]
        value = {"selected_modules": [{"name": row["name"], "source_sha256": digest, "code_sha256": digest} for row in modules],
                 "plaintext": plain, "entries": [{"path": "PYZ.pyz", "bytes": 1, "kind": "z", "sha256": digest}], "options": []}
        self.assertEqual(build._archive(value, modules, plain), value)
        for change in ("unknown", "module", "plaintext", "duplicate", "bool", "entry", "option"):
            altered = copy.deepcopy(value)
            if change == "unknown": altered["future"] = True
            if change == "module": altered["selected_modules"][0]["source_sha256"] = "b" * 64
            if change == "plaintext": altered["plaintext"][0]["sha256"] = "b" * 64
            if change == "duplicate": altered["entries"] *= 2
            if change == "bool": altered["entries"][0]["bytes"] = True
            if change == "entry": altered["entries"][0]["future"] = True
            if change == "option": altered["options"] = ["same", "same"]
            with self.subTest(change=change), self.assertRaises(build.BuildError):
                build._archive(altered, modules, plain)

    def test_original_deadline_kills_and_reaps_only_owned_process_closes_bundle(self):
        stage = self.root / "output"
        stage.mkdir()
        population = build.Population(self.root / "snapshot")
        population.add("source/a", b"a")
        descriptor, digest = population.seal({})
        popen = subprocess.Popen
        unrelated = popen(["/bin/sleep", "10"])
        owned = []
        def sleeper(*args, **kwargs):
            # Explicit controlled launch substitute; no namespace credit.
            child = popen(["/bin/sleep", "10"], **kwargs)
            owned.append(child)
            return child
        started = time.monotonic()
        try:
            with mock.patch.object(build.subprocess, "Popen", side_effect=sleeper):
                with self.assertRaisesRegex(build.BuildError, "original build deadline"):
                    build._run_isolated(stage, self.root, descriptor, digest, {}, started + 0.15)
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(len(owned), 1)
            self.assertIsNotNone(owned[0].returncode)
            self.assertTrue(owned[0].stdout.closed)
            self.assertIsNone(unrelated.poll())
            with self.assertRaises(OSError): os.fstat(descriptor)
            self.assertFalse((stage / build.HANDOFF_NAME).exists())
        finally:
            unrelated.kill()
            unrelated.wait()

    def test_launch_exception_closes_bundle_without_publication(self):
        stage = self.root / "output"
        stage.mkdir()
        population = build.Population(self.root / "snapshot")
        population.add("source/a", b"a")
        descriptor, digest = population.seal({})
        with mock.patch.object(build.subprocess, "Popen", side_effect=OSError("allocation refused")):
            with self.assertRaises(OSError):
                build._run_isolated(stage, self.root, descriptor, digest, {}, time.monotonic() + 1)
        with self.assertRaises(OSError): os.fstat(descriptor)
        self.assertFalse((stage / build.HANDOFF_NAME).exists())


INPUTS = {"voice_source": "VOICEBOX_STAGE_TEST_VOICE_SOURCE", "transcribe_source": "VOICEBOX_STAGE_TEST_TRANSCRIBE_SOURCE",
          "transcribe_wheel": "VOICEBOX_STAGE_TEST_TRANSCRIBE_WHEEL"}


@unittest.skipUnless(all(os.environ.get(value) for value in INPUTS.values()), "explicit exact source/wheel inputs required")
class SelectedProjectionTests(unittest.TestCase):
    setUp = BuildInputTests.setUp
    tearDown = BuildInputTests.tearDown
    def test_actual_production_stage_completion_payloads_and_mutation(self):
        actual = {key: Path(os.environ[value]) for key, value in INPUTS.items()}
        stage = self.root / "build"
        stage.mkdir(mode=0o700)
        population = build.Population(self.root / "snapshot")
        inputs, modules, expected = build._projection(stage, population, **actual)
        self.assertEqual((len(modules), len(expected), len(inputs["plaintext"])), (15, 19, 4))
        self.assertEqual(len(population.rows), 19)
        build._stage_inputs_unchanged(stage, inputs)
        (stage / "selected-inputs/python/voicelib/microphone_supervisor.py").write_bytes(b"changed")
        with self.assertRaises(build.BuildError):
            build._stage_inputs_unchanged(stage, inputs)

    def test_completion_unknown_identity_and_notice_refusals(self):
        actual = {key: Path(os.environ[value]) for key, value in INPUTS.items()}
        stage = self.root / "build"
        stage.mkdir(mode=0o700)
        inputs, _, expected = build._projection(stage, build.Population(self.root / "snapshot"), **actual)
        manifest = json.loads((build.ROOT / ".kilix-import/sidecar-inputs.json").read_bytes())
        for change in ("unknown", "wheel", "notice", "source"):
            altered = copy.deepcopy(inputs["completion"])
            if change == "unknown": altered["future"] = True
            if change == "wheel": altered["wheel"]["sha256"] = "0" * 64
            if change == "notice": altered["files"] = altered["files"][1:]
            if change == "source": altered["sources"]["voice"]["tree"] = "0" * 40
            with self.subTest(change=change), self.assertRaises(build.BuildError):
                build._completion(altered, manifest, expected)


if __name__ == "__main__":
    unittest.main()

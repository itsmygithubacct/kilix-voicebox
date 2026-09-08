"""Offline staging controls; selected source/wheel paths are explicit test inputs."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import warnings
import zipfile


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("stage_sidecar_inputs", ROOT / "tools/stage_sidecar_inputs.py")
assert SPEC is not None and SPEC.loader is not None
stage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage)
MANIFEST = json.loads((ROOT / ".kilix-import/sidecar-inputs.json").read_bytes())
INPUT_ENV = {
    "voice_source": "VOICEBOX_STAGE_TEST_VOICE_SOURCE",
    "transcribe_source": "VOICEBOX_STAGE_TEST_TRANSCRIBE_SOURCE",
    "transcribe_wheel": "VOICEBOX_STAGE_TEST_TRANSCRIBE_WHEEL",
}


class StructuralTests(unittest.TestCase):
    def test_manifest_and_projection_are_exact(self):
        raw = (ROOT / ".kilix-import/sidecar-inputs.json").read_bytes()
        self.assertEqual(stage.digest(raw), stage.MANIFEST_SHA256)
        rows = [row for spec in MANIFEST["sources"].values() for row in spec["members"]]
        self.assertEqual(MANIFEST["schema"], "kilix.voicebox.sidecar-inputs/v2")
        self.assertEqual(len(rows), 20)
        self.assertEqual(sum(r["role"] == "module" for r in rows), 16)
        self.assertEqual(sum(r["role"] == "helper" for r in rows), 1)
        self.assertEqual(sum(r["role"] == "license" for r in rows), 2)
        self.assertEqual(sum(r["role"] == "version" for r in rows), 1)
        self.assertEqual({r["path"] for r in rows if r["role"] == "helper"},
                         {"voicelib/microphone_supervisor.py"})
        self.assertFalse(any(r["output"].endswith(("/worker.py", "/cli.py", "/content.py"))
                             for r in rows))

    def test_archive_structure_refuses_paths_duplicates_links_and_sizes(self):
        cases = ["../escape", "/escape", "a//b", "a/./b", "a/../b", "a\\b",
                 "duplicate", "link", "directory", "oversize"]
        for case in cases:
            with self.subTest(case=case):
                buffer = io.BytesIO()
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                        info = zipfile.ZipInfo(case if case not in ("link", "directory", "oversize") else "file")
                        info.create_system = 3
                        info.compress_type = zipfile.ZIP_DEFLATED
                        mode = stat.S_IFLNK if case == "link" else stat.S_IFDIR if case == "directory" else stat.S_IFREG
                        info.external_attr = (mode | 0o600) << 16
                        archive.writestr(info, b"x" * (stage.MAX_BYTES + 1) if case == "oversize" else b"x")
                        if case == "duplicate":
                            archive.writestr(info, b"y")
                with self.assertRaises(stage.StageError) as caught:
                    stage.wheel_members(buffer.getvalue(), MANIFEST["wheel"])
                self.assertNotEqual(str(caught.exception), "whole wheel identity mismatch")

    def test_local_git_deadline_reaps_only_its_child(self):
        popen = subprocess.Popen
        owned = []
        unrelated = popen(["/bin/sleep", "10"])
        def slow(*args, **kwargs):
            child = popen(["/bin/sleep", "10"], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            owned.append(child)
            return child
        try:
            with contextlib.ExitStack() as stack:
                root = stage.Handles(stack).absolute(ROOT)
                with mock.patch.object(stage.subprocess, "Popen", side_effect=slow):
                    with self.assertRaises(stage.StageError):
                        stage._git(root, time.monotonic() + 0.15, "rev-parse", "HEAD")
            self.assertEqual(len(owned), 1)
            self.assertIsNotNone(owned[0].returncode)
            self.assertTrue(owned[0].stdout.closed)
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.kill()
            unrelated.wait()


@unittest.skipUnless(all(os.environ.get(name) for name in INPUT_ENV.values()),
                     "explicit selected offline source/wheel inputs required")
class SelectedInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual = {key: Path(os.environ[name]) for key, name in INPUT_ENV.items()}
        cls.templates = tempfile.TemporaryDirectory(prefix="voicebox-stage-templates-")
        cls.addClassCleanup(cls.templates.cleanup)
        cls.template = Path(cls.templates.name)
        environment = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
                       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        for key in ("voice_source", "transcribe_source"):
            dest = cls.template / key
            subprocess.run(["/usr/bin/git", "clone", "--quiet", "--shared", "--no-checkout",
                            str(cls.actual[key]), str(dest)], check=True, env=environment)
            source_key = "voice" if key == "voice_source" else "transcribe"
            subprocess.run(["/usr/bin/git", "-C", str(dest), "checkout", "--quiet", "--detach",
                            MANIFEST["sources"][source_key]["commit"]], check=True, env=environment)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="voicebox-stage-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "stage"
        self.kwargs = {**self.actual, "destination": self.output}
        self.fd_before = len(os.listdir("/proc/self/fd"))

    def tearDown(self):
        self.assertEqual(len(os.listdir("/proc/self/fd")), self.fd_before)

    def mutable_sources(self):
        for key in ("voice_source", "transcribe_source"):
            path = self.root / key
            shutil.copytree(self.template / key, path)
            self.kwargs[key] = path

    def cli(self, environment=None):
        command = ["/usr/bin/python3", "-I", "-B", "-S", str(ROOT / "tools/stage_sidecar_inputs.py")]
        for name, path in self.kwargs.items():
            command.extend(["--" + name.replace("_", "-"), str(path)])
        return subprocess.run(command, env=environment, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=40, check=False)

    def assert_refused(self, cli=False, allocated=False):
        if cli:
            result = self.cli()
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(b"refused", result.stderr)
        else:
            with self.assertRaises(stage.StageError):
                stage.stage(**self.kwargs)
        self.assertFalse((self.output / "completion.json").exists())
        if not allocated:
            self.assertFalse(os.path.lexists(self.output))

    def validate(self, completion):
        self.assertEqual(completion, json.loads((self.output / "completion.json").read_bytes()))
        expected = {row["path"] for row in completion["files"]} | {"completion.json"}
        observed = {p.relative_to(self.output).as_posix() for p in self.output.rglob("*") if p.is_file()}
        self.assertEqual(observed, expected)
        for row in completion["files"]:
            path = self.output / row["path"]
            self.assertEqual(path.stat().st_size, row["bytes"])
            self.assertEqual(stage.digest(path.read_bytes()), row["sha256"])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), int(row["mode"], 8))
        for path in [self.output, *self.output.rglob("*")]:
            if path.is_dir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        microphone = (self.output / "python/voicelib/microphone.py").read_text()
        self.assertIn('"/usr/bin/python3", "-I", "-B", "-S"', microphone)
        self.assertEqual((self.output / "python/VERSION").read_bytes(),
                         (self.actual["voice_source"] / "VERSION").read_bytes())

    def test_actual_api_and_cli_reproduce_exact_stage(self):
        first = stage.stage(**self.kwargs)
        self.validate(first)
        self.output = self.root / "second"
        self.kwargs["destination"] = self.output
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        second = json.loads((self.output / "completion.json").read_bytes())
        self.validate(second)
        self.assertEqual(first, second)
        self.assertEqual(json.loads(result.stdout)["files"], 20)
        self.assertEqual(second["schema"], "kilix.voicebox.sidecar-stage/v2")

    def test_exact_added_output_is_causal_for_all_transcribe_imports(self):
        completion = stage.stage(**self.kwargs)
        self.validate(completion)
        added = self.output / "python/kilix_transcribe/output.py"
        original = added.read_bytes()
        self.assertEqual(stage.digest(original), "7a97d97cceb8e7b8f3bf7daa427e81778ec27dedfdaeecb506ccbafb81257f43")
        names = ["kilix_transcribe", "kilix_transcribe.output", "kilix_transcribe.owned",
                 "kilix_transcribe.protocol", "kilix_transcribe.results", "kilix_transcribe.runtime",
                 "kilix_transcribe.service", "kilix_transcribe.surface"]
        code = ("import importlib,json,pathlib,sys;root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root));"
                "mods=[importlib.import_module(n) for n in sys.argv[2:]];"
                "assert all(pathlib.Path(m.__file__).is_relative_to(root) for m in mods);"
                "print(json.dumps({m.__name__:m.__file__ for m in mods}))")
        def run():
            return subprocess.run([sys.executable, "-I", "-B", "-S", "-c", code,
                                   str(self.output / "python"), *names], capture_output=True, timeout=5)
        added.unlink()
        old = run()
        self.assertEqual(old.returncode, 1)
        self.assertIn(b"No module named 'kilix_transcribe.output'", old.stderr)
        added.write_bytes(original)
        added.chmod(0o600)
        corrected = run()
        self.assertEqual(corrected.returncode, 0, corrected.stderr)
        self.assertEqual(set(json.loads(corrected.stdout)), set(names))
        self.validate(completion)

    def test_old_manifest_authority_refuses_before_allocation(self):
        with mock.patch.object(stage, "MANIFEST_SHA256", "63a8498f47b1c8c1daf78a4d62b3f98bb6ba53a29f8caeb9929395163b5f2d8e"):
            self.assert_refused()

    def test_missing_and_changed_modules_helper_version_notices(self):
        self.mutable_sources()
        cases = [("voice_source", "voicelib/audio.py"),
                 ("voice_source", "voicelib/microphone_supervisor.py"),
                 ("voice_source", "VERSION"), ("voice_source", "LICENSE"),
                 ("transcribe_source", "src/kilix_transcribe/service.py"),
                 ("transcribe_source", "src/kilix_transcribe/output.py"),
                 ("transcribe_source", "LICENSE")]
        for key, name in cases:
            path = self.kwargs[key] / name
            original = path.read_bytes()
            for action in ("missing", "changed"):
                with self.subTest(source=key, member=name, action=action):
                    if action == "missing":
                        path.unlink()
                    else:
                        path.write_bytes(original + b"\n")
                    self.assert_refused(cli=True)
                    path.write_bytes(original)

    def test_wrong_commit_and_wrong_source_root(self):
        self.mutable_sources()
        repo = self.kwargs["voice_source"]
        head = (repo / ".git/HEAD").read_bytes()
        parent = subprocess.check_output(["/usr/bin/git", "-C", str(repo), "rev-parse", "HEAD^"])
        (repo / ".git/HEAD").write_bytes(parent)
        self.assert_refused(cli=True)
        (repo / ".git/HEAD").write_bytes(head)
        self.kwargs["voice_source"] = self.kwargs["transcribe_source"]
        self.assert_refused()

    def test_wrong_wheel_and_invalid_archives_use_production_cli(self):
        directory = self.root / "wheel"
        directory.mkdir()
        path = directory / self.actual["transcribe_wheel"].name
        self.kwargs["transcribe_wheel"] = path
        raw = self.actual["transcribe_wheel"].read_bytes()
        path.write_bytes(raw[:-1])
        self.assert_refused(cli=True)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("../escape", b"never extract")
        self.assert_refused(cli=True)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("duplicate", b"one")
                archive.writestr("duplicate", b"two")
        self.assert_refused(cli=True)
        self.assertFalse((self.root / "escape").exists())

    def test_symlink_hardlink_special_and_mode_inputs(self):
        self.mutable_sources()
        path = self.kwargs["voice_source"] / "LICENSE"
        raw = path.read_bytes()
        target = self.root / "notice"
        target.write_bytes(raw)
        for kind in ("symlink", "hardlink", "fifo", "executable", "shared"):
            with self.subTest(kind=kind):
                path.unlink()
                if kind == "symlink":
                    path.symlink_to(target)
                elif kind == "hardlink":
                    os.link(target, path)
                elif kind == "fifo":
                    os.mkfifo(path, 0o600)
                else:
                    path.write_bytes(raw)
                    path.chmod(0o700 if kind == "executable" else 0o666)
                self.assert_refused()
                path.unlink()
                path.write_bytes(raw)

    def test_ancestor_symlink_and_lexical_traversal(self):
        link = self.root / "source-link"
        link.symlink_to(self.actual["voice_source"], target_is_directory=True)
        self.kwargs["voice_source"] = link
        self.assert_refused(cli=True)
        self.kwargs["voice_source"] = str(self.actual["voice_source"]) + "/../kilix-voice-snapshot"
        self.assert_refused()

    def test_existing_stale_and_dangling_destination_preserve_bytes(self):
        for kind in ("directory", "file", "symlink"):
            with self.subTest(kind=kind):
                if kind == "directory":
                    self.output.mkdir()
                    (self.output / "sentinel").write_bytes(b"keep")
                elif kind == "file":
                    self.output.write_bytes(b"keep")
                else:
                    self.output.symlink_to(self.root / "absent")
                with self.assertRaises(stage.StageError):
                    stage.stage(**self.kwargs)
                if kind == "directory":
                    self.assertEqual((self.output / "sentinel").read_bytes(), b"keep")
                    shutil.rmtree(self.output)
                else:
                    if kind == "file":
                        self.assertEqual(self.output.read_bytes(), b"keep")
                    else:
                        self.assertFalse((self.root / "absent").exists())
                    self.output.unlink()

    def test_nonprivate_parent_refuses_without_chmod(self):
        self.root.chmod(0o755)
        self.assert_refused(cli=True)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o755)

    def test_poisoned_environment_cannot_supply_python_or_git(self):
        poison = self.root / "poison"
        poison.mkdir()
        marker = self.root / "executed"
        (poison / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').write('bad')\n")
        (poison / "git").write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
        (poison / "git").chmod(0o700)
        environment = {**os.environ, "PATH": str(poison), "PYTHONPATH": str(poison),
                       "PYTHONHOME": str(poison), "GIT_DIR": str(poison),
                       "GIT_WORK_TREE": str(poison), "GIT_OBJECT_DIRECTORY": str(poison),
                       "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.worktree",
                       "GIT_CONFIG_VALUE_0": str(poison), "LD_PRELOAD": ""}
        result = self.cli(environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.validate(json.loads((self.output / "completion.json").read_bytes()))

    def test_input_mutation_after_validation_refuses_completion(self):
        self.mutable_sources()
        original = stage._write_file
        changed = False
        def write(*args):
            nonlocal changed
            original(*args)
            if not changed:
                changed = True
                path = self.kwargs["voice_source"] / "voicelib/microphone_supervisor.py"
                path.write_bytes(path.read_bytes() + b"\n")
        with mock.patch.object(stage, "_write_file", side_effect=write):
            self.assert_refused(allocated=True)

    def test_staged_mutation_refuses_completion(self):
        self.mutable_sources()
        original = stage._write_file
        def write(parent, name, data):
            original(parent, name, data)
            if name == ".completion.pending":
                target = self.output / "python/voicelib/audio.py"
                target.write_bytes(b"changed")
        with mock.patch.object(stage, "_write_file", side_effect=write):
            self.assert_refused(allocated=True)

    def test_final_record_and_population_mutations_refuse_completion(self):
        original = stage._write_file
        for kind in ("record", "extra"):
            with self.subTest(kind=kind):
                self.output = self.root / kind
                self.kwargs["destination"] = self.output
                def write(parent, name, data):
                    original(parent, name, data)
                    if name == ".completion.pending":
                        target = self.output / (name if kind == "record" else "unexpected")
                        target.write_bytes(b"changed")
                with mock.patch.object(stage, "_write_file", side_effect=write):
                    self.assert_refused(allocated=True)

    def test_same_byte_source_replacement_refuses_identity(self):
        self.mutable_sources()
        original = stage._write_file
        def write(parent, name, data):
            original(parent, name, data)
            if name == ".completion.pending":
                target = self.kwargs["voice_source"] / "LICENSE"
                raw = target.read_bytes()
                target.rename(target.with_name("LICENSE.old"))
                target.write_bytes(raw)
        with mock.patch.object(stage, "_write_file", side_effect=write):
            self.assert_refused(allocated=True)

    def test_interruption_retains_only_incomplete_private_output(self):
        with mock.patch.object(stage, "_write_file", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                stage.stage(**self.kwargs)
        self.assertTrue(self.output.is_dir())
        self.assertFalse((self.output / "completion.json").exists())
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()

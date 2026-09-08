"""Controls for the sidecar producer/collector output contract.

Two boundaries are exercised here with no PyInstaller, sidecar, Tauri or model
build:

* the real ``scripts/build-server.sh`` against a controlled producer that is
  handed the script's own arguments, so the collector's selection of outputs is
  observed rather than assumed; and
* the real ``kilix_voicebox_backend.build_binary`` producer against a stubbed
  PyInstaller entry point, so the arguments it passes and the handoff it writes
  are observed directly.

The old geometry is kept as an explicit control: a producer that writes only to
the package-relative ``dist`` directory, which is what this module did before
the correction, must now fail the build instead of being silently collected
from a guessed path.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kilix_voicebox_backend import build_binary


ROOT = build_binary.ROOT
BUILD_SCRIPT = ROOT / "scripts" / "build-server.sh"
PLATFORM = "x86_64-unknown-linux-gnu"
HANDOFF_SCHEMA = "kilix.voicebox.sidecar-output/candidate-v1"

RUSTC = """#!/bin/sh
set -eu
test "${1-}" = -vV
printf 'rustc 1.0.0\\nhost: %s\\n' '""" + PLATFORM + """'
"""

# A controlled stand-in for `uv run ... build_binary.py`. It reads the staging
# directory and build identity out of the arguments the collector actually
# passed -- not out of a hardcoded path -- and records them, so a collector that
# stopped naming its own output would fail these controls rather than pass them.
PRODUCER = '''#!/usr/bin/env python3
import hashlib
import json
import os
import sys
from pathlib import Path

SCHEMA = "kilix.voicebox.sidecar-output/candidate-v1"
PLAN = json.loads(
    Path(__file__).with_name("producer-plan.json").read_text(encoding="utf-8")
)


def refuse(message):
    sys.stderr.write("controlled-producer: " + message + "\\n")
    raise SystemExit(3)


for required in ("PYTHONNOUSERSITE", "PYTHONSAFEPATH"):
    if os.environ.get(required) != "1":
        refuse("the collector did not set " + required)
for forbidden in ("PYTHONPATH", "PYTHONHOME", "VOICEBOX_CALLER_MARKER"):
    if forbidden in os.environ:
        refuse("caller environment reached the producer: " + forbidden)

argv = sys.argv[1:]
stage = None
build_id = None
for index, item in enumerate(argv):
    if item == "--stage-dir" and index + 1 < len(argv):
        stage = argv[index + 1]
    if item == "--build-id" and index + 1 < len(argv):
        build_id = argv[index + 1]
if stage is None or build_id is None:
    refuse("the collector named no staging directory and build identity")
Path(PLAN["record"]).write_text(
    json.dumps({"argv": argv, "stage": stage, "build_id": build_id}, sort_keys=True)
    + "\\n",
    encoding="utf-8",
)

mode = PLAN["mode"]
if mode == "producer_failure":
    refuse("the sidecar build failed")

if mode == "legacy_producer_output":
    # Exactly the geometry this producer had before the correction: outputs
    # written to a fixed package-relative dist directory, and nothing else.
    legacy = Path.cwd() / "kilix_voicebox_backend" / "dist"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "voicebox-server").write_bytes(PLAN["binary"].encode("ascii"))
    (legacy / "voicebox-server.provenance.json").write_text("{}\\n", encoding="utf-8")
    raise SystemExit(0)

stage_path = Path(stage)
if stage_path.exists():
    refuse("the collector reused an existing staging directory")
dist = stage_path / "dist"
dist.mkdir(parents=True)
name = "voicebox-server.exe" if mode == "windows_output" else "voicebox-server"
binary = dist / name
payload = PLAN["binary"].encode("ascii")
if mode == "symlinked_output":
    binary.symlink_to(PLAN["symlink_target"])
else:
    binary.write_bytes(payload)
provenance = dist / "voicebox-server.provenance.json"
provenance.write_text(PLAN["provenance"], encoding="utf-8")

if mode == "missing_handoff":
    raise SystemExit(0)

fields = [
    ("schema", PLAN.get("schema", SCHEMA)),
    ("build_id", PLAN.get("build_id", build_id)),
    ("binary", PLAN.get("binary_path", "dist/" + name)),
    ("binary_sha256", PLAN.get("binary_sha256", hashlib.sha256(payload).hexdigest())),
    ("binary_bytes", PLAN.get("binary_bytes", str(len(payload)))),
    ("provenance", PLAN.get("provenance_path", "dist/voicebox-server.provenance.json")),
    (
        "provenance_sha256",
        PLAN.get(
            "provenance_sha256",
            hashlib.sha256(provenance.read_bytes()).hexdigest(),
        ),
    ),
]
if PLAN.get("extra_field"):
    fields.append(tuple(PLAN["extra_field"]))
(stage_path / "sidecar-outputs").write_text(
    "".join(key + "=" + value + "\\n" for key, value in fields), encoding="ascii"
)

if mode == "mutated_output":
    binary.write_bytes(PLAN["mutated"].encode("ascii"))
raise SystemExit(0)
'''


class CollectorHarness:
    """A fixture repository holding the real build script and fake build tools."""

    def __init__(self, temporary: Path) -> None:
        self.temporary = temporary
        self.repository = temporary / "repository"
        self.tools = temporary / "tools"
        self.poison = temporary / "python-path"
        (self.repository / "scripts").mkdir(parents=True)
        self.tools.mkdir()
        self.poison.mkdir()
        (self.poison / "sitecustomize.py").write_text(
            "import os\nos._exit(86)\n", encoding="utf-8"
        )
        self.script = self.repository / "scripts" / "build-server.sh"
        self.script.write_bytes(BUILD_SCRIPT.read_bytes())
        self.script.chmod(0o755)
        rustc = self.tools / "rustc"
        rustc.write_text(RUSTC, encoding="utf-8")
        rustc.chmod(0o755)
        self.producer = self.tools / "uv"
        self.producer.write_text(PRODUCER, encoding="utf-8")
        self.producer.chmod(0o755)
        self.record = temporary / "producer-invocation.json"
        self.binaries = self.repository / "tauri" / "src-tauri" / "binaries"

    def plan(self, mode: str, **overrides: object) -> None:
        payload = {
            "mode": mode,
            "record": str(self.record),
            "binary": "fresh-sidecar",
            "provenance": '{"schema":"kilix.voicebox.sidecar-provenance/candidate-v1"}\n',
        }
        payload.update(overrides)
        (self.tools / "producer-plan.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def run(self) -> subprocess.CompletedProcess[bytes]:
        environment = os.environ.copy()
        environment["PATH"] = f"{self.tools}{os.pathsep}{environment['PATH']}"
        # Caller state that must not survive the script's environment filter.
        environment["PYTHONPATH"] = str(self.poison)
        environment["PYTHONHOME"] = str(self.temporary / "invalid-python-home")
        environment["VOICEBOX_CALLER_MARKER"] = "1"
        return subprocess.run(
            (str(self.script),),
            cwd=self.repository,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )

    @property
    def collected_binary(self) -> Path:
        return self.binaries / f"voicebox-server-{PLATFORM}"

    @property
    def collected_provenance(self) -> Path:
        return self.binaries / f"voicebox-server-{PLATFORM}.provenance.json"

    def staging_residue(self) -> list[Path]:
        root = self.repository / "kilix_voicebox_backend" / "build"
        if not root.is_dir():
            return []
        return sorted(root.iterdir())


class SidecarCollectorTests(unittest.TestCase):
    """The real build script against a controlled producer."""

    def harness(self) -> CollectorHarness:
        temporary = tempfile.TemporaryDirectory(
            prefix="voicebox-sidecar-output-", dir=os.environ.get("TMPDIR")
        )
        self.addCleanup(temporary.cleanup)
        return CollectorHarness(Path(temporary.name))

    def assertNothingCollected(self, harness: CollectorHarness) -> None:
        self.assertFalse(harness.collected_binary.exists())
        self.assertFalse(harness.collected_provenance.exists())

    def test_collects_exactly_the_outputs_this_build_produced(self) -> None:
        harness = self.harness()
        harness.plan("success")
        result = harness.run()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(harness.collected_binary.read_bytes(), b"fresh-sidecar")
        self.assertTrue(os.access(harness.collected_binary, os.X_OK))
        self.assertEqual(
            harness.collected_provenance.read_text(encoding="utf-8"),
            '{"schema":"kilix.voicebox.sidecar-provenance/candidate-v1"}\n',
        )
        # The producer was handed a staging directory it owns and a build
        # identity, both inside the repository the script resolved for itself.
        invocation = json.loads(harness.record.read_text(encoding="utf-8"))
        stage = Path(invocation["stage"])
        self.assertEqual(
            stage.parent,
            harness.repository / "kilix_voicebox_backend" / "build",
        )
        self.assertEqual(stage.name, f"stage-{invocation['build_id']}")
        self.assertRegex(invocation["build_id"], r"\A[0-9a-f]{32}\Z")
        self.assertIn("kilix_voicebox_backend/build_binary.py", invocation["argv"])
        # The staging directory belongs to that invocation alone and does not
        # survive it, so no later build can select these bytes.
        self.assertEqual(harness.staging_residue(), [])

    def test_two_builds_never_share_a_staging_identity(self) -> None:
        harness = self.harness()
        harness.plan("success")
        first = harness.run()
        self.assertEqual(first.returncode, 0, first.stderr.decode())
        earlier = json.loads(harness.record.read_text(encoding="utf-8"))
        harness.plan("success", binary="second-sidecar")
        second = harness.run()
        self.assertEqual(second.returncode, 0, second.stderr.decode())
        later = json.loads(harness.record.read_text(encoding="utf-8"))
        self.assertNotEqual(earlier["build_id"], later["build_id"])
        self.assertNotEqual(earlier["stage"], later["stage"])
        self.assertEqual(harness.collected_binary.read_bytes(), b"second-sidecar")

    def test_old_producer_output_path_is_no_longer_collected(self) -> None:
        # The exact G1 mismatch: the producer writes where it used to write and
        # the collector, which used to read a third path, now refuses instead of
        # guessing. Nothing reaches the Tauri binaries directory.
        harness = self.harness()
        harness.plan("legacy_producer_output")
        result = harness.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"no sidecar handoff", result.stderr)
        self.assertNothingCollected(harness)
        self.assertTrue(
            (
                harness.repository
                / "kilix_voicebox_backend"
                / "dist"
                / "voicebox-server"
            ).is_file()
        )

    def test_stale_output_left_by_an_earlier_layout_is_never_collected(self) -> None:
        harness = self.harness()
        for relative in ("backend/dist", "kilix_voicebox_backend/dist"):
            stale = harness.repository / relative
            stale.mkdir(parents=True)
            (stale / "voicebox-server").write_bytes(b"stale-sidecar")
            (stale / "voicebox-server.provenance.json").write_text(
                '{"stale":true}\n', encoding="utf-8"
            )
        harness.plan("success")
        result = harness.run()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(harness.collected_binary.read_bytes(), b"fresh-sidecar")
        self.assertNotIn(b"stale", harness.collected_provenance.read_bytes())
        for relative in ("backend/dist", "kilix_voicebox_backend/dist"):
            self.assertEqual(
                (harness.repository / relative / "voicebox-server").read_bytes(),
                b"stale-sidecar",
            )

    def test_failed_build_with_stale_output_present_collects_nothing(self) -> None:
        harness = self.harness()
        stale = harness.repository / "backend" / "dist"
        stale.mkdir(parents=True)
        (stale / "voicebox-server").write_bytes(b"stale-sidecar")
        (stale / "voicebox-server.provenance.json").write_text("{}\n", encoding="utf-8")
        harness.plan("producer_failure")
        result = harness.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertNothingCollected(harness)
        self.assertEqual(harness.staging_residue(), [])

    def test_an_earlier_artifact_is_replaced_only_by_a_successful_build(self) -> None:
        harness = self.harness()
        harness.binaries.mkdir(parents=True)
        harness.collected_binary.write_bytes(b"previous-sidecar")
        harness.collected_provenance.write_text('{"previous":true}\n', encoding="utf-8")
        harness.plan("producer_failure")
        failed = harness.run()
        self.assertNotEqual(failed.returncode, 0)
        # A failed build publishes nothing; the previously collected artifact is
        # left exactly as it was and is not re-signed as this build's output.
        self.assertEqual(harness.collected_binary.read_bytes(), b"previous-sidecar")
        harness.plan("success")
        succeeded = harness.run()
        self.assertEqual(succeeded.returncode, 0, succeeded.stderr.decode())
        self.assertEqual(harness.collected_binary.read_bytes(), b"fresh-sidecar")
        self.assertEqual(
            harness.collected_provenance.read_text(encoding="utf-8"),
            '{"schema":"kilix.voicebox.sidecar-provenance/candidate-v1"}\n',
        )

    def test_producer_failure_is_refused(self) -> None:
        harness = self.harness()
        harness.plan("producer_failure")
        result = harness.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"the sidecar build failed", result.stderr)
        self.assertNothingCollected(harness)

    def test_missing_handoff_is_refused(self) -> None:
        harness = self.harness()
        harness.plan("missing_handoff")
        result = harness.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"no sidecar handoff", result.stderr)
        self.assertNothingCollected(harness)

    def test_handoff_from_another_build_is_refused(self) -> None:
        harness = self.harness()
        harness.plan("success", build_id="0123456789abcdef0123456789abcdef")
        result = harness.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"not produced by this build", result.stderr)
        self.assertNothingCollected(harness)

    def test_unsupported_handoff_schema_is_refused(self) -> None:
        harness = self.harness()
        harness.plan("success", schema="kilix.voicebox.sidecar-output/other")
        result = harness.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"unsupported sidecar handoff schema", result.stderr)
        self.assertNothingCollected(harness)

    def test_unrecognized_handoff_field_is_refused(self) -> None:
        harness = self.harness()
        harness.plan("success", extra_field=["binary_alternate", "dist/other"])
        result = harness.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"unrecognized sidecar handoff field", result.stderr)
        self.assertNothingCollected(harness)

    def test_handoff_naming_a_file_outside_the_staging_output_is_refused(self) -> None:
        harness = self.harness()
        harness.plan("success", binary_path="../../../../etc/hostname")
        result = harness.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"unexpected binary", result.stderr)
        self.assertNothingCollected(harness)

    def test_handoff_naming_an_unexpected_provenance_file_is_refused(self) -> None:
        harness = self.harness()
        harness.plan("success", provenance_path="dist/other.json")
        result = harness.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"unexpected provenance file", result.stderr)
        self.assertNothingCollected(harness)

    def test_malformed_output_identity_is_refused(self) -> None:
        harness = self.harness()
        harness.plan("success", binary_sha256="not-a-digest")
        result = harness.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"malformed output identity", result.stderr)
        self.assertNothingCollected(harness)

    def test_output_mutated_after_the_handoff_was_written_is_refused(self) -> None:
        harness = self.harness()
        harness.plan("mutated_output", mutated="tampered-sidecar")
        result = harness.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"does not match the identity this build recorded", result.stderr)
        self.assertNothingCollected(harness)

    def test_symlinked_output_is_refused_even_when_its_content_matches(self) -> None:
        harness = self.harness()
        target = harness.temporary / "outside-the-build"
        target.write_bytes(b"fresh-sidecar")
        harness.plan("symlinked_output", symlink_target=str(target))
        result = harness.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"is not a regular file", result.stderr)
        self.assertNothingCollected(harness)

    def test_symlinked_destination_is_refused(self) -> None:
        harness = self.harness()
        outside = harness.temporary / "outside-destination"
        outside.write_bytes(b"untouched")
        harness.binaries.mkdir(parents=True)
        harness.collected_binary.symlink_to(outside)
        harness.plan("success")
        result = harness.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"destination is a symbolic link", result.stderr)
        self.assertEqual(outside.read_bytes(), b"untouched")

    def test_windows_output_is_collected_under_its_own_name(self) -> None:
        harness = self.harness()
        harness.plan("windows_output")
        result = harness.run()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(
            (harness.binaries / f"voicebox-server-{PLATFORM}.exe").read_bytes(),
            b"fresh-sidecar",
        )
        self.assertFalse(harness.collected_binary.exists())
        self.assertTrue(harness.collected_provenance.is_file())


class StubbedPyInstaller:
    """A PyInstaller entry point that records its arguments and writes outputs."""

    def __init__(self, behaviour: str = "success", payload: bytes = b"stub-sidecar"):
        self.behaviour = behaviour
        self.payload = payload
        self.arguments: list[str] | None = None
        self.__main__ = self

    def run(self, arguments: list[str]) -> None:
        self.arguments = list(arguments)
        if self.behaviour == "failure":
            raise SystemExit("PyInstaller refused the build")
        distpath = Path(arguments[arguments.index("--distpath") + 1])
        distpath.mkdir(parents=True, exist_ok=True)
        name = "voicebox-server.exe" if self.behaviour == "windows" else "voicebox-server"
        if self.behaviour == "symlink":
            (distpath / name).symlink_to(distpath.parent / "elsewhere")
            return
        (distpath / name).write_bytes(self.payload)

    def value(self, key: str) -> str:
        assert self.arguments is not None
        return self.arguments[self.arguments.index(key) + 1]


class SidecarProducerTests(unittest.TestCase):
    """The real producer against a stubbed PyInstaller entry point."""

    def stage(self) -> Path:
        temporary = tempfile.TemporaryDirectory(
            prefix="voicebox-sidecar-producer-", dir=os.environ.get("TMPDIR")
        )
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name) / "stage"

    def install(self, stub: StubbedPyInstaller) -> None:
        from .producer_fixture import ControlledProducer
        fixture = ControlledProducer(stub)
        fixture.__enter__()
        self.addCleanup(fixture.__exit__, None, None, None)

    def read_handoff(self, handoff: Path) -> dict[str, str]:
        fields: dict[str, str] = {}
        for line in handoff.read_text(encoding="ascii").splitlines():
            key, _, value = line.partition("=")
            fields[key] = value
        return fields

    def test_producer_writes_this_build_into_the_directory_it_was_given(self) -> None:
        stub = StubbedPyInstaller()
        self.install(stub)
        stage = self.stage()
        handoff = build_binary.build_server(stage, "a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4")
        self.assertEqual(handoff, stage / "sidecar-outputs")
        # The producer's own arguments are observed, not assumed: every path it
        # hands PyInstaller is inside the staging directory it was given.
        self.assertEqual(stub.value("--distpath"), str(stage / "dist"))
        self.assertEqual(stub.value("--workpath"), str(stage / "work"))
        self.assertEqual(stub.value("--specpath"), str(stage / "work"))
        self.assertEqual(stub.value("--name"), "voicebox-server")
        fields = self.read_handoff(handoff)
        self.assertEqual(fields["schema"], HANDOFF_SCHEMA)
        self.assertEqual(fields["build_id"], "a1b2c3d4a1b2c3d4a1b2c3d4a1b2c3d4")
        self.assertEqual(fields["binary"], "dist/voicebox-server")
        self.assertEqual(
            fields["binary_sha256"], hashlib.sha256(b"stub-sidecar").hexdigest()
        )
        self.assertEqual(fields["binary_bytes"], str(len(b"stub-sidecar")))
        self.assertEqual(fields["provenance"], "dist/voicebox-server.provenance.json")
        provenance = stage / "dist" / "voicebox-server.provenance.json"
        self.assertEqual(
            fields["provenance_sha256"],
            hashlib.sha256(provenance.read_bytes()).hexdigest(),
        )
        manifest = json.loads(provenance.read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["schema"], "kilix.voicebox.sidecar-provenance/candidate-v3"
        )
        self.assertEqual(manifest["sidecar_sha256"], fields["binary_sha256"])
        self.assertIs(manifest["contains_model_runtime"], False)

    def test_producer_refuses_a_staging_directory_that_already_exists(self) -> None:
        stub = StubbedPyInstaller()
        self.install(stub)
        stage = self.stage()
        stage.mkdir(parents=True)
        with self.assertRaises(FileExistsError):
            build_binary.build_server(stage, "b" * 32)
        self.assertIsNone(stub.arguments)

    def test_producer_writes_no_handoff_when_the_build_fails(self) -> None:
        stub = StubbedPyInstaller(behaviour="failure")
        self.install(stub)
        stage = self.stage()
        with self.assertRaises(SystemExit):
            build_binary.build_server(stage, "c" * 32)
        self.assertFalse((stage / "sidecar-outputs").exists())

    def test_producer_refuses_an_output_that_is_not_a_regular_file(self) -> None:
        stub = StubbedPyInstaller(behaviour="symlink")
        self.install(stub)
        stage = self.stage()
        with self.assertRaises(SystemExit) as caught:
            build_binary.build_server(stage, "d" * 32)
        self.assertIn("not a regular file", str(caught.exception))
        self.assertFalse((stage / "sidecar-outputs").exists())

    def test_producer_refuses_a_malformed_build_identity(self) -> None:
        stub = StubbedPyInstaller()
        self.install(stub)
        stage = self.stage()
        with self.assertRaises(SystemExit) as caught:
            build_binary.build_server(stage, "NOT-HEX")
        self.assertIn("build id", str(caught.exception))
        self.assertFalse(stage.exists())

    def test_producer_names_a_windows_output_in_its_handoff(self) -> None:
        stub = StubbedPyInstaller(behaviour="windows")
        self.install(stub)
        stage = self.stage()
        handoff = build_binary.build_server(stage, "e" * 32)
        self.assertEqual(
            self.read_handoff(handoff)["binary"], "dist/voicebox-server.exe"
        )

    def test_linux_producer_refuses_windows_only_output_without_handoff(self) -> None:
        stub = StubbedPyInstaller(behaviour="windows")
        self.install(stub)
        stage = self.stage()
        with self.assertRaisesRegex(build_binary.BuildError, "not a regular file"):
            build_binary.build_server(stage, "f" * 32)
        self.assertTrue((stage / "dist/voicebox-server.exe").is_file())
        self.assertFalse((stage / "sidecar-outputs").exists())

    def test_producer_generates_its_own_identity_when_none_is_given(self) -> None:
        stub = StubbedPyInstaller()
        self.install(stub)
        stage = self.stage()
        handoff = build_binary.build_server(stage)
        # An unbound build is still fully described, and a collector that passed
        # its own identity refuses it -- it never silently becomes this build's.
        self.assertRegex(self.read_handoff(handoff)["build_id"], r"\A[0-9a-f]{32}\Z")


if __name__ == "__main__":
    unittest.main()

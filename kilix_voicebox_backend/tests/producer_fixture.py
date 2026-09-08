"""Controlled prerequisite/worker substitution for output-contract unit tests.

No selected-source, dependency, compiled archive or actual-build acceptance is
claimed here. The real parent producer, sealed descriptor, argument builder,
output checks and handoff execute; prerequisite identities and worker output
are explicitly synthetic. Real selected-input tests exercise those boundaries
separately without these patches.
"""
from contextlib import ExitStack
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
from unittest.mock import patch

from kilix_voicebox_backend import build_binary as build


class ControlledProducer:
    def __init__(self, stub):
        self.stub = stub
        self.stack = ExitStack()

    def __enter__(self):
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="producer-input-fixture-")))
        self.identity = {"source_commit": "1" * 40, "source_tree": "2" * 40,
                         "uv_lock_sha256": hashlib.sha256(b"fixture lock\n").hexdigest()}
        explicit = {key: self.root / key for key in (
            "voice_source", "transcribe_source", "transcribe_wheel", "wheelhouse", "python_home")}
        original = build.build_server
        self.stack.enter_context(patch.object(build, "build_server", partial(original, **explicit)))
        self.stack.enter_context(patch.object(build, "_source", side_effect=self.source))
        self.stack.enter_context(patch.object(build, "_git", side_effect=self.git))
        self.stack.enter_context(patch.object(build, "_external_inputs"))
        self.stack.enter_context(patch.object(build, "_projection", side_effect=self.projection))
        self.stack.enter_context(patch.object(build, "_dependencies", return_value=([], [])))
        self.stack.enter_context(patch.object(build, "_python", return_value={
            "implementation": "CPython", "version": "3.12.8", "executable": "python/bin/python3.12", "members": []}))
        self.stack.enter_context(patch.object(build, "_host_tools", return_value=[]))
        self.stack.enter_context(patch.object(build, "_run_isolated", side_effect=self.worker))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def git(self, *args):
        if args[0] == "status": return b""
        if args == ("rev-parse", "HEAD"): return self.identity["source_commit"].encode()
        if args == ("rev-parse", "HEAD^{tree}"): return self.identity["source_tree"].encode()
        raise AssertionError(args)

    def source(self, population, watches):
        population.add("source/uv.lock", b"fixture lock\n")
        return self.identity.copy()

    def projection(self, stage, population, *_inputs):
        manifest = json.loads((build.ROOT / ".kilix-import/sidecar-inputs.json").read_bytes())
        files, modules, plain = [], [], []
        for source in manifest["sources"].values():
            for row in source["members"]:
                data = ("controlled payload " + row["output"]).encode()
                digest = hashlib.sha256(data).hexdigest()
                path = stage / "selected-inputs" / row["output"]
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                path.write_bytes(data)
                path.chmod(0o600)
                population.add("projection/" + row["output"], data)
                files.append({"path": row["output"], "bytes": len(data), "sha256": digest,
                              "mode": "0600", "role": row["role"]})
                if row["role"] == "module":
                    name = row["output"].removeprefix("python/").removesuffix(".py").replace("/", ".").removesuffix(".__init__")
                    modules.append({"name": name, "path": "projection/" + row["output"], "sha256": digest})
                else:
                    plain.append({"path": row["output"].removeprefix("python/"), "bytes": len(data), "sha256": digest})
        completion = {"schema": "kilix.voicebox.sidecar-stage/v2", "files": sorted(files, key=lambda r:r["path"])}
        raw = (json.dumps(completion, indent=2, sort_keys=True) + "\n").encode()
        (stage / "selected-inputs/completion.json").write_bytes(raw)
        self.modules = sorted(modules, key=lambda r:r["name"])
        self.plain = sorted(plain, key=lambda r:r["path"])
        return {"completion": completion, "completion_sha256": hashlib.sha256(raw).hexdigest(), "plaintext": self.plain}, self.modules, files

    def worker(self, stage, python, descriptor, digest, watches, deadline):
        try:
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                raw = stream.read()
            assert hashlib.sha256(raw).hexdigest() == digest
            size = struct.unpack("!Q", raw[:8])[0]
            request = json.loads(raw[8:8+size])["request"]
            # The real argument builder names /output; this mock models the
            # launcher's bind of the actual supplied stage at that location.
            arguments = [str(stage) + value.removeprefix("/output") if value.startswith("/output/") else value
                         for value in build._pyinstaller_arguments(request)]
            self.stub.run(arguments)
            for path in (stage / "dist").iterdir():
                if path.is_file() and not path.is_symlink(): path.chmod(0o700)
            inspection = {"selected_modules": [{"name": r["name"], "source_sha256": r["sha256"], "code_sha256": "a" * 64} for r in self.modules],
                          "plaintext": self.plain, "entries": [{"path": "PYZ.pyz", "kind": "z", "bytes": 1, "sha256": "b" * 64}], "options": []}
            (stage / "archive-inspection.json").write_bytes(build._json(inspection))
        finally:
            os.close(descriptor)

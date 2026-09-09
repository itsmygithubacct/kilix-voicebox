"""Pure fixed-binutils closure controls; filesystem and ELF processes are fake."""
from contextlib import ExitStack
import hashlib
import io
from pathlib import PurePosixPath
from types import SimpleNamespace
import unittest
from unittest import mock

from kilix_voicebox_backend import build_binary as build


class MemoryPath:
    def __init__(self, files, modes, name="/snapshot"):
        self.files, self.modes, self.name = files, modes, PurePosixPath(name)

    def __str__(self):
        return str(self.name)

    def __truediv__(self, other):
        return MemoryPath(self.files, self.modes, str(self.name / other))

    @property
    def parent(self):
        return MemoryPath(self.files, self.modes, str(self.name.parent))

    def mkdir(self, **kwargs):
        pass

    def chmod(self, mode):
        self.modes[str(self)] = mode

    def open(self, mode):
        name = str(self)
        if mode == "rb":
            return io.BytesIO(self.files[name])
        if mode != "xb":
            raise AssertionError("unexpected fake file mode")
        if name in self.files:
            raise FileExistsError(name)
        files = self.files

        class Writer(io.BytesIO):
            def close(self):
                if not self.closed:
                    files[name] = self.getvalue()
                super().close()

        return Writer()


class HostFacts:
    tools = (
        "/usr/bin/bwrap", "/usr/bin/readelf", "/usr/bin/objdump", "/usr/bin/objcopy",
        "/usr/bin/ldd", "/usr/bin/bash", "/usr/sbin/ldconfig", "/usr/bin/env", "/usr/bin/readlink",
        "/usr/bin/dirname", "/usr/bin/uname", "/usr/bin/grep", "/usr/bin/sed",
        "/usr/bin/head", "/usr/bin/cut", "/usr/bin/dash", "/etc/ld.so.cache",
    )
    objcopy = "/usr/bin/x86_64-linux-gnu-objcopy"
    loader = "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"

    def __init__(self):
        self.aliases = {"/usr/bin/objcopy": self.objcopy, "/lib64/ld-linux-x86-64.so.2": self.loader}
        self.sources = {name: b"fake-tool:" + name.encode() for name in self.tools}
        del self.sources["/usr/bin/objcopy"]
        self.sources[self.objcopy] = b"\x7fELFfake-objcopy"
        self.sources[self.loader] = b"\x7fELFfake-loader"
        self.files, self.modes = {}, {}
        self.population = build.Population(MemoryPath(self.files, self.modes))
        self.watches = {}
        self.resolved, self.reads, self.commands = [], [], []
        self.libraries, self.needed = {}, {}
        self.read_error = None

    def resolve(self, path, *, strict=False):
        assert strict is True
        requested = str(path)
        self.resolved.append(requested)
        canonical = self.aliases.get(requested, requested)
        if canonical not in self.sources:
            raise FileNotFoundError(canonical)
        return build.Path(canonical)

    def info(self, path):
        assert str(path) in self.sources
        return SimpleNamespace(st_mode=0o100755)

    def read(self, path, watches):
        assert watches is self.watches
        name = str(path)
        self.reads.append(name)
        if name == self.objcopy and self.read_error is not None:
            raise self.read_error
        raw = self.sources[name]
        # Synthetic identity facts; the real Population.add binds these bytes.
        watches[name] = (("fake-identity", name), hashlib.sha256(raw).hexdigest())
        return raw

    def command(self, argv, *, env, timeout):
        assert env == {"PATH": "/usr/bin:/bin", "LC_ALL": "C"} and timeout == 10
        self.commands.append(argv)
        if argv == ("/usr/sbin/ldconfig", "-p"):
            return "".join(f" {name} (libc6,x86-64) => {path}\n"
                           for name, path in self.libraries.items()).encode()
        assert argv[:2] == ("/usr/bin/readelf", "-d")
        name = str(PurePosixPath(argv[2]).relative_to("/snapshot"))
        assert self.files[argv[2]].startswith(b"\x7fELF")
        return b"".join(b" (NEEDED) Shared library: [" + item.encode() + b"]\n"
                        for item in self.needed.get(name, ()))

    def run(self):
        with ExitStack() as scope:
            scope.enter_context(mock.patch.object(build.Path, "resolve", autospec=True, side_effect=self.resolve))
            scope.enter_context(mock.patch.object(build.Path, "stat", autospec=True, side_effect=self.info))
            scope.enter_context(mock.patch.object(build, "_read", side_effect=self.read))
            scope.enter_context(mock.patch.object(build.subprocess, "check_output", side_effect=self.command))
            return build._host_tools(self.population, self.watches)


class HostToolClosureTests(unittest.TestCase):
    def test_objcopy_requested_name_binds_canonical_bytes_and_recursive_libraries(self):
        facts = HostFacts()
        facts.libraries = {"libbfd.so": "/lib/x86_64-linux-gnu/libbfd.so",
                           "libsframe.so": "/lib/x86_64-linux-gnu/libsframe.so"}
        for name, path in facts.libraries.items():
            facts.sources[path] = b"\x7fELFfake-" + name.encode()
        facts.needed = {"host/usr/bin/objcopy": ("libbfd.so",),
                        "host/usr/lib/x86_64-linux-gnu/libbfd.so": ("libsframe.so",),
                        "host/usr/lib/x86_64-linux-gnu/libsframe.so": ("libbfd.so",)}
        tools = facts.run()
        row = facts.population.rows["host/usr/bin/objcopy"]
        self.assertEqual(row, {"path": "host/usr/bin/objcopy", "bytes": len(facts.sources[facts.objcopy]),
                             "sha256": hashlib.sha256(facts.sources[facts.objcopy]).hexdigest(), "mode": "0700"})
        self.assertEqual(facts.files["/snapshot/host/usr/bin/objcopy"], facts.sources[facts.objcopy])
        self.assertEqual(facts.resolved[:len(facts.tools)], list(facts.tools))
        self.assertIn(facts.objcopy, facts.watches)
        self.assertNotIn("/usr/bin/objcopy", facts.watches)
        self.assertEqual(facts.watches[facts.objcopy][1], row["sha256"])
        self.assertIn({"name": "usr/bin/objcopy", "member": "host/usr/bin/objcopy"}, tools)
        self.assertEqual(tools, sorted(tools, key=lambda item: item["name"]))
        for name in facts.libraries:
            self.assertIn("host/usr/lib/x86_64-linux-gnu/" + name, facts.population.rows)
        parsed = [cmd[2] for cmd in facts.commands if cmd[0] == "/usr/bin/readelf"]
        self.assertEqual(len(parsed), 4)
        self.assertEqual(len(parsed), len(set(parsed)))
        self.assertEqual(facts.population.total, sum(len(raw) for raw in facts.files.values()))

    def test_objcopy_missing_recursive_dependency_refuses(self):
        facts = HostFacts()
        facts.needed["host/usr/bin/objcopy"] = ("libmissing.so",)
        with self.assertRaisesRegex(build.BuildError, "missing native dependency: libmissing.so"):
            facts.run()
        self.assertIn("host/usr/bin/objcopy", facts.population.rows)
        self.assertFalse(any("libmissing" in path for path in facts.population.rows))

    def test_objcopy_dependency_ambiguity_refuses_and_identical_bytes_reuse(self):
        for second in (b"different", b"same"):
            with self.subTest(second=second):
                facts = HostFacts()
                facts.population.add("env/first/libduplicate.so", b"same")
                facts.population.add("env/second/libduplicate.so", second)
                facts.needed["host/usr/bin/objcopy"] = ("libduplicate.so",)
                if second != b"same":
                    with self.assertRaisesRegex(build.BuildError, "ambiguous selected native dependency"):
                        facts.run()
                else:
                    facts.run()
                    self.assertFalse(any(path.startswith("host/") and path.endswith("/libduplicate.so")
                                         for path in facts.population.rows))

    def test_objcopy_canonical_read_refusal_propagates_without_tool_launch(self):
        facts = HostFacts()
        failure = build.BuildError("synthetic changed canonical tool")
        facts.read_error = failure
        with self.assertRaises(build.BuildError) as caught:
            facts.run()
        self.assertIs(caught.exception, failure)
        self.assertEqual(facts.reads[-1], facts.objcopy)
        self.assertNotIn("host/usr/bin/objcopy", facts.population.rows)
        self.assertEqual(facts.commands, [])


if __name__ == "__main__":
    unittest.main()

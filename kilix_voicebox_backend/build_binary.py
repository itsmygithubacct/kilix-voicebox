"""Build the product-only Voicebox sidecar without model runtimes.

One invocation owns one staging directory. The directory must not already
exist: the producer creates it, writes this build's outputs into it, and
finally writes a handoff file naming and hashing exactly those outputs. A
collector reads the handoff and nothing else, so no earlier build's binary or
provenance can be selected, and a producer that fails before the final write
leaves no handoff for a collector to act on.
"""

from __future__ import annotations

import argparse
import ast
import base64
import csv
import ctypes
import email.parser
import errno
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import stat
import struct
import subprocess
import sys
import time
import tomllib
import types
import zipfile


ROOT = Path(__file__).resolve().parent.parent

# The handoff is deliberately not JSON: its only consumer is a shell collector
# that must parse it without a JSON reader, and every value below is a fixed
# literal, a hexadecimal digest or a decimal size.
HANDOFF_SCHEMA = "kilix.voicebox.sidecar-output/candidate-v1"
HANDOFF_NAME = "sidecar-outputs"
DIST_NAME = "dist"
WORK_NAME = "work"
BINARY_STEM = "voicebox-server"
PROVENANCE_NAME = f"{BINARY_STEM}.provenance.json"
BUILD_ID_PATTERN = re.compile(r"\A[0-9a-f]{8,64}\Z")
PROVENANCE_SCHEMA = "kilix.voicebox.sidecar-provenance/candidate-v3"
FILE_LIMIT = 128 * 1024 * 1024
BUNDLE_LIMIT = 512 * 1024 * 1024
STAGE_LIMIT = 1024 * 1024 * 1024
BUILD_SECONDS = 240
# Linux UAPI values, also usable with the selected standalone Python whose
# older build headers omit the newer fcntl names.
F_ADD_SEALS = 1033
F_GET_SEALS = 1034
REQUIRED_SEALS = 1 | 2 | 4 | 8
PYTHON_BINARY_SHA256 = "0dc3a692fa85fcdb7f1a5877d2adf179809ac417a07ffde2373c832863800a15"


class BuildError(SystemExit):
    """A refused build never publishes a handoff."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BuildError("Error: " + message)


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _strict_json(data: bytes) -> object:
    _require(len(data) <= 16 * 1024 * 1024, "metadata exceeds bound")
    def pairs(rows):
        result = {}
        for key, value in rows:
            _require(key not in result, "duplicate metadata key")
            result[key] = value
        return result
    def number(value):
        raise BuildError("Error: non-integer metadata number")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_float=number, parse_constant=number)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BuildError("Error: invalid bounded UTF-8 metadata") from exc
    def depth(item, level=0):
        _require(level <= 32, "metadata nesting exceeds bound")
        if isinstance(item, dict):
            for child in item.values():
                depth(child, level + 1)
        elif isinstance(item, list):
            for child in item:
                depth(child, level + 1)
        elif type(item) is int:
            _require(0 <= item <= 2**53 - 1, "metadata integer exceeds bound")
    depth(value)
    return value


def _parts(name: str) -> list[str]:
    parts = name.split("/")
    _require(bool(name) and len(name.encode()) <= 1024 and "\\" not in name and "\0" not in name
             and all(part not in ("", ".", "..") for part in parts), "unsafe logical path")
    return parts


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _read(path: Path, watches: dict | None = None) -> bytes:
    _require(path.is_absolute(), "absolute local input required")
    # Refuse symbolic ancestors as well as a symbolic leaf.
    for parent in reversed(path.parents):
        _require(not parent.is_symlink(), "symbolic input ancestor")
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode) and before.st_size <= FILE_LIMIT,
                 "input is not a bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(FILE_LIMIT + 1)
        _require(len(raw) == before.st_size and _stamp(before) == _stamp(os.fstat(fd))
                 and _stamp(before) == _stamp(path.lstat()), "input changed while read")
        if watches is not None:
            old = watches.setdefault(str(path), (_stamp(before), hashlib.sha256(raw).hexdigest()))
            _require(old == (_stamp(before), hashlib.sha256(raw).hexdigest()), "input identity changed")
        return raw
    finally:
        os.close(fd)


def _watch(watches: dict, *, hashes: bool = False) -> None:
    for name, (stamp, digest) in watches.items():
        path = Path(name)
        _require(_stamp(path.lstat()) == stamp, "selected input identity changed during build")
        if hashes:
            _require(hashlib.sha256(_read(path)).hexdigest() == digest, "selected input bytes changed")


def _git(*args: str) -> bytes:
    environment = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                   "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
                   "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    return subprocess.check_output(("/usr/bin/git", "--no-pager", "--no-replace-objects",
                                    "-c", "protocol.allow=never", "-c", "core.fsmonitor=false",
                                    "-c", "core.hooksPath=/dev/null", *args),
                                   cwd=ROOT, env=environment, timeout=20)


class Population:
    def __init__(self, root: Path):
        self.root = root
        self.rows: dict[str, dict] = {}
        self.total = 0

    def add(self, name: str, data: bytes, executable: bool = False) -> None:
        _parts(name)
        _require(name not in self.rows and len(data) <= FILE_LIMIT, "duplicate or oversized build input")
        self.total += len(data)
        _require(self.total <= BUNDLE_LIMIT and len(self.rows) < 40000, "build input population exceeds bound")
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open("xb") as out:
            out.write(data)
        target.chmod(0o700 if executable else 0o600)
        self.rows[name] = {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                           "mode": "0700" if executable else "0600"}

    def verify(self) -> None:
        actual = {p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if not p.is_dir()}
        _require(actual == set(self.rows), "build snapshot population changed")
        for name, row in self.rows.items():
            path = self.root / name
            _require(hashlib.sha256(_read(path)).hexdigest() == row["sha256"]
                     and stat.S_IMODE(path.lstat().st_mode) == int(row["mode"], 8), "build snapshot bytes changed")

    def seal(self, request: dict) -> tuple[int, str]:
        self.verify()
        header = _json({"files": sorted(self.rows.values(), key=lambda row: row["path"]), "request": request})
        _require(len(header) <= 16 * 1024 * 1024 and self.total + len(header) + 8 <= BUNDLE_LIMIT,
                 "complete sealed bundle exceeds bound")
        # The selected standalone CPython3.12.8 omits os.memfd_create even
        # though this Linux host exposes the libc function and kernel seals.
        libc = ctypes.CDLL(None, use_errno=True)
        create = libc.memfd_create
        create.argtypes = (ctypes.c_char_p, ctypes.c_uint)
        create.restype = ctypes.c_int
        fd = create(b"voicebox-build-inputs", 1 | 2)  # CLOEXEC | ALLOW_SEALING
        if fd < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        digest = hashlib.sha256()
        try:
            def write(data):
                digest.update(data)
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
            write(struct.pack("!Q", len(header)))
            write(header)
            for row in sorted(self.rows.values(), key=lambda row: row["path"]):
                with (self.root / row["path"]).open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        write(chunk)
            fcntl.fcntl(fd, F_ADD_SEALS, REQUIRED_SEALS)
            _require(fcntl.fcntl(fd, F_GET_SEALS) == REQUIRED_SEALS, "build bundle seal mismatch")
            os.lseek(fd, 0, os.SEEK_SET)
            return fd, digest.hexdigest()
        except BaseException:
            os.close(fd)
            raise


def _source(population: Population, watches: dict) -> dict:
    _require(not _git("status", "--porcelain=v1", "--untracked-files=all"), "clean exact product source required")
    _require(not _git("for-each-ref", "--format=%(refname)", "refs/replace"), "replace refs refused")
    _require(all(row.startswith(b"H ") for row in _git("ls-files", "-v", "-z").split(b"\0") if row),
             "hidden index state refused")
    identity = {"source_commit": _git("rev-parse", "HEAD").decode().strip(),
                "source_tree": _git("rev-parse", "HEAD^{tree}").decode().strip()}
    for raw in _git("ls-tree", "-rlz", "HEAD").split(b"\0"):
        if not raw:
            continue
        metadata, path = raw.split(b"\t", 1)
        mode, kind, blob, size = metadata.split()
        _require(kind == b"blob" and mode in (b"100644", b"100755") and int(size) <= FILE_LIMIT,
                 "unsupported product source leaf")
        name = path.decode()
        _parts(name)
        data = _read(ROOT / name, watches)
        _require(len(data) == int(size) and data == _git("cat-file", "blob", blob.decode()),
                 "product source differs from its claimed commit")
        population.add("source/" + name, data, mode == b"100755")
    identity["uv_lock_sha256"] = hashlib.sha256(_read(ROOT / "uv.lock")).hexdigest()
    return identity


def _stager():
    path = ROOT / "tools/stage_sidecar_inputs.py"
    _require(_read(path) == _git("show", "HEAD:tools/stage_sidecar_inputs.py"), "stager differs from product commit")
    spec = importlib.util.spec_from_file_location("voicebox_sidecar_stager", path)
    _require(spec is not None and spec.loader is not None, "stager unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _projection(stage: Path, population: Population, voice_source: Path,
                transcribe_source: Path, transcribe_wheel: Path) -> tuple[dict, list[dict], list[dict]]:
    module = _stager()
    completion = module.stage(voice_source=voice_source, transcribe_source=transcribe_source,
                              transcribe_wheel=transcribe_wheel, destination=stage / "selected-inputs")
    root = stage / "selected-inputs"
    _require(_strict_json((root / "completion.json").read_bytes()) == completion, "input completion mismatch")
    manifest = _strict_json(_read(ROOT / ".kilix-import/sidecar-inputs.json"))
    expected = sorted([{"path": row["output"], "bytes": row["bytes"], "sha256": row["sha256"],
                        "mode": row["output_mode"], "role": row["role"]}
                       for source in manifest["sources"].values() for row in source["members"]],
                      key=lambda row: row["path"])
    _completion(completion, manifest, expected)
    _require({p.relative_to(root).as_posix() for p in root.rglob("*") if not p.is_dir()}
             == {r["path"] for r in expected} | {"completion.json"}, "input stage has extra or missing members")
    plaintext, modules = [], []
    for row in expected:
        data = _read(root / row["path"])
        _require(len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"]
                 and stat.S_IMODE((root / row["path"]).lstat().st_mode) == 0o600, "staged payload mismatch")
        population.add("projection/" + row["path"], data)
        if row["role"] == "module":
            name = row["path"].removeprefix("python/").removesuffix(".py").replace("/", ".")
            name = name.removesuffix(".__init__")
            modules.append({"name": name, "path": "projection/" + row["path"], "sha256": row["sha256"]})
        else:
            plaintext.append({"path": row["path"].removeprefix("python/"), "bytes": row["bytes"], "sha256": row["sha256"]})
    _require(len(modules) == 16 and len(plaintext) == 4, "unexpected projection denominator")
    inputs = {"completion": completion,
              "completion_sha256": hashlib.sha256(module.json_bytes(completion)).hexdigest(),
              "plaintext": sorted(plaintext, key=lambda row: row["path"])}
    return inputs, sorted(modules, key=lambda row: row["name"]), expected


def _completion(completion: dict, manifest: dict, expected: list[dict]) -> None:
    _require(type(completion) is dict and set(completion) == {
        "schema", "input_manifest_sha256", "sources", "wheel", "observed_source_modes",
        "observed_wheel_mode", "files"}, "unknown or missing completion field")
    _require(completion["schema"] == "kilix.voicebox.sidecar-stage/v2"
             and completion["input_manifest_sha256"] == hashlib.sha256(_read(ROOT / ".kilix-import/sidecar-inputs.json")).hexdigest(),
             "completion authority mismatch")
    _require(completion["sources"] == {name: {key: source[key] for key in ("repository", "commit", "tree")}
                                       for name, source in manifest["sources"].items()}, "completion source mismatch")
    _require(completion["wheel"] == {key: manifest["wheel"][key] for key in ("filename", "bytes", "sha256")},
             "completion wheel mismatch")
    names = {name + "/" + row["path"] for name, source in manifest["sources"].items() for row in source["members"]}
    modes = completion["observed_source_modes"]
    _require(type(modes) is dict and set(modes) == names and all(x in ("0600", "0644") for x in modes.values())
             and completion["observed_wheel_mode"] in ("0600", "0644"), "completion mode population mismatch")
    _require(completion["files"] == expected and len(expected) == 20, "input completion population mismatch")


def _external_inputs(voice: Path, transcribe: Path, wheel: Path, watches: dict) -> None:
    manifest = _strict_json(_read(ROOT / ".kilix-import/sidecar-inputs.json"))
    for name, directory in (("voice", voice), ("transcribe", transcribe)):
        for row in manifest["sources"][name]["members"]:
            raw = _read(directory / row["path"], watches)
            _require(len(raw) == row["bytes"] and hashlib.sha256(raw).hexdigest() == row["sha256"], "selected external source changed")
    raw = _read(wheel, watches)
    _require(len(raw) == manifest["wheel"]["bytes"] and hashlib.sha256(raw).hexdigest() == manifest["wheel"]["sha256"],
             "selected transcribe wheel changed")


def _marker(expression: str) -> bool:
    environment = {"sys_platform": "linux", "platform_machine": "x86_64", "python_full_version": "3.12.8",
                   "implementation_name": "cpython", "platform_python_implementation": "CPython"}
    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Name):
            _require(node.id in environment, "unsupported lock marker variable")
            return environment[node.id]
        if isinstance(node, ast.Constant) and type(node.value) is str:
            return node.value
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            return (all if isinstance(node.op, ast.And) else any)(visit(x) for x in node.values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = visit(node.left), visit(node.comparators[0])
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
            if isinstance(node.ops[0], ast.Lt) and isinstance(node.left, ast.Name) and node.left.id == "python_full_version":
                return tuple(map(int, left.split("."))) < tuple(map(int, right.split(".")))
        raise BuildError("Error: unsupported lock marker")
    return bool(visit(ast.parse(expression, mode="eval")))


def _locked_packages(lock: dict) -> dict[str, dict]:
    packages = {}
    for package in lock["package"]:
        _require(package["name"] not in packages, "duplicate lock package selection")
        packages[package["name"]] = package
    project = packages["kilix-voicebox-backend"]
    pending = list(project["dependencies"]) + list(project["dev-dependencies"]["build"])
    selected, extras = {}, set()
    while pending:
        item = pending.pop()
        _require(set(item) <= {"name", "marker", "extra"}, "unknown lock dependency field")
        if "marker" in item and not _marker(item["marker"]):
            continue
        name = item["name"]
        package = packages[name]
        if name not in selected:
            selected[name] = package
            pending.extend(package.get("dependencies", []))
        for extra in item.get("extra", []):
            if (name, extra) not in extras:
                extras.add((name, extra))
                pending.extend(package["optional-dependencies"][extra])
    _require(selected["pyinstaller"]["version"] == "6.15.0", "PyInstaller pin mismatch")
    return selected


def _wheel(raw: bytes, name: str, version: str) -> tuple[list[tuple[str, bytes, bool]], str]:
    files, total = {}, 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        _require(len(infos) <= 40000, "wheel population exceeds bound")
        names = set()
        for info in infos:
            path = info.orig_filename.rstrip("/") if info.is_dir() else info.orig_filename
            _parts(path)
            _require(info.filename == info.orig_filename and path not in names, "duplicate wheel path")
            names.add(path)
            mode = info.external_attr >> 16
            if info.is_dir():
                _require(info.file_size == 0 and stat.S_IFMT(mode) in (0, stat.S_IFDIR), "invalid wheel directory")
                continue
            _require(stat.S_IFMT(mode) in (0, stat.S_IFREG) and not info.flag_bits & 1
                     and info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), "unsupported wheel member")
            total += info.file_size
            _require(info.file_size <= FILE_LIMIT and total <= BUNDLE_LIMIT, "wheel expansion exceeds bound")
            with archive.open(info) as source:
                data = source.read(info.file_size + 1)
            _require(len(data) == info.file_size, "wheel size mismatch")
            files[path] = (data, bool(mode & 0o111))
    metadata = [p for p in files if p.endswith(".dist-info/METADATA") and p.count("/") == 1]
    _require(len(metadata) == 1, "wheel distribution population mismatch")
    parsed = email.parser.BytesParser().parsebytes(files[metadata[0]][0])
    _require(parsed.get_all("Name") is not None and len(parsed.get_all("Name")) == 1
             and parsed.get_all("Version") == [version]
             and parsed["Name"].lower().replace("_", "-") == name, "wheel metadata identity mismatch")
    record = metadata[0].removesuffix("METADATA") + "RECORD"
    rows = list(csv.reader(io.StringIO(files[record][0].decode())))
    seen = set()
    for row in rows:
        _require(len(row) == 3 and row[0] in files and row[0] not in seen, "wheel RECORD population mismatch")
        path, encoded, size = row
        seen.add(path)
        if path == record:
            _require((encoded, size) == ("", ""), "wheel RECORD self identity mismatch")
        else:
            data = files[path][0]
            actual = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            _require(encoded == "sha256=" + actual and size == str(len(data)), "wheel RECORD bytes mismatch")
    _require(seen == set(files), "wheel has unrecorded files")
    return [(p, data, executable) for p, (data, executable) in sorted(files.items())], hashlib.sha256(files[record][0]).hexdigest()


def _dependencies(wheelhouse: Path, population: Population, watches: dict) -> tuple[list[dict], list[dict]]:
    selected = _locked_packages(tomllib.loads(_read(ROOT / "uv.lock").decode()))
    _require(wheelhouse.is_absolute() and wheelhouse.is_dir() and not wheelhouse.is_symlink(), "explicit local wheelhouse required")
    choices = {}
    for name, package in selected.items():
        for wheel in package.get("wheels", []):
            filename = wheel["url"].rsplit("/", 1)[-1]
            _require(filename not in choices, "ambiguous lock wheel filename")
            choices[filename] = (name, package["version"], wheel)
    observed, artifacts = {}, []
    for path in sorted(wheelhouse.iterdir()):
        _require(path.name in choices, "unknown wheelhouse member")
        name, version, spec = choices[path.name]
        _require(name not in observed, "multiple wheels selected for one distribution")
        filename = path.name
        compatible = filename.endswith("-py3-none-any.whl") or filename.endswith("-py2.py3-none-any.whl")
        tag = filename.removesuffix(".whl").rsplit("-", 3)
        stable_abi = len(tag) == 4 and tag[2] == "abi3" and re.fullmatch(r"cp3[0-9]+", tag[1]) and int(tag[1][2:]) <= 312
        compatible |= ("x86_64" in filename and "manylinux" in filename
                       and ("-cp312-cp312-" in filename or stable_abi
                            or (tag[2] == "none" and "py3" in tag[1].split("."))))
        _require(compatible, "wheel is outside the selected Linux CPython3.12 target: " + filename)
        raw = _read(path, watches)
        actual = hashlib.sha256(raw).hexdigest()
        _require(len(raw) == spec["size"] and "sha256:" + actual == spec["hash"], "locked whole wheel identity mismatch")
        members, record_sha256 = _wheel(raw, name, version)
        logical = []
        for relative, data, executable in members:
            logical.append("env/" + relative)
            population.add(logical[-1], data, executable)
        observed[name] = {"name": name, "version": version, "record_sha256": record_sha256, "members": logical}
        artifacts.append({"name": name, "filename": filename, "bytes": len(raw), "sha256": actual})
    _require(set(observed) == set(selected), "missing locked software wheels: " + ", ".join(sorted(set(selected) - set(observed))))
    return [observed[name] for name in sorted(observed)], sorted(artifacts, key=lambda row: row["name"])


def _python(home: Path, population: Population, watches: dict) -> dict:
    _require(home.is_absolute() and not home.is_symlink(), "explicit pinned Python home required")
    executable = home / "bin/python3.12"
    _require(hashlib.sha256(_read(executable, watches)).hexdigest() == PYTHON_BINARY_SHA256,
             "pinned Python3.12.8 executable mismatch")
    sources = [("bin/python3.12", executable), ("lib/libpython3.12.so.1.0", home / "lib/libpython3.12.so.1.0")]
    stdlib = home / "lib/python3.12"
    _require(stdlib.is_dir() and not stdlib.is_symlink(), "Python standard library unavailable")
    for path in sorted(stdlib.rglob("*")):
        relative = path.relative_to(home)
        if "site-packages" in relative.parts or "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_dir():
            _require(not path.is_symlink(), "symbolic standard-library directory")
            continue
        sources.append((relative.as_posix(), path))
    members = []
    for relative, source in sources:
        name = "python/" + relative
        population.add(name, _read(source, watches), relative == "bin/python3.12")
        members.append(name)
    return {"implementation": "CPython", "version": "3.12.8", "executable": "python/bin/python3.12",
            "members": sorted(members)}


def _host_tools(population: Population, watches: dict) -> list[dict]:
    # Fixed bootstrap/binutils/shell utilities; no PATH-selected tool execution.
    selected = ["/usr/bin/bwrap", "/usr/bin/readelf", "/usr/bin/objdump", "/usr/bin/ldd",
                "/usr/bin/bash", "/usr/sbin/ldconfig", "/usr/bin/env", "/usr/bin/readlink",
                "/usr/bin/dirname", "/usr/bin/uname", "/usr/bin/grep", "/usr/bin/sed",
                "/usr/bin/head", "/usr/bin/cut", "/usr/bin/dash", "/etc/ld.so.cache"]
    names = {}
    pending = []
    def register(source):
        requested = str(source)
        if requested.startswith("/lib/") or requested.startswith("/lib64/"):
            requested = "/usr" + requested
        source = Path(source).resolve(strict=True)
        logical = "host/" + requested.lstrip("/")
        if logical not in population.rows:
            data = _read(source, watches)
            population.add(logical, data, bool(source.stat().st_mode & 0o111))
            names[logical] = {"name": requested.lstrip("/"), "member": logical}
            if data.startswith(b"\x7fELF"):
                pending.append(logical)
        return logical
    for name in selected:
        register(name)
    library_map = {}
    for line in subprocess.check_output(("/usr/sbin/ldconfig", "-p"), env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, timeout=10).decode().splitlines():
        match = re.match(r"\s*(\S+) \([^)]*x86-64[^)]*\) => (/.+)$", line)
        if match:
            library_map.setdefault(match[1], match[2])
    # The loader named in ELF PT_INTERP is needed even without a DT_NEEDED row.
    register(str(Path("/lib64/ld-linux-x86-64.so.2").resolve(strict=True)))
    for name in list(population.rows):
        if not name.startswith("host/"):
            with (population.root / name).open("rb") as source:
                if source.read(4) == b"\x7fELF":
                    pending.append(name)
    checked = set()
    while pending:
        name = pending.pop()
        if name in checked:
            continue
        checked.add(name)
        raw = subprocess.check_output(("/usr/bin/readelf", "-d", str(population.root / name)),
                                      env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, timeout=10)
        needed = re.findall(rb"\(NEEDED\).*?\[([^\]]+)\]", raw)
        for library in needed:
            basename = library.decode()
            if basename.startswith("$ORIGIN/"):
                target = (population.root / name).parent / basename.removeprefix("$ORIGIN/")
                relative = target.resolve(strict=True).relative_to(population.root.resolve()).as_posix()
                _require(relative in population.rows, "missing selected origin-relative native dependency")
                continue
            _require("/" not in basename, "unsupported path in native dependency")
            existing = [path for path in population.rows if path.rsplit("/", 1)[-1] == basename]
            if existing:
                _require(len({population.rows[path]["sha256"] for path in existing}) == 1,
                         "ambiguous selected native dependency: " + basename)
                continue
            _require(basename in library_map, "missing native dependency: " + basename)
            register(library_map[basename])
    return sorted(names.values(), key=lambda row: row["name"])


# This bootstrap only extracts the sealed, internally generated bundle. The
# second namespace remounts the new private tmpfs read-only before any package
# code executes. No mutable source, cache directory or wheel is on sys.path.
_UNPACK = r'''
import hashlib,json,os,pathlib,struct,sys
bundle=pathlib.Path('/bundle')
digest=hashlib.sha256()
with bundle.open('rb') as stream:
 while chunk:=stream.read(1024*1024):digest.update(chunk)
if digest.hexdigest()!=sys.argv[1]:raise SystemExit('sealed bundle digest mismatch')
with bundle.open('rb') as stream:
 size=struct.unpack('!Q',stream.read(8))[0]
 if size>16*1024*1024:raise SystemExit('bundle header exceeds bound')
 header_bytes=stream.read(size)
 header=json.loads(header_bytes)
 if set(header)!={'files','request'}:raise SystemExit('bundle header keys')
 seen=set()
 for row in header['files']:
  if set(row)!={'path','bytes','sha256','mode'}:raise SystemExit('bundle member keys')
  parts=row['path'].split('/')
  if row['path'] in seen or any(p in ('','.','..') for p in parts) or '\\' in row['path'] or '\0' in row['path']:raise SystemExit('bundle path')
  if parts[0] not in ('source','projection','env','python','host'):raise SystemExit('bundle prefix')
  seen.add(row['path'])
  target=pathlib.Path('/sealed')/row['path']
  target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
  remaining=row['bytes'];digest=hashlib.sha256()
  with target.open('xb') as out:
   while remaining:
    block=stream.read(min(remaining,1024*1024))
    if not block:raise SystemExit('truncated bundle')
    digest.update(block);out.write(block);remaining-=len(block)
  if digest.hexdigest()!=row['sha256']:raise SystemExit('bundle member digest')
  target.chmod(int(row['mode'],8))
 if stream.read(1):raise SystemExit('trailing bundle bytes')
pathlib.Path('/sealed/.manifest.json').write_bytes(header_bytes)
command=['/usr/bin/bwrap','--tmpfs','/','--ro-bind','/sealed','/sealed','--bind','/output','/output',
 '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/usr/bin','--dir','/usr/lib64',
 '--symlink','usr/bin','/bin','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64',
 '--symlink','dash','/usr/bin/sh','--symlink','/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2','/usr/lib64/ld-linux-x86-64.so.2',
 '--cap-drop','ALL','--die-with-parent','--chdir','/output']
for row in header['request']['tools']:
 member=row['member']
 command += ['--ro-bind','/sealed/'+member,'/'+member.removeprefix('host/')]
command += ['/sealed/python/bin/python3.12','-I','-B','-S','-c',
 "import sys;sys.path[:0]=['/sealed/source','/sealed/projection/python','/sealed/env'];from kilix_voicebox_backend.build_binary import _frozen_build;_frozen_build()"]
os.execv(command[0],command)
'''


def _run_isolated(stage: Path, python_home: Path, fd: int, bundle_sha256: str,
                  watches: dict, deadline: float) -> None:
    command = ["/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session",
               "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
               "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
               "--dir", "/etc", "--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache",
               "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
               "--ro-bind", str(python_home), str(python_home), "--tmpfs", "/sealed",
               "--bind", str(stage), "/output", "--ro-bind-data", str(fd), "/bundle",
               "--chdir", "/output", str(python_home / "bin/python3.12"), "-I", "-B", "-S", "-c", _UNPACK, bundle_sha256]
    environment = {"PATH": "/usr/bin:/bin", "HOME": "/output/home", "LC_ALL": "C.UTF-8",
                   "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                   "PYINSTALLER_CONFIG_DIR": "/output/cache", "SOURCE_DATE_EPOCH": "0"}
    proc = None
    try:
        proc = subprocess.Popen(command, pass_fds=(fd,), env=environment, start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    finally:
        # The namespace inherited the only needed copy; no bundle RAM retention
        # in the parent through inference/build execution.
        os.close(fd)
    try:
        with (stage / "build.log").open("xb") as log, selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            total, last_check = 0, 0.0
            while True:
                now = time.monotonic()
                _require(now < deadline, "original build deadline exceeded")
                if now - last_check >= 0.25:
                    _watch(watches)
                    total_size = sum(p.lstat().st_size for p in stage.rglob("*") if not p.is_dir())
                    _require(total_size <= STAGE_LIMIT, "private build staging exceeds 1GiB")
                    last_check = now
                if not selector.select(min(0.05, deadline - now)):
                    continue
                block = os.read(proc.stdout.fileno(), 65536)
                if not block:
                    break
                total += len(block)
                _require(total <= 16 * 1024 * 1024, "build log exceeds bound")
                log.write(block)
            code = proc.wait(timeout=max(0.001, deadline - time.monotonic()))
            _require(code == 0, "isolated PyInstaller build refused; preserve build.log")
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        proc.stdout.close()


def _code_digest(code: types.CodeType) -> str:
    def constant(value):
        if isinstance(value, types.CodeType):
            return binding(value)
        if isinstance(value, (tuple, frozenset)):
            items = [constant(x) for x in value]
            return {type(value).__name__: sorted(items, key=repr) if isinstance(value, frozenset) else items}
        if isinstance(value, bytes):
            return {"bytes": value.hex()}
        return {type(value).__name__: repr(value)}
    def binding(value):
        return {name: constant(getattr(value, name)) for name in (
            "co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals", "co_stacksize",
            "co_flags", "co_code", "co_consts", "co_names", "co_varnames", "co_freevars", "co_cellvars",
            "co_name", "co_qualname", "co_firstlineno", "co_linetable", "co_exceptiontable")}
    return hashlib.sha256(_json(binding(code))).hexdigest()


def _pyinstaller_arguments(request: dict) -> list[str]:
    hidden = ["kilix_voicebox_backend", "kilix_voicebox_backend.main", "kilix_voicebox_backend.config",
              "kilix_voicebox_backend.database", "kilix_voicebox_backend.models", "kilix_voicebox_backend.profiles",
              "kilix_voicebox_backend.history", "kilix_voicebox_backend.tts", "kilix_voicebox_backend.transcribe",
              "kilix_voicebox_backend.kilix_provider", "kilix_voicebox_backend.orchestrator",
              "kilix_voicebox_backend.private_store", "kilix_voicebox_backend.managed_capture",
              "kilix_voicebox_backend.utils.audio", "kilix_voicebox_backend.utils.cache",
              "kilix_voicebox_backend.utils.validation", "fastapi", "uvicorn", "sqlalchemy", "soundfile"]
    hidden += [row["name"] for row in request["modules"]]
    arguments = ["/sealed/source/kilix_voicebox_backend/server.py", "--onefile", "--name", BINARY_STEM,
                 "--paths", "/sealed/source", "--paths", "/sealed/projection/python", "--paths", "/sealed/env",
                 "--distpath", "/output/dist", "--workpath", "/output/work", "--specpath", "/output/work",
                 "--noconfirm", "--clean", "--noupx"]
    for name in hidden:
        arguments.extend(("--hidden-import", name))
    for row in request["plaintext"]:
        source = "/sealed/projection/" + ("python/" if row["path"] in ("VERSION", "voicelib/microphone_supervisor.py") else "") + row["path"]
        target = str(Path(row["path"]).parent)
        arguments.extend(("--add-data", source + ":" + target))
    for name in ("kilix_transcribe.worker", "kilix_transcribe.cli", "kilix_transcribe.content",
                 "kilix_qwen_tts", "kilix_content", "torch", "onnxruntime", "transformers"):
        arguments.extend(("--exclude-module", name))
    return arguments


def _frozen_build() -> None:
    # This function is executed only by the fixed namespace bootstrap, using
    # the immutable copy of this exact source module and the selected Python.
    _require(sys.version_info[:3] == (3, 12, 8), "frozen Python version mismatch")
    header = _strict_json(Path("/sealed/.manifest.json").read_bytes())
    _require(set(header) == {"files", "request"}, "unknown frozen header field")
    request = header["request"]
    _require(set(request) == {"build_id", "modules", "plaintext", "tools"}, "unknown frozen request field")
    for row in header["files"]:
        path = Path("/sealed") / row["path"]
        _require(hashlib.sha256(_read(path)).hexdigest() == row["sha256"], "frozen input identity mismatch")
    try:
        descriptor = os.open("/sealed/source/uv.lock", os.O_WRONLY)
    except OSError as exc:
        _require(exc.errno == errno.EROFS, "unproven immutable build mount")
    else:
        os.close(descriptor)
        raise BuildError("Error: build inputs remain writable")
    os.makedirs("/output/home", mode=0o700, exist_ok=True)
    import PyInstaller
    import PyInstaller.__main__
    _require(PyInstaller.__version__ == "6.15.0" and str(Path(PyInstaller.__file__)).startswith("/sealed/env/"),
             "PyInstaller origin/version mismatch")
    arguments = _pyinstaller_arguments(request)
    PyInstaller.__main__.run(arguments)
    from PyInstaller.archive.readers import CArchiveReader
    binary = Path("/output/dist") / BINARY_STEM
    reader = CArchiveReader(str(binary))
    pyz = reader.open_embedded_archive("PYZ.pyz")
    inspected = []
    for row in request["modules"]:
        actual = pyz.extract(row["name"])
        _require(isinstance(actual, types.CodeType), "selected module absent from frozen archive")
        source = _read(Path("/sealed") / row["path"])
        expected = compile(source, actual.co_filename, "exec", dont_inherit=True, optimize=0)
        _require(_code_digest(actual) == _code_digest(expected), "consumed module differs from selected source")
        inspected.append({"name": row["name"], "source_sha256": hashlib.sha256(source).hexdigest(),
                          "code_sha256": _code_digest(actual)})
    forbidden = ("kilix_transcribe.worker", "kilix_transcribe.cli", "kilix_transcribe.content",
                 "kilix_qwen_tts", "kilix_content", "torch", "onnxruntime", "transformers")
    _require(not any(name == prefix or name.startswith(prefix + ".") for name in pyz.toc for prefix in forbidden),
             "forbidden provider/model module in frozen archive")
    plain = []
    for row in request["plaintext"]:
        raw = reader.extract(row["path"])
        _require(isinstance(raw, bytes) and len(raw) == row["bytes"] and hashlib.sha256(raw).hexdigest() == row["sha256"],
                 "plaintext helper/version/notice differs from selection")
        plain.append(row)
    entries = []
    for name, entry in sorted(reader.toc.items()):
        _parts(name)
        kind = entry[-1]
        if kind == "o":
            continue
        raw = reader.extract(name)
        _require(isinstance(raw, bytes) and len(raw) <= FILE_LIMIT, "invalid frozen archive entry")
        entries.append({"path": name, "kind": kind, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    inspection = {"selected_modules": sorted(inspected, key=lambda row: row["name"]), "plaintext": plain,
                  "entries": entries, "options": sorted(reader.options)}
    Path("/output/archive-inspection.json").write_bytes(_json(inspection))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive(value: object, modules: list[dict], plaintext: list[dict]) -> dict:
    _require(type(value) is dict and set(value) == {"selected_modules", "plaintext", "entries", "options"},
             "unknown or missing archive inspection field")
    selected = value["selected_modules"]
    _require(type(selected) is list and len(selected) == len(modules) == 16, "archive module population mismatch")
    for row, expected in zip(selected, modules):
        _require(type(row) is dict and set(row) == {"name", "source_sha256", "code_sha256"}
                 and row["name"] == expected["name"] and row["source_sha256"] == expected["sha256"]
                 and type(row["code_sha256"]) is str and re.fullmatch("[0-9a-f]{64}", row["code_sha256"]),
                 "archive module identity mismatch")
    _require(value["plaintext"] == plaintext and len(plaintext) == 4, "archive plaintext mismatch")
    entries = value["entries"]
    _require(type(entries) is list and 0 < len(entries) <= 40000, "archive entry population bound")
    last = ""
    for row in entries:
        _require(type(row) is dict and set(row) == {"path", "kind", "bytes", "sha256"}, "archive entry fields")
        _parts(row["path"])
        _require(row["path"] > last and type(row["kind"]) is str and len(row["kind"]) == 1
                 and row["kind"].isascii() and row["kind"] != "o"
                 and type(row["bytes"]) is int and 0 <= row["bytes"] <= FILE_LIMIT
                 and type(row["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", row["sha256"]), "archive entry identity")
        last = row["path"]
    _require("PYZ.pyz" in {row["path"] for row in entries}, "archive PYZ identity missing")
    options = value["options"]
    _require(type(options) is list and len(options) <= 128 and all(type(x) is str and len(x.encode()) <= 1024 for x in options)
             and options == sorted(set(options)), "archive options mismatch")
    return value


def _fresh_stage(stage: Path) -> None:
    _require(stage.is_absolute() and ".." not in stage.parts, "absolute staging path required")
    _require(stage != ROOT and ROOT not in stage.parents, "build stage must be outside product source")
    for ancestor in reversed(stage.parents):
        _require(ancestor.is_dir() and not ancestor.is_symlink(), "staging ancestor is not an existing directory")
    # mkdir itself rejects all existing entries, including dangling symlinks.
    stage.mkdir(mode=0o700)
    _require(stage.lstat().st_uid == os.getuid() and stat.S_IMODE(stage.lstat().st_mode) == 0o700,
             "private owned build directory required")


def _stage_inputs_unchanged(stage: Path, inputs: dict) -> None:
    root = stage / "selected-inputs"
    completion = _read(root / "completion.json")
    _require(_strict_json(completion) == inputs["completion"]
             and hashlib.sha256(completion).hexdigest() == inputs["completion_sha256"], "staged completion changed")
    files = inputs["completion"]["files"]
    _require({p.relative_to(root).as_posix() for p in root.rglob("*") if not p.is_dir()}
             == {row["path"] for row in files} | {"completion.json"}, "staged input population changed")
    for row in files:
        data = _read(root / row["path"])
        _require(len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"]
                 and stat.S_IMODE((root / row["path"]).lstat().st_mode) == 0o600, "staged input changed")


def build_server(stage_dir: Path | str, build_id: str | None = None, *,
                 voice_source: Path | None = None, transcribe_source: Path | None = None,
                 transcribe_wheel: Path | None = None, wheelhouse: Path | None = None,
                 python_home: Path | None = None) -> Path:
    """Build from explicit exact local inputs; publish the handoff only last."""
    started = time.monotonic()
    _require(sys.platform == "linux" and os.uname().machine == "x86_64", "Linux x86_64 build target required")
    identity = build_id if build_id is not None else secrets.token_hex(16)
    _require(type(identity) is str and BUILD_ID_PATTERN.fullmatch(identity), "build id must be 8-64 lowercase hex digits")
    stage = Path(stage_dir)
    # Preserve refusal of any existing entry before evaluating dependencies.
    if os.path.lexists(stage):
        raise FileExistsError(stage)
    required = (voice_source, transcribe_source, transcribe_wheel, wheelhouse, python_home)
    _require(all(isinstance(path, Path) and path.is_absolute() for path in required),
             "explicit absolute Voice source, transcribe source/wheel, software wheelhouse and Python home required")
    _require(not _git("status", "--porcelain=v1", "--untracked-files=all"), "clean exact product source required")
    _fresh_stage(stage)
    watches = {}
    population = Population(stage / "snapshot")
    source = _source(population, watches)
    _external_inputs(voice_source, transcribe_source, transcribe_wheel, watches)
    inputs, modules, _ = _projection(stage, population, voice_source, transcribe_source, transcribe_wheel)
    for row in inputs["completion"]["files"]:
        _read(stage / "selected-inputs" / row["path"], watches)
    _read(stage / "selected-inputs/completion.json", watches)
    distributions, archives = _dependencies(wheelhouse, population, watches)
    python = _python(python_home, population, watches)
    tools = _host_tools(population, watches)
    _watch(watches, hashes=True)
    _stage_inputs_unchanged(stage, inputs)
    deadline = started + BUILD_SECONDS
    _require(time.monotonic() < deadline, "original build preparation deadline exceeded")
    request = {"build_id": identity, "modules": modules, "plaintext": inputs["plaintext"], "tools": tools}
    descriptor, bundle_sha256 = population.seal(request)
    # This private input receipt distinguishes verified original wheel archives
    # from the separately bound installed RECORD/member population in v2.
    try:
        with (stage / "original-wheel-inputs.json").open("xb") as output:
            output.write(_json({"uv_lock_sha256": source["uv_lock_sha256"], "wheels": archives}))
    except BaseException:
        os.close(descriptor)
        raise
    _run_isolated(stage, python_home, descriptor, bundle_sha256, watches, deadline)
    _watch(watches, hashes=True)
    population.verify()
    _stage_inputs_unchanged(stage, inputs)
    _external_inputs(voice_source, transcribe_source, transcribe_wheel, watches)
    _require(not _git("status", "--porcelain=v1", "--untracked-files=all")
             and _git("rev-parse", "HEAD").decode().strip() == source["source_commit"]
             and _git("rev-parse", "HEAD^{tree}").decode().strip() == source["source_tree"],
             "product source identity changed through build")
    inspection = _archive(_strict_json(_read(stage / "archive-inspection.json")), modules, inputs["plaintext"])
    binary = stage / DIST_NAME / BINARY_STEM
    try:
        binary_raw = _read(binary)
    except OSError as exc:
        raise BuildError("Error: produced sidecar is not a regular file") from exc
    _require(bool(binary_raw) and bool(binary.stat().st_mode & 0o111), "produced sidecar is not executable")
    manifest = {
        "schema": PROVENANCE_SCHEMA, "build_id": identity, **source,
        "sidecar_sha256": hashlib.sha256(binary_raw).hexdigest(), "sidecar_bytes": len(binary_raw),
        "contains_model_runtime": False, "provider_endpoints": ["kilix-qwen-tts.sock", "kilix-transcribe.sock"],
        "inputs": inputs,
        "build": {"python": python, "pyinstaller": {"distribution": "pyinstaller", "version": "6.15.0"},
                  "distributions": distributions, "tools": tools,
                  "snapshot": {"sha256": bundle_sha256, "files": sorted(population.rows.values(), key=lambda row: row["path"])},
                  "archive": inspection},
    }
    encoded = _json(manifest)
    _require(_strict_json(encoded) == manifest, "provenance exceeds contract bounds")
    destination = stage / DIST_NAME / PROVENANCE_NAME
    with destination.open("xb") as output:
        output.write(encoded)
    _require(_read(destination) == encoded and _read(binary) == binary_raw, "produced output changed before handoff")
    _require(time.monotonic() < deadline, "original build publication deadline exceeded")
    _require(sum(p.lstat().st_size for p in stage.rglob("*") if not p.is_dir()) <= STAGE_LIMIT,
             "private build staging exceeds 1GiB")
    handoff = stage / HANDOFF_NAME
    with handoff.open("x", encoding="ascii") as output:
        output.write("".join(f"{key}={value}\n" for key, value in (
            ("schema", HANDOFF_SCHEMA), ("build_id", identity),
            ("binary", f"{DIST_NAME}/{binary.name}"), ("binary_sha256", manifest["sidecar_sha256"]),
            ("binary_bytes", str(manifest["sidecar_bytes"])), ("provenance", f"{DIST_NAME}/{PROVENANCE_NAME}"),
            ("provenance_sha256", hashlib.sha256(encoded).hexdigest()),
        )))
    return handoff


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage-dir", required=True, type=Path, help="fresh absolute private build directory")
    parser.add_argument("--build-id", default=None, help="per-invocation 8-64 lowercase hexadecimal identity")
    parser.add_argument("--voice-source", required=True, type=Path)
    parser.add_argument("--transcribe-source", required=True, type=Path)
    parser.add_argument("--transcribe-wheel", required=True, type=Path)
    parser.add_argument("--wheelhouse", required=True, type=Path, help="only exact original locked software wheels")
    parser.add_argument("--python-home", required=True, type=Path, help="exact existing CPython3.12.8 installation")
    options = parser.parse_args(argv)
    build_server(options.stage_dir, options.build_id, voice_source=options.voice_source,
                 transcribe_source=options.transcribe_source, transcribe_wheel=options.transcribe_wheel,
                 wheelhouse=options.wheelhouse, python_home=options.python_home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

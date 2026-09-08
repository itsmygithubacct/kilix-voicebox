#!/usr/bin/env python3
"""Validate and stage the selected offline sidecar inputs; never run them."""

from __future__ import annotations

import argparse
import base64
import contextlib
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import selectors
import stat
import subprocess
import sys
import time
import zipfile
import zlib


MANIFEST_SHA256 = "fa26ea1c27217be6f9eb4509bd84e3c0b8f4c02a95913d5cd2cadd0c352b59d2"
MAX_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 30
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


class StageError(Exception):
    """No successful stage was published."""


def require(ok: bool, message: str) -> None:
    if not ok:
        raise StageError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def relative_parts(name: str) -> list[str]:
    parts = name.split("/")
    require(bool(name) and "\\" not in name and "\0" not in name
            and all(p not in ("", ".", "..") for p in parts), "unsafe member path")
    return parts


def file_stamp(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


class Handles:
    """Hold no-follow directory identities through final publication."""

    def __init__(self, stack: contextlib.ExitStack):
        self.stack = stack
        self.edges: list[tuple[int, str, int]] = []

    def keep(self, fd: int) -> int:
        self.stack.callback(os.close, fd)
        return fd

    def child(self, parent: int, name: str) -> int:
        fd = self.keep(os.open(name, DIRECTORY_FLAGS, dir_fd=parent))
        self.edges.append((parent, name, fd))
        return fd

    def absolute(self, path: str | Path) -> int:
        raw = os.fspath(path)
        require(raw.startswith("/") and raw != "/", "absolute local path required")
        parts = relative_parts(raw[1:])
        fd = self.keep(os.open("/", DIRECTORY_FLAGS))
        for part in parts:
            fd = self.child(fd, part)
        return fd

    def parent(self, root: int, name: str) -> tuple[int, str]:
        parts = relative_parts(name)
        for part in parts[:-1]:
            root = self.child(root, part)
        return root, parts[-1]

    def verify(self) -> None:
        for parent, name, fd in self.edges:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            held = os.fstat(fd)
            require(stat.S_ISDIR(current.st_mode)
                    and (current.st_dev, current.st_ino, current.st_mode, current.st_uid)
                    == (held.st_dev, held.st_ino, held.st_mode, held.st_uid),
                    "directory identity changed")


class Snapshot:
    def __init__(self, handles: Handles, root: int, name: str, maximum: int):
        self.parent, self.name = handles.parent(root, name)
        self.fd = handles.keep(os.open(self.name, FILE_FLAGS, dir_fd=self.parent))
        info = os.fstat(self.fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) in (0o600, 0o644)
                and info.st_nlink == 1, "input must be an owned regular 0600/0644 file")
        require(0 <= info.st_size <= maximum, "input size exceeds bound")
        self.stamp = file_stamp(info)
        self.maximum = maximum
        self.data = os.pread(self.fd, maximum + 1, 0)
        require(len(self.data) == info.st_size, "input size changed")
        self.verify()

    def verify(self) -> None:
        require(file_stamp(os.fstat(self.fd)) == self.stamp
                and file_stamp(os.stat(self.name, dir_fd=self.parent,
                                       follow_symlinks=False)) == self.stamp
                and os.pread(self.fd, self.maximum + 1, 0) == self.data,
                "input identity or bytes changed")


def _git(root: int, deadline: float, *args: str) -> bytes:
    """Fixed read-only plumbing, sanitized environment, bounded output/reaping."""
    remaining = deadline - time.monotonic()
    require(remaining > 0, "staging deadline expired")
    environment = {
        "PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
    }
    command = ["/usr/bin/git", "--no-pager", "--no-replace-objects",
               "-c", "protocol.allow=never", "-c", "core.fsmonitor=false",
               "-c", "core.hooksPath=/dev/null", *args]
    proc = subprocess.Popen(command, cwd=f"/proc/self/fd/{root}", pass_fds=(root,),
                            env=environment, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        assert proc.stdout is not None
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                require(remaining > 0, "staging deadline expired")
                if not selector.select(min(remaining, 0.1)):
                    continue
                block = os.read(proc.stdout.fileno(), 65536)
                if not block:
                    break
                output.extend(block)
                require(len(output) <= MAX_BYTES, "Git output exceeds bound")
        require(proc.wait(timeout=max(0.001, deadline - time.monotonic())) == 0,
                "selected local Git object unavailable")
        return bytes(output)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()


def load_manifest(handles: Handles) -> tuple[dict, Snapshot]:
    # The reviewed tool and adjacent manifest are authority; no caller override.
    root = handles.absolute(Path(__file__).absolute().parent.parent / ".kilix-import")
    snapshot = Snapshot(handles, root, "sidecar-inputs.json", MAX_BYTES)
    require(digest(snapshot.data) == MANIFEST_SHA256, "staging manifest identity mismatch")
    return json.loads(snapshot.data), snapshot


def _source_identity(root: int, spec: dict, deadline: float) -> dict[str, list[str]]:
    require(_git(root, deadline, "rev-parse", "--verify", "HEAD").strip().decode()
            == spec["commit"], "source commit mismatch")
    require(_git(root, deadline, "rev-parse", "HEAD^{tree}").strip().decode()
            == spec["tree"], "source tree mismatch")
    top = _git(root, deadline, "rev-parse", "--show-toplevel").rstrip(b"\n")
    require(os.path.samestat(os.stat(top), os.fstat(root)), "source root mismatch")
    population = {}
    for row in _git(root, deadline, "ls-tree", "-rz", spec["commit"]).split(b"\0"):
        if row:
            metadata, path = row.split(b"\t", 1)
            name = path.decode("utf-8", "strict")
            relative_parts(name)
            require(name not in population, "duplicate source member")
            population[name] = metadata.decode().split()
    notices = sorted(name for name in population
                     if name.rsplit("/", 1)[-1].upper().split(".")[0]
                     in ("LICENSE", "LICENCE", "NOTICE", "COPYING"))
    require(notices == spec["notices"], "source notice population mismatch")
    for member in spec["members"]:
        require(population.get(member["path"])
                == [member["git_mode"], "blob", member["git_blob"]],
                "source member identity mismatch")
    return population


def wheel_members(raw: bytes, spec: dict) -> dict[str, bytes]:
    """Read bounded members into memory, never extract or import the archive."""
    require(len(raw) <= MAX_BYTES, "wheel size exceeds bound")
    data = {}
    metadata = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        require(0 < len(infos) <= 64, "wheel member count exceeds bound")
        total = 0
        for info in infos:
            relative_parts(info.orig_filename)
            require(info.filename == info.orig_filename and info.filename not in data,
                    "duplicate or truncated wheel member")
            mode = info.external_attr >> 16
            require(info.create_system == 3 and stat.S_ISREG(mode)
                    and not info.flag_bits & 1 and info.compress_type == zipfile.ZIP_DEFLATED,
                    "unsupported wheel member type or encoding")
            total += info.file_size
            require(0 <= info.file_size <= MAX_BYTES and total <= MAX_BYTES,
                    "expanded wheel size exceeds bound")
            with archive.open(info) as stream:
                payload = stream.read(info.file_size + 1)
            require(len(payload) == info.file_size, "wheel member size mismatch")
            data[info.filename] = payload
            metadata.append({"path": info.filename, "bytes": len(payload),
                             "sha256": digest(payload), "mode": oct(mode),
                             "compression": info.compress_type})
    require(len(raw) == spec["bytes"] and digest(raw) == spec["sha256"],
            "whole wheel identity mismatch")
    require(metadata == spec["members"], "wheel member population or identity mismatch")
    record_name = "kilix_transcribe-0.1.0.dist-info/RECORD"
    rows = list(csv.reader(io.StringIO(data[record_name].decode("utf-8"))))
    require(len(rows) == len(data), "wheel RECORD population mismatch")
    seen = set()
    for row in rows:
        require(len(row) == 3 and row[0] in data and row[0] not in seen,
                "invalid wheel RECORD member")
        name, encoded, size = row
        seen.add(name)
        if name == record_name:
            require((encoded, size) == ("", ""), "invalid self RECORD")
        else:
            expected = base64.urlsafe_b64encode(hashlib.sha256(data[name]).digest())
            require(encoded == "sha256=" + expected.rstrip(b"=").decode()
                    and size == str(len(data[name])), "wheel RECORD identity mismatch")
    return data


def _write_file(parent: int, name: str, data: bytes) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                 | os.O_CLOEXEC, 0o600, dir_fd=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def _stage(voice_source: str | Path, transcribe_source: str | Path,
           transcribe_wheel: str | Path, destination: str | Path) -> dict:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    with contextlib.ExitStack() as stack:
        handles = Handles(stack)
        manifest, authority = load_manifest(handles)
        # lexically split before Path can normalize away traversal components.
        output_parts = relative_parts(os.fspath(destination)[1:])
        require(os.fspath(destination).startswith("/"), "absolute output path required")
        parent_path = "/" + "/".join(output_parts[:-1])
        parent = handles.absolute(parent_path)
        parent_info = os.fstat(parent)
        require(parent_info.st_uid == os.getuid()
                and stat.S_IMODE(parent_info.st_mode) == 0o700, "output parent must be owned 0700")
        output_name = output_parts[-1]
        try:
            os.stat(output_name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise StageError("destination already exists")

        sources = {}
        snapshots = [authority]
        payloads = {}
        input_modes = {}
        for key, path in (("voice", voice_source), ("transcribe", transcribe_source)):
            root = handles.absolute(path)
            spec = manifest["sources"][key]
            _source_identity(root, spec, deadline)
            sources[key] = root
            for member in spec["members"]:
                snapshot = Snapshot(handles, root, member["path"], member["bytes"])
                require(len(snapshot.data) == member["bytes"]
                        and digest(snapshot.data) == member["sha256"]
                        and snapshot.data == _git(root, deadline, "cat-file", "blob",
                                                  member["git_blob"]), "source bytes mismatch")
                snapshots.append(snapshot)
                payloads[member["output"]] = snapshot.data
                input_modes[key + "/" + member["path"]] = f"{snapshot.stamp[2] & 0o7777:04o}"

        wheel_path = os.fspath(transcribe_wheel)
        require(wheel_path.startswith("/"), "absolute wheel path required")
        parts = relative_parts(wheel_path[1:])
        wheel_parent = handles.absolute("/" + "/".join(parts[:-1]))
        require(parts[-1] == manifest["wheel"]["filename"], "wheel filename mismatch")
        wheel = Snapshot(handles, wheel_parent, parts[-1], MAX_BYTES)
        snapshots.append(wheel)
        members = wheel_members(wheel.data, manifest["wheel"])
        for member in manifest["sources"]["transcribe"]["members"]:
            require(members[member["wheel_path"]] == payloads[member["output"]],
                    "selected source/wheel bytes differ")

        def verify_inputs() -> None:
            require(time.monotonic() < deadline, "staging deadline expired")
            handles.verify()
            require(stat.S_IMODE(os.fstat(parent).st_mode) == 0o700
                    and os.fstat(parent).st_uid == os.getuid(), "output parent changed")
            for key, fd in sources.items():
                _source_identity(fd, manifest["sources"][key], deadline)
            for snapshot in snapshots:
                snapshot.verify()

        verify_inputs()
        os.mkdir(output_name, 0o700, dir_fd=parent)  # exclusive, including dangling links
        output = handles.child(parent, output_name)
        os.fchmod(output, 0o700)
        directories = {"": output}
        rows = []
        for spec in manifest["sources"].values():
            for member in spec["members"]:
                parts = relative_parts(member["output"])
                prefix = ""
                current = output
                for part in parts[:-1]:
                    prefix = prefix + "/" + part if prefix else part
                    if prefix not in directories:
                        os.mkdir(part, 0o700, dir_fd=current)
                        directories[prefix] = handles.child(current, part)
                        os.fchmod(directories[prefix], 0o700)
                    current = directories[prefix]
                _write_file(current, parts[-1], payloads[member["output"]])
                rows.append({"path": member["output"], "bytes": member["bytes"],
                             "sha256": member["sha256"], "mode": member["output_mode"],
                             "role": member["role"]})

        # Verify actual files/population before the final completion marker.
        for row in rows:
            check = Snapshot(handles, output, row["path"], row["bytes"])
            require(digest(check.data) == row["sha256"]
                    and stat.S_IMODE(os.fstat(check.fd).st_mode) == 0o600,
                    "staged bytes or mode mismatch")
            snapshots.append(check)
        def verify_population(pending: bool = False) -> None:
            for prefix, fd in directories.items():
                expected = {path.removeprefix(prefix + "/" if prefix else "").split("/")[0]
                            for path in payloads if not prefix or path.startswith(prefix + "/")}
                if not prefix and pending:
                    expected.add(".completion.pending")
                require(set(os.listdir(fd)) == expected
                        and stat.S_IMODE(os.fstat(fd).st_mode) == 0o700,
                        "staged population or directory mode mismatch")
                os.fsync(fd)

        verify_population()
        verify_inputs()
        completion = {
            "schema": "kilix.voicebox.sidecar-stage/v2",
            "input_manifest_sha256": MANIFEST_SHA256,
            "sources": {key: {k: spec[k] for k in ("repository", "commit", "tree")}
                        for key, spec in manifest["sources"].items()},
            "wheel": {key: manifest["wheel"][key] for key in ("filename", "bytes", "sha256")},
            "observed_source_modes": input_modes,
            "observed_wheel_mode": f"{wheel.stamp[2] & 0o7777:04o}",
            "files": sorted(rows, key=lambda row: row["path"]),
        }
        _write_file(output, ".completion.pending", json_bytes(completion))
        pending = Snapshot(handles, output, ".completion.pending", MAX_BYTES)
        require(pending.data == json_bytes(completion)
                and stat.S_IMODE(os.fstat(pending.fd).st_mode) == 0o600,
                "completion record bytes or mode mismatch")
        snapshots.append(pending)
        verify_inputs()
        verify_population(pending=True)
        os.link(".completion.pending", "completion.json", src_dir_fd=output,
                dst_dir_fd=output, follow_symlinks=False)
        os.unlink(".completion.pending", dir_fd=output)
        os.fsync(output)
        os.fsync(parent)
        return completion


def stage(*, voice_source: str | Path, transcribe_source: str | Path,
          transcribe_wheel: str | Path, destination: str | Path) -> dict:
    """Return the completion record; refuse all stale outputs (no overwrite/retry).

    On a post-allocation failure, retain the private incomplete directory for
    inspection. Callers must require completion.json and verify its file rows.
    This is input staging only, not a built-sidecar or release attestation.
    """
    try:
        return _stage(voice_source, transcribe_source, transcribe_wheel, destination)
    except (OSError, ValueError, zipfile.BadZipFile, zlib.error,
            RuntimeError, subprocess.SubprocessError) as exc:
        raise StageError(f"staging refused ({type(exc).__name__})") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice-source", required=True)
    parser.add_argument("--transcribe-source", required=True)
    parser.add_argument("--transcribe-wheel", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    try:
        completion = stage(**vars(args))
    except StageError as exc:
        print(f"sidecar inputs: refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "staged", "files": len(completion["files"]),
                      "completion_sha256": digest(json_bytes(completion))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Inventory every candidate source input for private or generated payloads."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
IMPORT_PROOF_COMMIT = "f76181896488583bc0d1abb9b37752e075e99cf3"
EXCLUDED_GENERATED_ROOTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
}
AUDIO_SUFFIXES = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm"}
DATABASE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
MODEL_SUFFIXES = {".ckpt", ".ggml", ".gguf", ".onnx", ".pt", ".pth", ".safetensors"}
PRIVATE_SEGMENTS = {"data", "generations", "recordings"}
SECRET_NAMES = {".env", "id_dsa", "id_ed25519", "id_rsa"}


def require(condition: bool, code: str) -> None:
    if not condition:
        raise SystemExit(f"VOICEBOX_IMPORT_INVENTORY_FAIL {code}")


def git(*arguments: str) -> str:
    environment = os.environ.copy()
    unsafe_names = {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_QUARANTINE_PATH",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
    for name in list(environment):
        if (name in unsafe_names or name.startswith("GIT_CONFIG_KEY_")
                or name.startswith("GIT_CONFIG_VALUE_")):
            environment.pop(name)
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    result = subprocess.run(
        ("git", *arguments),
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=environment,
    )
    require(result.returncode == 0, "GIT_QUERY")
    return result.stdout


def source_files() -> list[Path]:
    files: list[Path] = []
    for directory, names, filenames in os.walk(ROOT, followlinks=False):
        names[:] = sorted(name for name in names if name not in EXCLUDED_GENERATED_ROOTS)
        parent = Path(directory)
        for filename in sorted(filenames):
            path = parent / filename
            if path.is_file() and not path.is_symlink():
                files.append(path.relative_to(ROOT))
    return sorted(files)


def main() -> int:
    git("merge-base", "--is-ancestor", IMPORT_PROOF_COMMIT, "HEAD")
    index_entries = [
        record for record in git("ls-files", "-s", "-z").split("\0") if record
    ]
    parsed_entries = [record.partition("\t") for record in index_entries]
    tracked_nonfiles = []
    tracked = []
    for metadata, separator, path in parsed_entries:
        fields = metadata.split()
        if (separator != "\t" or len(fields) != 3
                or fields[0] not in {"100644", "100755"}
                or fields[2] != "0"):
            tracked_nonfiles.append(path or metadata)
            continue
        tracked.append(Path(path))
    untracked = [
        Path(record) for record in git(
            "ls-files", "--others", "--exclude-standard", "-z"
        ).split("\0") if record and (ROOT / record).is_file()
    ]
    candidate = sorted(tracked + untracked)
    scanned = source_files()
    audio = [path for path in scanned if path.suffix.casefold() in AUDIO_SUFFIXES]
    databases = [path for path in scanned if path.suffix.casefold() in DATABASE_SUFFIXES]
    models = [path for path in scanned if path.suffix.casefold() in MODEL_SUFFIXES]
    private = [
        path for path in scanned
        if any(part.casefold() in PRIVATE_SEGMENTS for part in path.parts)
    ]
    secrets = [
        path for path in scanned
        if path.name.casefold() in SECRET_NAMES
        or path.suffix.casefold() in {".key", ".pem"}
    ]
    require(not tracked_nonfiles, "TRACKED_NON_FILE")
    require(not audio, "PRIVATE_AUDIO")
    require(not databases, "PRIVATE_DATABASE")
    require(not models, "MODEL_PAYLOAD")
    require(not private, "PRIVATE_DATA_PATH")
    require(not secrets, "SECRET_MATERIAL")
    require(bool(candidate) and set(candidate) <= set(scanned), "CANDIDATE_POPULATION")
    print(
        "VOICEBOX_IMPORT_INVENTORY_PASS "
        f"(import ancestry 1/1; candidate files {len(candidate)}/{len(candidate)}; "
        f"source files scanned {len(scanned)}/{len(scanned)}; tracked non-files "
        f"0/{len(candidate)}; private audio 0/{len(scanned)}; private databases "
        f"0/{len(scanned)}; model payloads 0/{len(scanned)}; private data paths "
        f"0/{len(scanned)}; secret artifacts 0/{len(scanned)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify the exact curated Voicebox snapshot and its publication boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Any, Iterable


OWNED_PREFIX = ".kilix-import/"
PROVENANCE_REL = ".kilix-import/PROVENANCE.json"
EXPECTED_SCHEMA = "kilix.voicebox.curated-import/v1"
EXPECTED_STATUS = "PREP7_CLEAN_IMPORT_CANDIDATE_NOT_P8"
EXPECTED_IDENTITY = "itsmygithubacct <itsmygithubacct@users.noreply.github.com>"
EXPECTED_IMPORT_COMMIT = "08e763fa8f734fe519b14cf6e9810404a7aa2617"
EXPECTED_IMPORT_TREE = "57f4545ddb5b163b0df8429b0fb12de66d905ebd"
EXPECTED_CONTROL_PATHS = [
    ".kilix-import/PROVENANCE.json",
    ".kilix-import/README.md",
    ".kilix-import/test_verify_import.py",
    ".kilix-import/verify_import.py",
]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RISK_PATTERN = re.compile(
    r"(^|/)(\.venv|venv|env|node_modules|target|dist|build|\.cache)(/|$)"
    r"|(^|/)(data|assets|samples?|tests?/data)/"
    r"(cache|generations?|recordings?|voice[-_ ]?samples?)(/|$)"
    r"|\.(wav|mp3|flac|ogg|m4a|aac|opus|safetensors|pt|pth|onnx|ckpt|sqlite|sqlite3|db)$",
    re.IGNORECASE,
)


class ImportErrorDetail(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ImportErrorDetail(code, message)


def git(root: Path, *args: str, input_bytes: bytes | None = None,
        env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    command = ["git", "-C", str(root), *args]
    return subprocess.run(
        command,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=check,
    )


def repository_root(start: Path | None = None) -> Path:
    base = start or Path(__file__).resolve().parent
    result = git(base, "rev-parse", "--show-toplevel")
    return Path(result.stdout.decode("utf-8").strip())


def load_provenance(root: Path) -> dict[str, Any]:
    with (root / PROVENANCE_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_provenance(provenance: dict[str, Any]) -> None:
    require(provenance.get("schema") == EXPECTED_SCHEMA,
            "PROVENANCE_SCHEMA", "provenance schema identity drifted")
    require(provenance.get("status") == EXPECTED_STATUS,
            "PROVENANCE_STATUS", "import must not claim P8 or acceptance")
    upstream = provenance.get("upstream", {})
    expected_upstream = {
        "url": "https://github.com/jamiepine/voicebox.git",
        "commit": "eb2cd861b19baa16720fd31747071c187c054bc5",
        "tree": "d047f7de60574c99815ddd8179a3b5a079cb2567",
        "archive_sha256": "7ed3d1fdfa0aa56c4d3ac353169b1e7d56664ac9b34277794bf95439c8c33eb4",
        "tracked_files": 406,
        "tracked_blob_bytes": 54600537,
        "licence_path": "LICENSE",
        "licence_sha256": "5d515f5a28ec4b8028c2b7410ad3bb338b9d3a73dd187f9c0fc720e01e387547",
    }
    require(upstream == expected_upstream, "UPSTREAM_IDENTITY",
            "accepted upstream identity or population drifted")
    import_record = provenance.get("import", {})
    require(import_record == {
        "source": "ACCEPTED_CLEAN_GIT_OBJECT_ONLY",
        "method": "GIT_ARCHIVE_EXACT_COMMIT",
        "owned_prefix": OWNED_PREFIX,
        "product_snapshot_commit": EXPECTED_IMPORT_COMMIT,
        "product_snapshot_tree": EXPECTED_IMPORT_TREE,
        "control_paths": EXPECTED_CONTROL_PATHS,
        "upstream_history_imported": False,
        "dirty_prototype_read": False,
        "dirty_prototype_copied": False,
        "remote_population_expected": 0,
        "product_identity": EXPECTED_IDENTITY,
    }, "IMPORT_BOUNDARY", "curated import boundary drifted")
    evidence = provenance.get("accepted_evidence", [])
    require(len(evidence) == 3, "EVIDENCE_POPULATION",
            "accepted evidence population differs from 3/3")
    require(all(re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", "")))
                for row in evidence),
            "EVIDENCE_DIGEST", "accepted evidence digest is invalid")
    risks = provenance.get("expected_source_risks", [])
    require(risks == [{
        "path": "data/cache/.gitkeep",
        "bytes": 27,
        "sha256": "f9a52f9892c0566ac33e11849185f1cc470edb16c4a7427f0aa8fe83e2175e39",
        "disposition": "EMPTY_CACHE_PLACEHOLDER_NOT_GENERATED_MATERIAL",
    }], "RISK_AUTHORITY", "accepted source-risk population differs from 1/1")
    hygiene_delta = provenance.get("hygiene_delta", {})
    require(hygiene_delta == {
        "status": "PREP7_CANDIDATE_NOT_INDEPENDENTLY_ACCEPTED",
        "normalized_pngs": [
            {
                "path": "app/src/assets/voicebox-logo.png",
                "import_sha256": "c688271d63dbcf0e10e7268b9184ca12c2b34ce6b1d9f20b348e2e9f8930bca5",
                "current_sha256": "e0bfce322d08419ed2d5c0f8a46a86ea43f6db5589164b921c42a61aa2b26e54",
                "decoded_idat_sha256": "4629a315a89854acc3b4c00bc70198b719e6c9e90a401e1423b7429fe32b004a",
            },
            {
                "path": "tauri/assets/Voicebox_Microphone.png",
                "import_sha256": "f41af2e7f1ea333c1665e8c0d057d76d0c24c39ebe2a9fe3afcd1b27cbc9e6c4",
                "current_sha256": "5aa3e154c49d129508e7833f44985e6b79c4b820b7404ada19d4067fa5f990d2",
                "decoded_idat_sha256": "4629a315a89854acc3b4c00bc70198b719e6c9e90a401e1423b7429fe32b004a",
            },
            {
                "path": "tauri/assets/voicebox.icon/Assets/Voicebox.png",
                "import_sha256": "c688271d63dbcf0e10e7268b9184ca12c2b34ce6b1d9f20b348e2e9f8930bca5",
                "current_sha256": "e0bfce322d08419ed2d5c0f8a46a86ea43f6db5589164b921c42a61aa2b26e54",
                "decoded_idat_sha256": "4629a315a89854acc3b4c00bc70198b719e6c9e90a401e1423b7429fe32b004a",
            },
            {
                "path": "tauri/assets/voicebox.icon/Assets/Voicebox_Microphone.png",
                "import_sha256": "f41af2e7f1ea333c1665e8c0d057d76d0c24c39ebe2a9fe3afcd1b27cbc9e6c4",
                "current_sha256": "5aa3e154c49d129508e7833f44985e6b79c4b820b7404ada19d4067fa5f990d2",
                "decoded_idat_sha256": "4629a315a89854acc3b4c00bc70198b719e6c9e90a401e1423b7429fe32b004a",
            },
        ],
        "scanner_exception_candidate": {
            "arm": "tree/PII+hosts",
            "path": "tauri/src-tauri/gen/Assets.car",
            "file_sha256": "aa87dbf86a2528b7df6118904bc8cb9af5953f63bb12393b029cccef2193b01c",
            "matched_unit_sha256": "af732763bb717caae650c2e3f5b57992d4a87ff4cc03a61e5a05d54928ac41bd",
            "matched_units": 1,
            "disposition": "INDEPENDENT_REVIEW_REQUIRED_NOT_ALLOWLISTED",
        },
    }, "HYGIENE_DELTA", "hygiene delta or pending exception drifted")


def risk_paths(paths: Iterable[str]) -> list[str]:
    return sorted(path for path in paths if RISK_PATTERN.search(path))


def tracked_paths(root: Path, revision: str = "HEAD") -> list[str]:
    raw = git(root, "ls-tree", "-r", "--name-only", "-z", revision).stdout
    return [part.decode("utf-8", "strict") for part in raw.split(b"\0") if part]


def source_tree(root: Path, owned_paths: list[str], revision: str = "HEAD") -> str:
    scratch_root = os.environ.get("TMPDIR")
    with tempfile.TemporaryDirectory(prefix="voicebox-import-", dir=scratch_root) as temporary:
        index_path = str(Path(temporary) / "index")
        environment = os.environ.copy()
        environment["GIT_INDEX_FILE"] = index_path
        git(root, "read-tree", revision, env=environment)
        for path in owned_paths:
            git(root, "update-index", "--force-remove", "--", path, env=environment)
        return git(root, "write-tree", env=environment).stdout.decode("ascii").strip()


def tree_population(root: Path, tree: str) -> tuple[list[str], int]:
    raw = git(root, "ls-tree", "-r", "-l", "-z", tree).stdout
    paths: list[str] = []
    total = 0
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, path_bytes = record.split(b"\t", 1)
        fields = metadata.split()
        require(len(fields) == 4 and fields[1] == b"blob", "TREE_ENTRY",
                "upstream snapshot contains a non-blob leaf")
        size = int(fields[3])
        paths.append(path_bytes.decode("utf-8", "strict"))
        total += size
    return paths, total


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def git_blob(root: Path, revision: str, path: str) -> bytes:
    return git(root, "show", f"{revision}:{path}").stdout


def png_chunks(content: bytes) -> list[tuple[bytes, bytes]]:
    require(content.startswith(PNG_SIGNATURE), "PNG_SIGNATURE", "hygiene delta is not PNG data")
    chunks: list[tuple[bytes, bytes]] = []
    offset = len(PNG_SIGNATURE)
    while offset < len(content):
        require(offset + 12 <= len(content), "PNG_TRUNCATED", "PNG chunk header is truncated")
        length = struct.unpack(">I", content[offset:offset + 4])[0]
        end = offset + 12 + length
        require(end <= len(content), "PNG_TRUNCATED", "PNG chunk payload is truncated")
        kind = content[offset + 4:offset + 8]
        payload = content[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", content[offset + 8 + length:end])[0]
        require(zlib.crc32(kind + payload) & 0xffffffff == expected_crc,
                "PNG_CRC", "PNG chunk checksum drifted")
        chunks.append((kind, payload))
        offset = end
    require(offset == len(content) and chunks and chunks[-1][0] == b"IEND",
            "PNG_STRUCTURE", "PNG structure is incomplete")
    return chunks


def decoded_idat(chunks: list[tuple[bytes, bytes]]) -> bytes:
    compressed = b"".join(payload for kind, payload in chunks if kind == b"IDAT")
    require(bool(compressed), "PNG_IDAT", "PNG contains no IDAT payload")
    try:
        return zlib.decompress(compressed)
    except zlib.error as error:
        raise ImportErrorDetail("PNG_IDAT", f"PNG IDAT stream is invalid: {error}") from error


def verify_png_normalization(root: Path, import_commit: str,
                             rows: list[dict[str, Any]]) -> int:
    for row in rows:
        path = row["path"]
        imported = git_blob(root, import_commit, path)
        current = (root / path).read_bytes()
        require(sha256_bytes(imported) == row["import_sha256"],
                "PNG_IMPORT_IDENTITY", f"imported PNG identity drifted: {path}")
        require(sha256_bytes(current) == row["current_sha256"],
                "PNG_CURRENT_IDENTITY", f"normalized PNG identity drifted: {path}")
        imported_chunks = png_chunks(imported)
        current_chunks = png_chunks(current)
        imported_non_idat = [(kind, payload) for kind, payload in imported_chunks
                             if kind != b"IDAT"]
        current_non_idat = [(kind, payload) for kind, payload in current_chunks
                            if kind != b"IDAT"]
        require(imported_non_idat == current_non_idat, "PNG_METADATA_DRIFT",
                f"non-IDAT PNG chunks changed: {path}")
        imported_idat = decoded_idat(imported_chunks)
        current_idat = decoded_idat(current_chunks)
        require(imported_idat == current_idat, "PNG_DECODED_DRIFT",
                f"decoded PNG scanlines changed: {path}")
        require(sha256_bytes(current_idat) == row["decoded_idat_sha256"],
                "PNG_DECODED_IDENTITY", f"decoded PNG identity drifted: {path}")
    return len(rows)


def verify_current_repository(root: Path, provenance: dict[str, Any]) -> dict[str, int]:
    status = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    require(status == b"", "WORKTREE_NOT_CLEAN",
            "exact import verification requires a clean tracked and untracked worktree")

    import_commit = provenance["import"]["product_snapshot_commit"]
    root_commits = sorted(line for line in git(
        root, "rev-list", "--max-parents=0", "--all"
    ).stdout.decode("ascii").splitlines() if line)
    require(root_commits == [import_commit], "IMPORT_ROOT",
            "product history does not have the exact 1/1 curated-import root")
    import_tree = git(root, "rev-parse", f"{import_commit}^{{tree}}").stdout.decode("ascii").strip()
    require(import_tree == provenance["import"]["product_snapshot_tree"],
            "IMPORT_PRODUCT_TREE", "curated product snapshot tree drifted")

    import_paths = tracked_paths(root, import_commit)
    import_owned = sorted(path for path in import_paths if path.startswith(OWNED_PREFIX))
    import_source = sorted(path for path in import_paths if not path.startswith(OWNED_PREFIX))
    require(import_owned == provenance["import"]["control_paths"], "IMPORT_CONTROL_PATHS",
            "curated import control population differs from 4/4")
    observed_tree = source_tree(root, import_owned, import_commit)
    expected = provenance["upstream"]
    require(observed_tree == expected["tree"], "SOURCE_TREE_MISMATCH",
            f"source tree {observed_tree} differs from accepted {expected['tree']}")

    tree_paths, blob_bytes = tree_population(root, observed_tree)
    require(tree_paths == import_source, "SOURCE_PATH_MISMATCH",
            "import source path population differs from reconstructed tree")
    require(len(import_source) == expected["tracked_files"], "SOURCE_FILE_COUNT",
            f"import source file population is {len(import_source)}/406")
    require(blob_bytes == expected["tracked_blob_bytes"], "SOURCE_BYTE_COUNT",
            f"import source blob population is {blob_bytes}/54600537 bytes")

    import_risks = risk_paths(import_source)
    expected_risk_paths = [row["path"] for row in provenance["expected_source_risks"]]
    require(import_risks == expected_risk_paths, "SOURCE_RISK_POPULATION",
            f"import source risk population differs from expected 1/1: {import_risks!r}")

    current_paths = tracked_paths(root)
    owned = sorted(path for path in current_paths if path.startswith(OWNED_PREFIX))
    source = sorted(path for path in current_paths if not path.startswith(OWNED_PREFIX))
    require(owned == provenance["import"]["control_paths"], "OWNED_POPULATION",
            "current import control population differs from 4/4")
    require(source == import_source, "CURRENT_SOURCE_PATHS",
            "current source path population differs from the 406/406 imported paths")
    source_delta_raw = git(
        root, "diff", "--name-only", "-z", import_commit, "HEAD", "--"
    ).stdout
    source_delta = sorted(
        part.decode("utf-8", "strict")
        for part in source_delta_raw.split(b"\0")
        if part and not part.decode("utf-8", "strict").startswith(OWNED_PREFIX)
    )
    normalized_rows = provenance["hygiene_delta"]["normalized_pngs"]
    expected_delta = sorted(row["path"] for row in normalized_rows)
    require(source_delta == expected_delta, "CURRENT_SOURCE_DELTA",
            "current source delta differs from the 4/4 reviewed PNG paths")
    normalized_count = verify_png_normalization(root, import_commit, normalized_rows)

    current_risks = risk_paths(source)
    require(current_risks == expected_risk_paths, "CURRENT_RISK_POPULATION",
            f"current source risk population differs from expected 1/1: {current_risks!r}")
    risk = provenance["expected_source_risks"][0]
    risk_path = root / risk["path"]
    require(risk_path.stat().st_size == risk["bytes"]
            and sha256_file(risk_path) == risk["sha256"],
            "SOURCE_RISK_IDENTITY", "accepted empty placeholder identity drifted")

    licence_path = root / expected["licence_path"]
    require(sha256_file(licence_path) == expected["licence_sha256"],
            "LICENCE_IDENTITY", "upstream MIT licence bytes drifted")

    exception = provenance["hygiene_delta"]["scanner_exception_candidate"]
    require(sha256_file(root / exception["path"]) == exception["file_sha256"],
            "SCANNER_EXCEPTION_IDENTITY",
            "pending scanner-exception binary differs from its exact reviewed file digest")

    untracked_raw = git(root, "ls-files", "--others", "--exclude-standard", "-z").stdout
    untracked = [part.decode("utf-8", "strict") for part in untracked_raw.split(b"\0") if part]
    require(not risk_paths(untracked), "UNTRACKED_PRIVATE_ARTIFACT",
            "untracked private/generated artifact path is present")

    remotes = [line for line in git(root, "remote").stdout.decode("utf-8").splitlines() if line]
    require(len(remotes) == provenance["import"]["remote_population_expected"],
            "REMOTE_POPULATION", "publication is not authorized; expected zero remotes")

    upstream_object = git(root, "cat-file", "-e", f"{expected['commit']}^{{commit}}", check=False)
    require(upstream_object.returncode != 0, "UPSTREAM_HISTORY_PRESENT",
            "upstream commit object is present; curated product history must exclude it")

    authors = [line for line in git(root, "log", "--all", "--format=%an <%ae>").stdout.decode().splitlines() if line]
    committers = [line for line in git(root, "log", "--all", "--format=%cn <%ce>").stdout.decode().splitlines() if line]
    require(bool(authors) and len(authors) == len(committers), "COMMIT_POPULATION",
            "product commit identity population is empty or inconsistent")
    require(all(identity == EXPECTED_IDENTITY for identity in authors),
            "AUTHOR_IDENTITY", "one or more product authors use a non-project identity")
    require(all(identity == EXPECTED_IDENTITY for identity in committers),
            "COMMITTER_IDENTITY", "one or more product committers use a non-project identity")

    return {
        "source_files": len(import_source),
        "source_blob_bytes": blob_bytes,
        "source_risks": len(import_risks),
        "current_source_files": len(source),
        "source_delta_paths": len(source_delta),
        "normalized_pngs": normalized_count,
        "scanner_exception_candidates": exception["matched_units"],
        "owned_files": len(owned),
        "product_commits": len(authors),
        "remotes": len(remotes),
    }


def run() -> None:
    root = repository_root()
    provenance = load_provenance(root)
    validate_provenance(provenance)
    counts = verify_current_repository(root, provenance)
    print(
        "VOICEBOX_CURATED_IMPORT: PASS "
        f"({counts['source_files']}/406 source files; "
        f"{counts['source_blob_bytes']}/54600537 source blob bytes; "
        f"{counts['source_risks']}/1 accepted source-risk path; "
        f"{counts['current_source_files']}/406 current source paths; "
        f"{counts['source_delta_paths']}/4 source-delta paths; "
        f"{counts['normalized_pngs']}/4 lossless PNG normalizations; "
        f"{counts['scanner_exception_candidates']}/1 scanner exception pending review; "
        f"{counts['owned_files']}/4 import-control files; "
        f"{counts['product_commits']}/{counts['product_commits']} product commits "
        f"use the project identity; remotes={counts['remotes']} expected=0; "
        "upstream-history commits=0 expected=0)"
    )


if __name__ == "__main__":
    try:
        run()
    except (ImportErrorDetail, OSError, ValueError, json.JSONDecodeError,
            subprocess.CalledProcessError) as error:
        code = error.code if isinstance(error, ImportErrorDetail) else type(error).__name__
        print(f"VOICEBOX_CURATED_IMPORT: FAIL [{code}] {error}", file=sys.stderr)
        raise SystemExit(1)

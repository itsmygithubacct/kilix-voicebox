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
EXPECTED_STATUS = "F104CONT2_IMPORT_HYGIENE_PROOF_CANDIDATE_NOT_P8"
EXPECTED_IDENTITY = "itsmygithubacct <itsmygithubacct@users.noreply.github.com>"
EXPECTED_IMPORT_COMMIT = "08e763fa8f734fe519b14cf6e9810404a7aa2617"
EXPECTED_IMPORT_TREE = "57f4545ddb5b163b0df8429b0fb12de66d905ebd"
EXPECTED_HYGIENE_COMMIT = "f76181896488583bc0d1abb9b37752e075e99cf3"
EXPECTED_HYGIENE_TREE = "6e02708895e7dc1c5ede3331afb847be6d596b1c"
EXPECTED_PUBLIC_REMOTE = "https://github.com/itsmygithubacct/kilix-voicebox.git"
EXPECTED_CONTROL_PATHS = [
    ".kilix-import/PROVENANCE.json",
    ".kilix-import/README.md",
    ".kilix-import/test_verify_import.py",
    ".kilix-import/verify_import.py",
]
EXPECTED_HISTORICAL_MATCH_PROOF = {
    "status": "MEASURED_NOT_ADJUDICATED",
    "independent_adjudications": 0,
    "required_independent_adjudications": 1,
    "scanner_arm": "tree/PII+hosts",
    "scanner_unit_kind": "BINARY_PATH",
    "scanner_unit_sha256": "af732763bb717caae650c2e3f5b57992d4a87ff4cc03a61e5a05d54928ac41bd",
    "pattern_encoding": "HEX",
    "pattern_hex": "703530",
    "match_semantics": "ASCII_CASE_INSENSITIVE_WORD",
    "matches": [
        {
            "offset": 694170,
            "matched_hex": "703530",
            "left_byte_hex": "04",
            "right_byte_hex": "80",
            "context": {
                "before_bytes": 32,
                "after_bytes": 32,
                "start_offset": 694138,
                "end_offset_exclusive": 694205,
                "bytes": 67,
                "distinct_byte_values": 56,
                "sha256": "e2800fa5fe50fceb211b4ffc51593b46e470831d4d7ea5b6c56ff1e36bf594ee",
            },
            "printable_run": {
                "start_offset": 694170,
                "end_offset_exclusive": 694173,
                "bytes": 3,
                "hex": "703530",
                "sha256": "875636a511dab3de413499a61ca1d7ac1fdd97a6e38d8010b03b9552de840830",
            },
        }
    ],
    "generator_inputs": [
        {
            "path": "tauri/assets/voicebox.icon/Assets/Voicebox.png",
            "bytes": 4245540,
            "sha256": "c688271d63dbcf0e10e7268b9184ca12c2b34ce6b1d9f20b348e2e9f8930bca5",
            "matches": 0,
        },
        {
            "path": "tauri/assets/voicebox.icon/Assets/Voicebox_Microphone.png",
            "bytes": 4244260,
            "sha256": "f41af2e7f1ea333c1665e8c0d057d76d0c24c39ebe2a9fe3afcd1b27cbc9e6c4",
            "matches": 0,
        },
        {
            "path": "tauri/assets/voicebox.icon/icon.json",
            "bytes": 556,
            "sha256": "6d8cbed93e877993a3b501d094d16616664a6bdbfdc82b16cfb47abc8dacce01",
            "matches": 0,
        },
    ],
    "generator_input_bytes": 8490356,
}
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
        "remote_population_at_import": 0,
        "product_identity": EXPECTED_IDENTITY,
    }, "IMPORT_BOUNDARY", "curated import boundary drifted")
    require(provenance.get("publication", {}) == {
        "status": "PUBLIC_WORK_REF_AUTHORIZED",
        "hygiene_boundary_commit": EXPECTED_HYGIENE_COMMIT,
        "hygiene_boundary_tree": EXPECTED_HYGIENE_TREE,
        "maximum_configured_remotes": 1,
        "authorized_remote_urls": [EXPECTED_PUBLIC_REMOTE],
    }, "PUBLICATION_BOUNDARY", "curated publication boundary drifted")
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
        "status": "F104CONT2_CANDIDATE_NOT_INDEPENDENTLY_ACCEPTED",
        "changed_source_paths": [
            ".gitignore",
            "app/src/assets/voicebox-logo.png",
            "tauri/assets/Voicebox_Microphone.png",
            "tauri/assets/voicebox.icon/Assets/Voicebox.png",
            "tauri/assets/voicebox.icon/Assets/Voicebox_Microphone.png",
            "tauri/src-tauri/gen/Assets.car",
            "tauri/src-tauri/tauri.conf.json",
            "tauri/src-tauri/tauri.macos.conf.json",
        ],
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
        "generated_binary_boundary": {
            "path": "tauri/src-tauri/gen/Assets.car",
            "import_bytes": 3847048,
            "import_sha256": "aa87dbf86a2528b7df6118904bc8cb9af5953f63bb12393b029cccef2193b01c",
            "previous_matched_unit_sha256": "af732763bb717caae650c2e3f5b57992d4a87ff4cc03a61e5a05d54928ac41bd",
            "previous_matched_units": 1,
            "head_tracked": False,
            "ignored_build_output": True,
            "previous_exception_accepted": False,
            "scanner_exception_required_at_head": False,
            "historical_match_proof": EXPECTED_HISTORICAL_MATCH_PROOF,
            "gitignore": {
                "path": ".gitignore",
                "sha256": "86d03696b92c24545e824e09e7bf29152c510c9a476d048071f040b523689ca0",
                "exact_rule": "tauri/src-tauri/gen/Assets.car",
            },
            "regenerator": {
                "path": "tauri/src-tauri/build.rs",
                "sha256": "f2642864caf1eb3fa50aafecf38ee37e08c3b40c28aa4ff63bbfab56d6f28fba",
                "target_os": "macos",
                "tool": "xcrun actool",
                "build_executed": False,
            },
            "resource_config": {
                "base_path": "tauri/src-tauri/tauri.conf.json",
                "base_sha256": "d6625a6f6fc3bd950cc2eec8df3d78b1955bf036029c6c6b59ed8e4895388386",
                "platform_path": "tauri/src-tauri/tauri.macos.conf.json",
                "platform_sha256": "5bda79a9e350df860da2b8261333816c19772c8c86fd63f23fd7bb49bf6b1d27",
                "platform": "macos",
                "resources": {
                    "gen/Assets.car": "./",
                    "gen/voicebox.icns": "./",
                    "gen/partial.plist": "./",
                },
            },
            "disposition": "REMOVED_FROM_TRACKED_FORK_REGENERATED_ONLY_ON_MACOS",
        },
    }, "HYGIENE_DELTA", "hygiene delta or generated-binary boundary drifted")


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


def verify_png_normalization(root: Path, import_commit: str, boundary_commit: str,
                             rows: list[dict[str, Any]]) -> int:
    for row in rows:
        path = row["path"]
        imported = git_blob(root, import_commit, path)
        current = git_blob(root, boundary_commit, path)
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


def ascii_word_match_offsets(content: bytes, pattern_hex: str) -> list[int]:
    """Return case-insensitive ASCII word matches without storing the token as text."""

    token = bytes.fromhex(pattern_hex)
    require(bool(token) and all(byte < 128 for byte in token),
            "HISTORICAL_PATTERN", "historical scanner pattern is not non-empty ASCII")
    pattern = re.compile(rb"\b" + re.escape(token) + rb"\b", re.IGNORECASE | re.ASCII)
    return [match.start() for match in pattern.finditer(content)]


def verify_historical_match_proof(root: Path, import_commit: str,
                                  path: str, imported: bytes,
                                  proof: dict[str, Any]) -> dict[str, int]:
    """Reproduce the historical match while leaving its adjudication external."""

    require(proof == EXPECTED_HISTORICAL_MATCH_PROOF, "HISTORICAL_PROOF",
            "historical match proof or its non-adjudicated status drifted")
    require(proof["status"] == "MEASURED_NOT_ADJUDICATED"
            and proof["independent_adjudications"] == 0
            and proof["required_independent_adjudications"] == 1,
            "HISTORICAL_ADJUDICATION",
            "historical content must retain the exact 0/1 independent adjudication state")

    scanner_unit = b"BINARY:" + path.encode("utf-8")
    require(sha256_bytes(scanner_unit) == proof["scanner_unit_sha256"],
            "HISTORICAL_SCANNER_UNIT", "historical scanner-unit identity drifted")
    token = bytes.fromhex(proof["pattern_hex"])
    offsets = ascii_word_match_offsets(imported, proof["pattern_hex"])
    expected_offsets = [row["offset"] for row in proof["matches"]]
    require(offsets == expected_offsets, "HISTORICAL_MATCH_POPULATION",
            "historical binary match population or offsets drifted")

    for row in proof["matches"]:
        offset = row["offset"]
        match_end = offset + len(token)
        require(imported[offset:match_end].hex() == row["matched_hex"],
                "HISTORICAL_MATCH_BYTES", "historical matched bytes drifted")
        require(imported[offset - 1:offset].hex() == row["left_byte_hex"]
                and imported[match_end:match_end + 1].hex() == row["right_byte_hex"],
                "HISTORICAL_ADJACENCY", "historical match adjacency drifted")

        context = row["context"]
        context_start = max(0, offset - context["before_bytes"])
        context_end = min(len(imported), match_end + context["after_bytes"])
        context_bytes = imported[context_start:context_end]
        require(context_start == context["start_offset"]
                and context_end == context["end_offset_exclusive"]
                and len(context_bytes) == context["bytes"],
                "HISTORICAL_CONTEXT_BOUNDARY", "historical context boundary drifted")
        require(len(set(context_bytes)) == context["distinct_byte_values"]
                and sha256_bytes(context_bytes) == context["sha256"],
                "HISTORICAL_CONTEXT_IDENTITY", "historical context identity drifted")

        run_start = offset
        while run_start > 0 and 32 <= imported[run_start - 1] <= 126:
            run_start -= 1
        run_end = match_end
        while run_end < len(imported) and 32 <= imported[run_end] <= 126:
            run_end += 1
        run_bytes = imported[run_start:run_end]
        printable_run = row["printable_run"]
        require(run_start == printable_run["start_offset"]
                and run_end == printable_run["end_offset_exclusive"]
                and len(run_bytes) == printable_run["bytes"],
                "HISTORICAL_PRINTABLE_BOUNDARY",
                "historical printable-run boundary drifted")
        require(run_bytes.hex() == printable_run["hex"]
                and sha256_bytes(run_bytes) == printable_run["sha256"],
                "HISTORICAL_PRINTABLE_IDENTITY",
                "historical printable-run identity drifted")

    input_bytes = 0
    matched_inputs = 0
    input_matches = 0
    for row in proof["generator_inputs"]:
        content = git_blob(root, import_commit, row["path"])
        offsets = ascii_word_match_offsets(content, proof["pattern_hex"])
        require(len(content) == row["bytes"]
                and sha256_bytes(content) == row["sha256"],
                "GENERATOR_INPUT_IDENTITY",
                f"historical generator input identity drifted: {row['path']}")
        require(len(offsets) == row["matches"], "GENERATOR_INPUT_MATCHES",
                f"historical generator input match population drifted: {row['path']}")
        input_bytes += len(content)
        input_matches += len(offsets)
        matched_inputs += int(bool(offsets))
    require(input_bytes == proof["generator_input_bytes"],
            "GENERATOR_INPUT_BYTES", "historical generator-input byte population drifted")

    return {
        "historical_binary_units": 1,
        "historical_matches": len(proof["matches"]),
        "generator_inputs": len(proof["generator_inputs"]),
        "generator_input_bytes": input_bytes,
        "matched_generator_inputs": matched_inputs,
        "generator_input_matches": input_matches,
        "historical_adjudications": proof["independent_adjudications"],
    }


def verify_generated_binary_boundary(root: Path, import_commit: str,
                                     boundary_commit: str,
                                     boundary: dict[str, Any]) -> dict[str, int]:
    path = boundary["path"]
    imported = git_blob(root, import_commit, path)
    require(len(imported) == boundary["import_bytes"],
            "GENERATED_IMPORT_SIZE", "imported generated binary size drifted")
    require(sha256_bytes(imported) == boundary["import_sha256"],
            "GENERATED_IMPORT_IDENTITY", "imported generated binary identity drifted")
    historical_counts = verify_historical_match_proof(
        root, import_commit, path, imported, boundary["historical_match_proof"]
    )
    boundary_binary = git(
        root, "cat-file", "-e", f"{boundary_commit}:{path}", check=False
    )
    require(boundary_binary.returncode != 0, "GENERATED_BINARY_PRESENT",
            "generated binary must not be stored in the hygiene-boundary tree")

    ignore = boundary["gitignore"]
    boundary_ignore = git_blob(root, boundary_commit, ignore["path"])
    require(sha256_bytes(boundary_ignore) == ignore["sha256"],
            "GENERATED_IGNORE_IDENTITY", "generated-binary ignore file drifted")
    ignore_lines = boundary_ignore.decode("utf-8").splitlines()
    require(ignore_lines.count(ignore["exact_rule"]) == 1,
            "GENERATED_IGNORE_RULE", "generated binary needs exactly 1/1 narrow ignore rule")

    regenerator = boundary["regenerator"]
    current_regenerator = git_blob(root, boundary_commit, regenerator["path"])
    imported_regenerator = git_blob(root, import_commit, regenerator["path"])
    require(current_regenerator == imported_regenerator,
            "REGENERATOR_DRIFT", "upstream macOS regeneration rule changed")
    require(sha256_bytes(current_regenerator) == regenerator["sha256"],
            "REGENERATOR_IDENTITY", "macOS regeneration rule identity drifted")
    regenerator_text = current_regenerator.decode("utf-8")
    require('#[cfg(target_os = "macos")]' in regenerator_text
            and 'Command::new("xcrun")' in regenerator_text
            and '"actool"' in regenerator_text,
            "REGENERATOR_DECLARATION",
            "generated binary is not bounded to the declared macOS actool rule")

    resource = boundary["resource_config"]
    require(resource["platform"] == "macos", "RESOURCE_PLATFORM",
            "generated resource configuration is not macOS-only")
    base_path = resource["base_path"]
    platform_path = resource["platform_path"]
    boundary_base = git_blob(root, boundary_commit, base_path)
    boundary_platform = git_blob(root, boundary_commit, platform_path)
    require(sha256_bytes(boundary_base) == resource["base_sha256"],
            "BASE_CONFIG_IDENTITY", "base Tauri configuration identity drifted")
    require(sha256_bytes(boundary_platform) == resource["platform_sha256"],
            "PLATFORM_CONFIG_IDENTITY", "macOS Tauri configuration identity drifted")

    imported_base = json.loads(git_blob(root, import_commit, base_path))
    imported_resources = imported_base.get("bundle", {}).get("resources")
    require(imported_resources == resource["resources"], "IMPORTED_RESOURCES",
            "recorded resource population differs from the imported 3/3 mappings")
    expected_base = dict(imported_base)
    expected_bundle = dict(imported_base["bundle"])
    expected_bundle.pop("resources")
    expected_base["bundle"] = expected_bundle
    current_base = json.loads(boundary_base)
    require(current_base == expected_base, "BASE_CONFIG_DELTA",
            "base Tauri config changed beyond removing the macOS resource mapping")
    platform_config = json.loads(boundary_platform)
    require(platform_config == {
        "$schema": imported_base["$schema"],
        "bundle": {"resources": resource["resources"]},
    }, "PLATFORM_CONFIG_DELTA",
            "macOS config differs from the exact 3/3 moved resource mappings")
    absent_at_import = git(
        root, "cat-file", "-e", f"{import_commit}:{platform_path}", check=False
    )
    require(absent_at_import.returncode != 0, "PLATFORM_CONFIG_PREEXISTED",
            "macOS split config unexpectedly existed in the immutable import root")
    return {"generated_binaries_removed": 1, **historical_counts}


def verify_remote_policy(root: Path, policy: dict[str, Any]) -> dict[str, int]:
    names = [
        line for line in git(root, "remote").stdout.decode("utf-8").splitlines()
        if line
    ]
    maximum = policy["maximum_configured_remotes"]
    authorized = set(policy["authorized_remote_urls"])
    require(len(names) <= maximum, "REMOTE_POPULATION",
            f"configured remote population exceeds {maximum}/{maximum}")

    fetch_urls = 0
    push_urls = 0
    for name in names:
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) is not None,
                "REMOTE_NAME", "configured remote name is not a bounded identifier")
        fetch = [
            line for line in git(
                root, "remote", "get-url", "--all", name, check=False
            ).stdout.decode("utf-8").splitlines()
            if line
        ]
        effective_push = [
            line for line in git(
                root, "remote", "get-url", "--push", "--all", name, check=False
            ).stdout.decode("utf-8").splitlines()
            if line
        ]
        require(len(fetch) == 1, "REMOTE_FETCH_POPULATION",
                "each configured remote needs exactly 1/1 fetch URL")
        require(len(effective_push) == 1, "REMOTE_PUSH_POPULATION",
                "each configured remote needs exactly 1/1 effective push URL")
        require(fetch[0] in authorized, "REMOTE_FETCH_URL",
                "configured fetch URL is outside the exact public repository allowlist")
        require(effective_push[0] in authorized, "REMOTE_PUSH_URL",
                "configured push URL is outside the exact public repository allowlist")
        fetch_urls += len(fetch)
        push_urls += len(effective_push)

    return {
        "remotes": len(names),
        "remote_fetch_urls": fetch_urls,
        "remote_push_urls": push_urls,
    }


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

    publication = provenance["publication"]
    boundary_commit = publication["hygiene_boundary_commit"]
    boundary_tree = git(
        root, "rev-parse", f"{boundary_commit}^{{tree}}"
    ).stdout.decode("ascii").strip()
    require(boundary_tree == publication["hygiene_boundary_tree"],
            "HYGIENE_BOUNDARY_TREE", "curated hygiene-boundary tree drifted")
    boundary_ancestor = git(
        root, "merge-base", "--is-ancestor", boundary_commit, "HEAD", check=False
    )
    require(boundary_ancestor.returncode == 0, "HYGIENE_BOUNDARY_ANCESTRY",
            "curated hygiene boundary is not an ancestor of the product candidate")

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

    boundary_paths = tracked_paths(root, boundary_commit)
    boundary_owned = sorted(
        path for path in boundary_paths if path.startswith(OWNED_PREFIX)
    )
    boundary_source = sorted(
        path for path in boundary_paths if not path.startswith(OWNED_PREFIX)
    )
    require(boundary_owned == provenance["import"]["control_paths"],
            "HYGIENE_CONTROL_PATHS",
            "hygiene-boundary import control population differs from 4/4")
    binary_boundary = provenance["hygiene_delta"]["generated_binary_boundary"]
    removed_path = binary_boundary["path"]
    added_path = binary_boundary["resource_config"]["platform_path"]
    expected_boundary_source = sorted(
        (set(import_source) - {removed_path}) | {added_path}
    )
    require(boundary_source == expected_boundary_source, "HYGIENE_SOURCE_PATHS",
            "hygiene-boundary source population differs from the exact "
            "405/406 retained plus 1/1 added paths")
    source_delta_raw = git(
        root, "diff", "--name-only", "-z", import_commit, boundary_commit, "--"
    ).stdout
    source_delta = sorted(
        part.decode("utf-8", "strict")
        for part in source_delta_raw.split(b"\0")
        if part and not part.decode("utf-8", "strict").startswith(OWNED_PREFIX)
    )
    normalized_rows = provenance["hygiene_delta"]["normalized_pngs"]
    expected_delta = provenance["hygiene_delta"]["changed_source_paths"]
    require(source_delta == expected_delta, "HYGIENE_SOURCE_DELTA",
            "hygiene-boundary source delta differs from the exact 8/8 bounded paths")
    normalized_count = verify_png_normalization(
        root, import_commit, boundary_commit, normalized_rows
    )
    generated_binary_counts = verify_generated_binary_boundary(
        root, import_commit, boundary_commit, binary_boundary
    )

    boundary_risks = risk_paths(boundary_source)
    require(boundary_risks == expected_risk_paths, "HYGIENE_RISK_POPULATION",
            "hygiene-boundary source risk population differs from expected 1/1: "
            f"{boundary_risks!r}")
    risk = provenance["expected_source_risks"][0]
    boundary_risk = git_blob(root, boundary_commit, risk["path"])
    require(len(boundary_risk) == risk["bytes"]
            and sha256_bytes(boundary_risk) == risk["sha256"],
            "HYGIENE_RISK_IDENTITY", "accepted empty placeholder identity drifted")

    current_paths = tracked_paths(root)
    owned = sorted(path for path in current_paths if path.startswith(OWNED_PREFIX))
    source = sorted(path for path in current_paths if not path.startswith(OWNED_PREFIX))
    require(owned == provenance["import"]["control_paths"], "OWNED_POPULATION",
            "current import control population differs from 4/4")
    current_risks = risk_paths(source)
    require(not current_risks, "CURRENT_RISK_POPULATION",
            f"current product tree contains forbidden artifact paths: {current_risks!r}")
    current_binary = git(
        root, "cat-file", "-e", f"HEAD:{removed_path}", check=False
    )
    require(current_binary.returncode != 0, "CURRENT_GENERATED_BINARY",
            "generated macOS binary is tracked in the current product tree")
    ignored = git(
        root, "check-ignore", "--quiet", "--no-index", "--", removed_path,
        check=False,
    )
    require(ignored.returncode == 0, "CURRENT_GENERATED_NOT_IGNORED",
            "regenerated macOS binary would become a publication input")

    licence_path = root / expected["licence_path"]
    require(sha256_file(licence_path) == expected["licence_sha256"],
            "LICENCE_IDENTITY", "upstream MIT licence bytes drifted")

    untracked_raw = git(root, "ls-files", "--others", "--exclude-standard", "-z").stdout
    untracked = [part.decode("utf-8", "strict") for part in untracked_raw.split(b"\0") if part]
    require(not risk_paths(untracked), "UNTRACKED_PRIVATE_ARTIFACT",
            "untracked private/generated artifact path is present")

    remote_counts = verify_remote_policy(root, publication)

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
        "boundary_source_files": len(boundary_source),
        "current_source_files": len(source),
        "current_source_risks": len(current_risks),
        "source_delta_paths": len(source_delta),
        "normalized_pngs": normalized_count,
        **generated_binary_counts,
        "platform_resource_configs": 1,
        "scanner_exception_candidates": 0,
        "owned_files": len(owned),
        "product_commits": len(authors),
        "hygiene_boundary_commits": 1,
        "hygiene_boundary_ancestors": 1,
        **remote_counts,
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
        f"{counts['hygiene_boundary_commits']}/1 hygiene-boundary commit; "
        f"{counts['hygiene_boundary_ancestors']}/1 hygiene-boundary ancestry; "
        f"{counts['boundary_source_files']}/406 hygiene-boundary source paths; "
        f"{counts['source_delta_paths']}/8 hygiene-boundary source-delta paths; "
        f"{counts['normalized_pngs']}/4 lossless PNG normalizations; "
        f"{counts['generated_binaries_removed']}/1 generated binary removed; "
        f"{counts['historical_binary_units']}/1 historical binary unit reproduced; "
        f"{counts['historical_matches']}/1 historical word match reproduced; "
        f"{counts['generator_inputs']}/3 generator inputs measured; "
        f"{counts['generator_input_bytes']}/8490356 generator-input bytes measured; "
        f"{counts['matched_generator_inputs']}/3 generator inputs matched; "
        f"{counts['historical_adjudications']}/1 independent historical adjudications; "
        f"{counts['platform_resource_configs']}/1 macOS-only resource config; "
        f"{counts['scanner_exception_candidates']}/0 scanner exceptions required; "
        f"{counts['owned_files']}/4 import-control files; "
        f"{counts['current_source_risks']}/{counts['current_source_files']} "
        f"forbidden current source paths; "
        f"{counts['product_commits']}/{counts['product_commits']} product commits "
        f"use the project identity; {counts['remotes']}/1 configured remotes; "
        f"{counts['remote_fetch_urls']}/{counts['remotes']} authorized fetch URLs; "
        f"{counts['remote_push_urls']}/{counts['remotes']} authorized push URLs; "
        "upstream-history commits=0/0)"
    )


if __name__ == "__main__":
    try:
        run()
    except (ImportErrorDetail, OSError, ValueError, json.JSONDecodeError,
            subprocess.CalledProcessError) as error:
        code = error.code if isinstance(error, ImportErrorDetail) else type(error).__name__
        print(f"VOICEBOX_CURATED_IMPORT: FAIL [{code}] {error}", file=sys.stderr)
        raise SystemExit(1)

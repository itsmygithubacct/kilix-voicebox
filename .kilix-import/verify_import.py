#!/usr/bin/env python3
"""Verify the exact curated Voicebox snapshot and its publication boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


OWNED_PREFIX = ".kilix-import/"
PROVENANCE_REL = ".kilix-import/PROVENANCE.json"
EXPECTED_SCHEMA = "kilix.voicebox.curated-import/v1"
EXPECTED_STATUS = "PREP7_CLEAN_IMPORT_CANDIDATE_NOT_P8"
EXPECTED_IDENTITY = "itsmygithubacct <itsmygithubacct@users.noreply.github.com>"
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


def risk_paths(paths: Iterable[str]) -> list[str]:
    return sorted(path for path in paths if RISK_PATTERN.search(path))


def tracked_paths(root: Path) -> list[str]:
    raw = git(root, "ls-files", "-z").stdout
    return [part.decode("utf-8", "strict") for part in raw.split(b"\0") if part]


def source_tree(root: Path, owned_paths: list[str]) -> str:
    scratch_root = os.environ.get("TMPDIR")
    with tempfile.TemporaryDirectory(prefix="voicebox-import-", dir=scratch_root) as temporary:
        index_path = str(Path(temporary) / "index")
        environment = os.environ.copy()
        environment["GIT_INDEX_FILE"] = index_path
        git(root, "read-tree", "HEAD", env=environment)
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


def verify_current_repository(root: Path, provenance: dict[str, Any]) -> dict[str, int]:
    status = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    require(status == b"", "WORKTREE_NOT_CLEAN",
            "exact import verification requires a clean tracked and untracked worktree")

    paths = tracked_paths(root)
    owned = sorted(path for path in paths if path.startswith(OWNED_PREFIX))
    source = sorted(path for path in paths if not path.startswith(OWNED_PREFIX))
    require(bool(owned), "OWNED_POPULATION", "curated import metadata is absent")
    observed_tree = source_tree(root, owned)
    expected = provenance["upstream"]
    require(observed_tree == expected["tree"], "SOURCE_TREE_MISMATCH",
            f"source tree {observed_tree} differs from accepted {expected['tree']}")

    tree_paths, blob_bytes = tree_population(root, observed_tree)
    require(tree_paths == source, "SOURCE_PATH_MISMATCH",
            "source path population differs from reconstructed tree")
    require(len(source) == expected["tracked_files"], "SOURCE_FILE_COUNT",
            f"source file population is {len(source)}/406")
    require(blob_bytes == expected["tracked_blob_bytes"], "SOURCE_BYTE_COUNT",
            f"source blob population is {blob_bytes}/54600537 bytes")

    risks = risk_paths(source)
    expected_risk_paths = [row["path"] for row in provenance["expected_source_risks"]]
    require(risks == expected_risk_paths, "SOURCE_RISK_POPULATION",
            f"source risk population differs from expected 1/1: {risks!r}")
    risk = provenance["expected_source_risks"][0]
    risk_path = root / risk["path"]
    require(risk_path.stat().st_size == risk["bytes"]
            and sha256_file(risk_path) == risk["sha256"],
            "SOURCE_RISK_IDENTITY", "accepted empty placeholder identity drifted")

    licence_path = root / expected["licence_path"]
    require(sha256_file(licence_path) == expected["licence_sha256"],
            "LICENCE_IDENTITY", "upstream MIT licence bytes drifted")

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
        "source_files": len(source),
        "source_blob_bytes": blob_bytes,
        "source_risks": len(risks),
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
        f"{counts['owned_files']}/{counts['owned_files']} import-control files; "
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

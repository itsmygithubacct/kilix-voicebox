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
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_identity(name: str) -> str:
    return subprocess.check_output(
        ("git", "rev-parse", name), cwd=ROOT, text=True, stderr=subprocess.DEVNULL
    ).strip()


def _require_regular_file(path: Path, description: str) -> None:
    # `is_file()` follows symlinks, so it alone would accept a link planted in
    # the staging directory pointing at an earlier build or at any readable
    # file on the host. Both facts are asserted.
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"Error: {description} is not a regular file: {path}")


def build_server(stage_dir: Path | str, build_id: str | None = None) -> Path:
    """Produce the sidecar into a fresh ``stage_dir`` and return its handoff.

    ``stage_dir`` must not exist. ``build_id`` is the caller's per-invocation
    identity; the handoff repeats it so a collector can prove the outputs it
    copies came from the invocation it made.
    """
    stage = Path(stage_dir)
    identity = build_id if build_id is not None else secrets.token_hex(16)
    if not BUILD_ID_PATTERN.fullmatch(identity):
        raise SystemExit(f"Error: build id must be 8-64 lowercase hex digits: {identity!r}")
    # Refuses an existing directory, so this build cannot inherit, overwrite or
    # re-publish the outputs of an earlier one.
    stage.mkdir(parents=True, exist_ok=False)
    dist = stage / DIST_NAME
    work = stage / WORK_NAME

    hidden = (
        "kilix_voicebox_backend",
        "kilix_voicebox_backend.main",
        "kilix_voicebox_backend.config",
        "kilix_voicebox_backend.database",
        "kilix_voicebox_backend.models",
        "kilix_voicebox_backend.profiles",
        "kilix_voicebox_backend.history",
        "kilix_voicebox_backend.tts",
        "kilix_voicebox_backend.transcribe",
        "kilix_voicebox_backend.kilix_provider",
        "kilix_voicebox_backend.orchestrator",
        "kilix_voicebox_backend.private_store",
        "kilix_voicebox_backend.utils.audio",
        "kilix_voicebox_backend.utils.cache",
        "kilix_voicebox_backend.utils.validation",
        "fastapi",
        "uvicorn",
        "sqlalchemy",
        "soundfile",
    )
    arguments = [
        str(ROOT / "kilix_voicebox_backend" / "server.py"),
        "--onefile",
        "--name",
        BINARY_STEM,
        "--paths",
        str(ROOT),
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
        "--noconfirm",
        "--clean",
    ]
    for module in hidden:
        arguments.extend(("--hidden-import", module))
    # Imported at point of use: PyInstaller is in the `build` dependency group,
    # not in [project.dependencies], and this module ships in the runtime wheel.
    # A module-level import made `import kilix_voicebox_backend.build_binary`
    # fail with ModuleNotFoundError for anyone who installed the wheel normally.
    import PyInstaller.__main__

    PyInstaller.__main__.run(arguments)
    suffix = ".exe" if (dist / f"{BINARY_STEM}.exe").is_file() else ""
    binary = dist / f"{BINARY_STEM}{suffix}"
    _require_regular_file(binary, "produced sidecar")
    manifest = {
        "schema": "kilix.voicebox.sidecar-provenance/candidate-v1",
        "source_commit": _git_identity("HEAD"),
        "source_tree": _git_identity("HEAD^{tree}"),
        "uv_lock_sha256": _sha256(ROOT / "uv.lock"),
        "sidecar_sha256": _sha256(binary),
        "sidecar_bytes": binary.stat().st_size,
        "contains_model_runtime": False,
        "provider_endpoints": ["kilix-qwen-tts.sock", "kilix-transcribe.sock"],
    }
    destination = dist / PROVENANCE_NAME
    destination.write_text(
        json.dumps(manifest, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _require_regular_file(destination, "produced provenance")

    # Written last, and only once every declared output exists: a producer that
    # fails at any earlier point leaves a staging directory with no handoff,
    # which the collector refuses rather than falling back to another path.
    handoff = stage / HANDOFF_NAME
    handoff.write_text(
        "".join(
            f"{key}={value}\n"
            for key, value in (
                ("schema", HANDOFF_SCHEMA),
                ("build_id", identity),
                ("binary", f"{DIST_NAME}/{binary.name}"),
                ("binary_sha256", manifest["sidecar_sha256"]),
                ("binary_bytes", str(manifest["sidecar_bytes"])),
                ("provenance", f"{DIST_NAME}/{PROVENANCE_NAME}"),
                ("provenance_sha256", _sha256(destination)),
            )
        ),
        encoding="ascii",
    )
    return handoff


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--stage-dir",
        required=True,
        type=Path,
        help="fresh staging directory this invocation owns; must not exist",
    )
    parser.add_argument(
        "--build-id",
        default=None,
        help="per-invocation identity repeated in the handoff (8-64 hex digits)",
    )
    options = parser.parse_args(argv)
    build_server(options.stage_dir, options.build_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

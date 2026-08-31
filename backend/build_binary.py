"""Build the product-only Voicebox sidecar without model runtimes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import PyInstaller.__main__


ROOT = Path(__file__).resolve().parent.parent


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


def build_server() -> None:
    hidden = (
        "backend",
        "backend.main",
        "backend.config",
        "backend.database",
        "backend.models",
        "backend.profiles",
        "backend.history",
        "backend.tts",
        "backend.transcribe",
        "backend.kilix_provider",
        "backend.orchestrator",
        "backend.private_store",
        "backend.utils.audio",
        "backend.utils.cache",
        "backend.utils.validation",
        "fastapi",
        "uvicorn",
        "sqlalchemy",
        "soundfile",
    )
    arguments = [
        str(ROOT / "backend" / "server.py"),
        "--onefile",
        "--name",
        "voicebox-server",
        "--paths",
        str(ROOT),
        "--distpath",
        str(ROOT / "backend" / "dist"),
        "--workpath",
        str(ROOT / "backend" / "build"),
        "--specpath",
        str(ROOT / "backend" / "build"),
        "--noconfirm",
        "--clean",
    ]
    for module in hidden:
        arguments.extend(("--hidden-import", module))
    PyInstaller.__main__.run(arguments)
    suffix = ".exe" if (ROOT / "backend" / "dist" / "voicebox-server.exe").is_file() else ""
    binary = ROOT / "backend" / "dist" / f"voicebox-server{suffix}"
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
    destination = ROOT / "backend" / "dist" / "voicebox-server.provenance.json"
    destination.write_text(
        json.dumps(manifest, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    build_server()

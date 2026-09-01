"""Private XDG-backed Voicebox product storage configuration."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def _default_data_dir() -> Path:
    configured = os.environ.get("XDG_DATA_HOME")
    base = Path(configured) if configured else Path.home() / ".local" / "share"
    return base / "kilix-voicebox"


_data_dir = _default_data_dir()


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("Voicebox data path is not a directory")
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("Voicebox data path must be private and owned by this user")
    return path


def set_data_dir(path: str | Path) -> None:
    global _data_dir
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError("Voicebox data directory must be absolute")
    _data_dir = _private_directory(candidate)


def get_data_dir() -> Path:
    return _private_directory(_data_dir)


def get_db_path() -> Path:
    return get_data_dir() / "voicebox.db"


def _child(name: str) -> Path:
    return _private_directory(get_data_dir() / name)


def get_profiles_dir() -> Path:
    return _child("profiles")


def get_generations_dir() -> Path:
    return _child("generations")


def get_cache_dir() -> Path:
    return _child("cache")


def get_models_dir() -> Path:
    """Compatibility location; model payloads are never stored here."""

    return _child("provider-metadata")


def get_privacy_dir() -> Path:
    return _child("privacy")

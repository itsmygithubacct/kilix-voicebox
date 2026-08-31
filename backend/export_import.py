"""Bounded profile export/import and provenance-preserving generation export."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from . import config
from .database import (
    Generation as DBGeneration,
    ProfileSample as DBProfileSample,
    VoiceProfile as DBVoiceProfile,
)
from .history import owned_generation_path
from .models import VoiceProfileCreate, VoiceProfileResponse
from .private_store import MAX_GENERATION_BYTES, MAX_SAMPLE_BYTES, PrivateVoiceStore
from .profiles import (
    add_profile_sample,
    create_profile,
    owned_avatar_path,
    owned_sample_path,
    upload_avatar,
)


MAX_ARCHIVE_ENTRIES = 68
MAX_ARCHIVE_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_JSON_BYTES = 65_536
MAX_AVATAR_BYTES = 10 * 1024 * 1024
_SAMPLE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}\.wav$")
_AVATAR_NAMES = frozenset({"avatar.jpg", "avatar.jpeg", "avatar.png", "avatar.webp"})
_ZIP_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON object contains a duplicate key")
        value[key] = item
    return value


def _invalid_constant(_value: str) -> None:
    raise ValueError("JSON contains a non-finite number")


def _json_payload(value: dict[str, Any]) -> bytes:
    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError("export metadata is not valid JSON") from error
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError("export metadata exceeds its bound")
    return payload


def _unique_profile_name(name: str, db: Session) -> str:
    base = name
    candidate = base
    counter = 1
    while db.query(DBVoiceProfile).filter_by(name=candidate).first() is not None:
        candidate = f"{base} ({counter})"
        counter += 1
    return candidate


def _owned_bytes(path: Path, maximum: int) -> bytes:
    """Read one bounded same-owner regular file without following a symlink."""

    try:
        before = path.stat(follow_symlinks=False)
    except OSError as error:
        raise ValueError("owned file is unavailable") from error
    if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid():
        raise ValueError("owned file is not a same-user regular file")
    if before.st_size <= 0 or before.st_size > maximum:
        raise ValueError("owned file size is outside its bound")
    descriptor = -1
    payload = bytearray()
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
    except OSError as error:
        raise ValueError("owned file read failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > maximum:
        raise ValueError("owned file grew beyond its bound")
    if (after.st_dev, after.st_ino, after.st_size) != (
        before.st_dev,
        before.st_ino,
        before.st_size,
    ):
        raise ValueError("owned file changed while being read")
    return bytes(payload)


def _open_archive(file_bytes: bytes) -> zipfile.ZipFile:
    archive: zipfile.ZipFile | None = None
    try:
        archive = zipfile.ZipFile(io.BytesIO(file_bytes), "r")
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if not 1 <= len(infos) <= MAX_ARCHIVE_ENTRIES or len(names) != len(set(names)):
            raise ValueError("archive entry population is invalid")
        total = 0
        for info in infos:
            mode = info.external_attr >> 16
            if (
                info.is_dir()
                or info.flag_bits & 0x1
                or info.compress_type not in _ZIP_METHODS
                or (stat.S_IFMT(mode) not in {0, stat.S_IFREG})
                or info.filename.startswith("/")
                or "\\" in info.filename
                or any(part in {"", ".", ".."} for part in info.filename.split("/"))
            ):
                raise ValueError("archive contains an unsafe entry")
            total += info.file_size
            if total > MAX_ARCHIVE_EXPANDED_BYTES:
                raise ValueError("archive expanded size exceeds its bound")
        return archive
    except ValueError:
        if archive is not None:
            archive.close()
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        if archive is not None:
            archive.close()
        raise ValueError("invalid ZIP archive") from error


def _read_member(archive: zipfile.ZipFile, name: str, maximum: int) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise ValueError(f"archive is missing {name}") from error
    if info.file_size > maximum:
        raise ValueError(f"archive member {name} exceeds its bound")
    payload = bytearray()
    try:
        with archive.open(info, "r") as source:
            while len(payload) <= maximum:
                chunk = source.read(min(1024 * 1024, maximum + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise ValueError(f"archive member {name} could not be read") from error
    if len(payload) > maximum:
        raise ValueError(f"archive member {name} exceeds its bound")
    return bytes(payload)


def _read_json_object(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            _read_member(archive, name, MAX_JSON_BYTES).decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ValueError(f"archive member {name} is not valid JSON") from error
    if type(value) is not dict:
        raise ValueError(f"archive member {name} is not a JSON object")
    return value


def _temporary(payload: bytes, suffix: str) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=suffix, delete=False, dir=config.get_cache_dir()
    ) as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
        return Path(output.name)


def export_profile_to_zip(
    profile_id: str,
    db: Session,
    *,
    include_samples: bool = False,
) -> bytes:
    """Export profile metadata; source samples are opt-in and never implicit."""

    profile = db.query(DBVoiceProfile).filter_by(id=profile_id).first()
    if profile is None:
        raise ValueError("profile not found")
    samples = db.query(DBProfileSample).filter_by(profile_id=profile_id).all()
    if len(samples) > MAX_ARCHIVE_ENTRIES - 3:
        raise ValueError("profile sample population exceeds archive bound")

    output = io.BytesIO()
    expanded = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        def write(name: str, payload: bytes) -> None:
            nonlocal expanded
            expanded += len(payload)
            if expanded > MAX_ARCHIVE_EXPANDED_BYTES:
                raise ValueError("profile export exceeds its expanded-size bound")
            archive.writestr(name, payload)

        avatar_name: str | None = None
        if profile.avatar_path:
            avatar_path = owned_avatar_path(profile)
            if avatar_path is None:
                raise ValueError("profile avatar path is unavailable")
            suffix = avatar_path.suffix.lower()
            candidate = f"avatar{suffix}"
            if candidate not in _AVATAR_NAMES:
                raise ValueError("profile avatar format is unsupported")
            write(candidate, _owned_bytes(avatar_path, MAX_AVATAR_BYTES))
            avatar_name = candidate

        manifest = {
            "schema": "kilix.voicebox.profile-export/candidate-v1",
            "version": "1.0",
            "profile": {
                "name": profile.name,
                "description": profile.description,
                "language": profile.language,
            },
            "avatar": avatar_name,
            "samples_included": include_samples,
        }
        write("manifest.json", _json_payload(manifest))

        sample_metadata: dict[str, str] = {}
        if include_samples:
            for sample in samples:
                sample_path = owned_sample_path(sample)
                filename = sample_path.name
                if _SAMPLE_NAME.fullmatch(filename) is None:
                    raise ValueError("profile sample name is unsafe")
                write(
                    f"samples/{filename}",
                    _owned_bytes(sample_path, MAX_SAMPLE_BYTES),
                )
                sample_metadata[filename] = sample.reference_text
        write("samples.json", _json_payload(sample_metadata))
    return output.getvalue()


async def import_profile_from_zip(file_bytes: bytes, db: Session) -> VoiceProfileResponse:
    """Import a bounded profile archive after validating its complete population."""

    archive = _open_archive(file_bytes)
    try:
        manifest = _read_json_object(archive, "manifest.json")
        samples = _read_json_object(archive, "samples.json")
        if manifest.get("schema") != "kilix.voicebox.profile-export/candidate-v1":
            raise ValueError("profile archive schema is unsupported")
        if manifest.get("version") != "1.0" or type(manifest.get("profile")) is not dict:
            raise ValueError("profile archive manifest is invalid")
        if type(manifest.get("samples_included")) is not bool:
            raise ValueError("profile archive sample selection is invalid")
        profile_data = manifest["profile"]
        expected_profile_fields = {"name", "description", "language"}
        if set(profile_data) != expected_profile_fields:
            raise ValueError("profile archive fields are invalid")
        if type(profile_data.get("name")) is not str:
            raise ValueError("profile archive name is invalid")
        if len(samples) > MAX_ARCHIVE_ENTRIES - 3:
            raise ValueError("profile archive sample population exceeds its bound")
        if bool(samples) != manifest["samples_included"]:
            raise ValueError("profile archive sample selection disagrees with its manifest")

        sample_payloads: list[tuple[str, str, bytes]] = []
        for filename, reference_text in samples.items():
            if _SAMPLE_NAME.fullmatch(filename) is None:
                raise ValueError("profile archive sample name is unsafe")
            if type(reference_text) is not str or not 1 <= len(reference_text) <= 1000:
                raise ValueError("profile archive reference text is invalid")
            sample_payloads.append(
                (filename, reference_text, _read_member(archive, f"samples/{filename}", MAX_SAMPLE_BYTES))
            )

        avatar_name = manifest.get("avatar")
        if avatar_name is not None and avatar_name not in _AVATAR_NAMES:
            raise ValueError("profile archive avatar name is invalid")
        avatar_payload = (
            _read_member(archive, avatar_name, MAX_AVATAR_BYTES)
            if type(avatar_name) is str
            else None
        )
        allowed = {"manifest.json", "samples.json"}
        allowed.update(f"samples/{filename}" for filename, _text, _payload in sample_payloads)
        if avatar_name is not None:
            allowed.add(avatar_name)
        if {info.filename for info in archive.infolist()} != allowed:
            raise ValueError("profile archive contains undeclared entries")

        profile_create = VoiceProfileCreate(
            name=_unique_profile_name(profile_data["name"], db),
            description=profile_data["description"],
            language=profile_data["language"],
        )
    finally:
        archive.close()

    profile: VoiceProfileResponse | None = None
    temporary_paths: list[Path] = []
    try:
        profile = await create_profile(profile_create, db)
        if avatar_payload is not None and avatar_name is not None:
            path = _temporary(avatar_payload, Path(avatar_name).suffix)
            temporary_paths.append(path)
            await upload_avatar(profile.id, str(path), db)
        for filename, reference_text, payload in sample_payloads:
            path = _temporary(payload, Path(filename).suffix)
            temporary_paths.append(path)
            await add_profile_sample(profile.id, str(path), reference_text, db)
        refreshed = db.query(DBVoiceProfile).filter_by(id=profile.id).first()
        if refreshed is None:
            raise ValueError("imported profile disappeared")
        return VoiceProfileResponse.model_validate(refreshed)
    except Exception:
        db.rollback()
        if profile is not None:
            db.query(DBProfileSample).filter_by(profile_id=profile.id).delete()
            stored = db.query(DBVoiceProfile).filter_by(id=profile.id).first()
            if stored is not None:
                db.delete(stored)
            db.commit()
            shutil.rmtree(config.get_profiles_dir() / profile.id, ignore_errors=True)
        raise
    finally:
        for path in temporary_paths:
            path.unlink(missing_ok=True)


def export_generation_to_zip(generation_id: str, db: Session) -> bytes:
    """Export generated audio together with its verified provenance sidecar."""

    generation = db.query(DBGeneration).filter_by(id=generation_id).first()
    if generation is None:
        raise ValueError("generation not found")
    profile = db.query(DBVoiceProfile).filter_by(id=generation.profile_id).first()
    if profile is None:
        raise ValueError("generation profile not found")

    audio_path = owned_generation_path(generation)
    suffix = audio_path.suffix.lower()
    audio = _owned_bytes(
        audio_path,
        min(MAX_GENERATION_BYTES, MAX_ARCHIVE_EXPANDED_BYTES - 2 * MAX_JSON_BYTES),
    )
    provenance = PrivateVoiceStore(config.get_privacy_dir()).generation_provenance(
        generation_id
    )
    if (
        provenance.get("output_sha256") != hashlib.sha256(audio).hexdigest()
        or provenance.get("output_bytes") != len(audio)
    ):
        raise ValueError("generation bytes disagree with provenance")

    manifest = {
        "schema": "kilix.voicebox.generation-export/candidate-v1",
        "version": "1.0",
        "generation": {
            "id": generation.id,
            "text": generation.text,
            "language": generation.language,
            "duration": generation.duration,
            "seed": generation.seed,
            "instruct": generation.instruct,
            "created_at": generation.created_at.isoformat(),
        },
        "profile": {"id": profile.id, "name": profile.name},
        "audio": f"audio/{generation.id}{suffix}",
        "provenance": "provenance.json",
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", _json_payload(manifest))
        archive.writestr(manifest["audio"], audio)
        archive.writestr("provenance.json", _json_payload(provenance))
    return output.getvalue()

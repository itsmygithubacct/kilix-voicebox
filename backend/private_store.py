"""Private, atomic consent and generation provenance storage for Voicebox."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable


MAX_SAMPLE_BYTES = 11_520_000
MAX_GENERATION_BYTES = 1_152_000_000
MAX_RECORD_BYTES = 65_536
MAX_RECOVERY_RECORDS = 4_097
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class StoreError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise StoreError(code, message)


def _identifier(value: str, name: str) -> str:
    _require(type(value) is str and _IDENTIFIER.fullmatch(value) is not None,
             "INVALID_ID", f"{name} is outside its safe population")
    return value


def _digest(value: str, name: str) -> str:
    _require(type(value) is str and _DIGEST.fullmatch(value) is not None,
             "INVALID_DIGEST", f"{name} is not a canonical SHA-256")
    return value


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical(value: dict) -> bytes:
    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise StoreError("INVALID_RECORD", "record is not canonical JSON") from error
    _require(len(payload) <= MAX_RECORD_BYTES, "RECORD_TOO_LARGE", "record exceeds its bound")
    return payload + b"\n"


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat(follow_symlinks=False)
    _require(stat.S_ISDIR(info.st_mode), "UNSAFE_STORE", "store path is not a directory")
    _require(info.st_uid == os.geteuid(), "UNSAFE_STORE", "store path has another owner")
    _require(info.st_mode & 0o077 == 0, "UNSAFE_STORE", "store path is not private")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _hash_regular_file(path: Path, maximum: int) -> tuple[str, int]:
    info = path.stat(follow_symlinks=False)
    _require(stat.S_ISREG(info.st_mode), "UNSAFE_INPUT", "input is not a regular file")
    _require(info.st_uid == os.geteuid(), "UNSAFE_INPUT", "input has another owner")
    _require(0 < info.st_size <= maximum, "INPUT_SIZE", "input size is outside its bound")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            _require(total <= maximum, "INPUT_SIZE", "input grew beyond its bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
        _require((after.st_dev, after.st_ino, after.st_size)
                 == (info.st_dev, info.st_ino, info.st_size),
                 "INPUT_CHANGED", "input identity changed while hashing")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), total


def _read_private_record(path: Path) -> dict:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as error:
        raise StoreError("CONSENT_NOT_FOUND", "consent record is unavailable") from error
    _require(stat.S_ISREG(info.st_mode), "UNSAFE_STORE", "record is not a regular file")
    _require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
             "UNSAFE_STORE", "record is not private")
    _require(0 < info.st_size <= MAX_RECORD_BYTES, "INVALID_RECORD",
             "record size is outside its bound")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        payload = b""
        while len(payload) <= MAX_RECORD_BYTES:
            chunk = os.read(descriptor, min(65_536, MAX_RECORD_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload += chunk
        after = os.fstat(descriptor)
    except OSError as error:
        raise StoreError("INVALID_RECORD", "record read failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _require(
        (after.st_dev, after.st_ino, after.st_size)
        == (info.st_dev, info.st_ino, info.st_size),
        "INVALID_RECORD",
        "record identity changed while reading",
    )
    _require(len(payload) <= MAX_RECORD_BYTES, "INVALID_RECORD", "record exceeds its bound")
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise StoreError("INVALID_RECORD", "record is not valid JSON") from error
    _require(type(value) is dict and _canonical(value) == payload,
             "INVALID_RECORD", "record is not canonical JSON")
    return value


def read_owned_sample(path: Path) -> tuple[bytes, str]:
    """Read one bounded, same-owner regular sample through a no-follow descriptor."""

    info = path.stat(follow_symlinks=False)
    _require(stat.S_ISREG(info.st_mode), "UNSAFE_INPUT", "input is not a regular file")
    _require(info.st_uid == os.geteuid(), "UNSAFE_INPUT", "input has another owner")
    _require(0 < info.st_size <= MAX_SAMPLE_BYTES, "INPUT_SIZE",
             "input size is outside its bound")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            _require(total <= MAX_SAMPLE_BYTES, "INPUT_SIZE", "input grew beyond its bound")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        _require(
            (after.st_dev, after.st_ino, after.st_size)
            == (info.st_dev, info.st_ino, info.st_size),
            "INPUT_CHANGED",
            "input identity changed while reading",
        )
    finally:
        os.close(descriptor)
    value = b"".join(chunks)
    return value, hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class ConsentAttestation:
    schema: str
    profile_id: str
    source_sha256: str
    source_container_sha256: str
    asserted_by_user: bool
    allowed_use: str
    purpose: str | None
    recorded_at: str

    @classmethod
    def create(
        cls,
        profile_id: str,
        source_sha256: str,
        source_container_sha256: str,
        asserted_by_user: bool,
        allowed_use: str,
        purpose: str | None,
    ) -> "ConsentAttestation":
        _identifier(profile_id, "profile_id")
        _digest(source_sha256, "source_sha256")
        _digest(source_container_sha256, "source_container_sha256")
        _require(asserted_by_user is True, "CONSENT_REQUIRED",
                 "voice-cloning consent must be explicitly asserted")
        _require(allowed_use in {"this-project", "named-purpose"}, "INVALID_CONSENT",
                 "allowed use is unsupported")
        if allowed_use == "named-purpose":
            _require(type(purpose) is str and bool(purpose.strip())
                     and len(purpose.encode("utf-8")) <= 256,
                     "INVALID_CONSENT", "named purpose is required and bounded")
        else:
            _require(purpose is None, "INVALID_CONSENT",
                     "this-project consent forbids a named purpose")
        return cls(
            "kilix.voice.consent/candidate-v1",
            profile_id,
            source_sha256,
            source_container_sha256,
            True,
            allowed_use,
            purpose,
            _timestamp(),
        )

    @property
    def wire_value(self) -> dict:
        return {
            "allowed_use": self.allowed_use,
            "asserted_by_peer": True,
            "purpose": self.purpose,
            "recorded_at": self.recorded_at,
            "schema": self.schema,
            "source_sha256": self.source_sha256,
        }

    @property
    def sha256(self) -> str:
        payload = json.dumps(
            self.wire_value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class GenerationProvenance:
    schema: str
    generation_id: str
    job_id: str
    provider: str
    model_id: str
    model_revision: str
    output_sha256: str
    output_bytes: int
    source_sha256: str
    consent_sha256: str
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        generation_id: str,
        job_id: str,
        provider: str,
        model_id: str,
        model_revision: str,
        output_sha256: str,
        output_bytes: int,
        source_sha256: str,
        consent_sha256: str,
    ) -> "GenerationProvenance":
        for name, value in (("generation_id", generation_id), ("job_id", job_id),
                            ("provider", provider), ("model_id", model_id),
                            ("model_revision", model_revision)):
            _identifier(value, name)
        for name, value in (("output_sha256", output_sha256),
                            ("source_sha256", source_sha256),
                            ("consent_sha256", consent_sha256)):
            _digest(value, name)
        _require(type(output_bytes) is int and 0 < output_bytes <= MAX_GENERATION_BYTES,
                 "OUTPUT_SIZE", "output byte count is outside its bound")
        return cls(
            "kilix.voice.generation-provenance/candidate-v1",
            generation_id,
            job_id,
            provider,
            model_id,
            model_revision,
            output_sha256,
            output_bytes,
            source_sha256,
            consent_sha256,
            _timestamp(),
        )


class PrivateVoiceStore:
    """Provider-independent product data with private modes and atomic records."""

    def __init__(self, root: Path):
        _require(isinstance(root, Path) and root.is_absolute(), "UNSAFE_STORE",
                 "store root must be an absolute Path")
        _private_directory(root)
        self.root = root
        for name in ("consent", "generations", "trash"):
            _private_directory(root / name)

    @property
    def consent_root(self) -> Path:
        return self.root / "consent"

    @property
    def generation_root(self) -> Path:
        return self.root / "generations"

    def _atomic_record(self, destination: Path, value: dict) -> None:
        payload = _canonical(value)
        temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.partial"
        descriptor = -1
        destination_created = False
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                _require(written > 0, "STORE_WRITE", "record write stalled")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.link(temporary, destination, follow_symlinks=False)
            destination_created = True
            temporary.unlink()
            _fsync_directory(destination.parent)
        except (OSError, StoreError) as error:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            if destination_created:
                destination.unlink(missing_ok=True)
            if isinstance(error, StoreError):
                raise
            raise StoreError("STORE_WRITE", "atomic record commit failed") from error

    def attest_sample(
        self,
        *,
        profile_id: str,
        sample_path: Path,
        asserted_by_user: bool,
        provider_source: bytes | None = None,
        allowed_use: str = "this-project",
        purpose: str | None = None,
    ) -> ConsentAttestation:
        source_container_sha256, _size = _hash_regular_file(sample_path, MAX_SAMPLE_BYTES)
        if provider_source is None:
            source_sha256 = source_container_sha256
        else:
            _require(type(provider_source) is bytes and 0 < len(provider_source) <= MAX_SAMPLE_BYTES,
                     "INPUT_SIZE", "provider source size is outside its bound")
            source_sha256 = hashlib.sha256(provider_source).hexdigest()
        attestation = ConsentAttestation.create(
            profile_id,
            source_sha256,
            source_container_sha256,
            asserted_by_user,
            allowed_use,
            purpose,
        )
        destination = self.consent_root / f"{attestation.sha256}.json"
        if not destination.exists():
            self._atomic_record(destination, {
                "schema": "kilix.voicebox.consent-record/candidate-v1",
                "profile_id": attestation.profile_id,
                "source_container_sha256": attestation.source_container_sha256,
                "consent": attestation.wire_value,
                "consent_sha256": attestation.sha256,
                "captured_at": attestation.recorded_at,
            })
        return attestation

    def commit_generation(
        self,
        *,
        source: BinaryIO,
        provenance: GenerationProvenance,
        suffix: str = ".wav",
    ) -> Path:
        _require(suffix in {".wav", ".flac", ".opus"}, "OUTPUT_FORMAT",
                 "generation format is unsupported")
        _require(provenance.provider == "kilix-qwen-tts", "INVALID_RECORD",
                 "generation provider is unsupported")
        consent_record = _read_private_record(
            self.consent_root / f"{provenance.consent_sha256}.json"
        )
        consent = consent_record.get("consent")
        _require(
            consent_record.get("consent_sha256") == provenance.consent_sha256
            and type(consent) is dict
            and consent.get("source_sha256") == provenance.source_sha256,
            "INVALID_RECORD",
            "generation provenance is not bound to stored consent",
        )
        generation_id = _identifier(provenance.generation_id, "generation_id")
        destination = self.generation_root / f"{generation_id}{suffix}"
        temporary = self.generation_root / f".{generation_id}.{uuid.uuid4().hex}.partial"
        descriptor = -1
        digest = hashlib.sha256()
        total = 0
        destination_created = False
        provenance_created = False
        output_link_created = False
        provenance_path = self.generation_root / f"{generation_id}.provenance.json"
        output_link_path = (
            self.consent_root
            / f"{provenance.consent_sha256}.{generation_id}.derived-output.json"
        )
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                _require(type(chunk) is bytes, "OUTPUT_READ", "generation source returned non-bytes")
                total += len(chunk)
                _require(total <= MAX_GENERATION_BYTES, "OUTPUT_SIZE",
                         "generation exceeds its byte bound")
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(descriptor, view)
                    _require(written > 0, "STORE_WRITE", "generation write stalled")
                    view = view[written:]
            _require(total == provenance.output_bytes and digest.hexdigest() == provenance.output_sha256,
                     "OUTPUT_DIGEST", "generation bytes disagree with provenance")
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.link(temporary, destination, follow_symlinks=False)
            destination_created = True
            temporary.unlink()
            self._atomic_record(provenance_path, asdict(provenance))
            provenance_created = True
            self._atomic_record(output_link_path, {
                "schema": "kilix.voicebox.consent-derived-output/candidate-v1",
                "consent_sha256": provenance.consent_sha256,
                "generation_id": provenance.generation_id,
                "model_id": provenance.model_id,
                "model_revision": provenance.model_revision,
                "output_name": destination.name,
                "output_sha256": provenance.output_sha256,
                "provider": provenance.provider,
                "source_sha256": provenance.source_sha256,
            })
            output_link_created = True
            _fsync_directory(self.generation_root)
            return destination
        except (OSError, StoreError) as error:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            if output_link_created:
                output_link_path.unlink(missing_ok=True)
            if provenance_created:
                provenance_path.unlink(missing_ok=True)
            if destination_created:
                destination.unlink(missing_ok=True)
            if isinstance(error, StoreError):
                raise
            raise StoreError("STORE_WRITE", "atomic generation commit failed") from error

    def preview_generation_delete(self, generation_id: str) -> tuple[str, ...]:
        identifier = _identifier(generation_id, "generation_id")
        names = []
        for suffix in (".wav", ".flac", ".opus", ".provenance.json"):
            candidate = self.generation_root / f"{identifier}{suffix}"
            if candidate.is_file() and not candidate.is_symlink():
                names.append(f"generations/{candidate.name}")
        for candidate in self.consent_root.glob(
            f"*.{identifier}.derived-output.json"
        ):
            if candidate.is_file() and not candidate.is_symlink():
                names.append(f"consent/{candidate.name}")
        return tuple(names)

    def generation_provenance(self, generation_id: str) -> dict:
        """Return one canonical private provenance record after identity checks."""

        identifier = _identifier(generation_id, "generation_id")
        value = _read_private_record(
            self.generation_root / f"{identifier}.provenance.json"
        )
        _require(
            value.get("schema") == "kilix.voice.generation-provenance/candidate-v1"
            and value.get("generation_id") == identifier,
            "INVALID_RECORD",
            "generation provenance has the wrong identity",
        )
        return value

    def _owned_path_population(self, paths: Iterable[Path]) -> tuple[str, ...]:
        data_root = self.root.parent
        logical: list[str] = []
        seen: set[Path] = set()

        def inspect(candidate: Path) -> None:
            try:
                relative = candidate.relative_to(data_root)
                info = candidate.stat(follow_symlinks=False)
            except (OSError, ValueError) as error:
                raise StoreError("UNSAFE_DELETE", "delete target is outside product storage") from error
            _require(relative.parts and relative.parts[0] != "privacy", "UNSAFE_DELETE",
                     "generic delete cannot target privacy records")
            _require(candidate not in seen, "UNSAFE_DELETE", "delete target population overlaps")
            seen.add(candidate)
            _require(info.st_uid == os.geteuid() and not candidate.is_symlink(),
                     "UNSAFE_DELETE", "delete target is not same-user owned")
            if stat.S_ISREG(info.st_mode):
                logical.append(relative.as_posix())
                return
            _require(stat.S_ISDIR(info.st_mode), "UNSAFE_DELETE",
                     "delete target is not a regular file or directory")
            logical.append(f"{relative.as_posix()}/")
            try:
                children = sorted(candidate.iterdir(), key=lambda item: item.name)
            except OSError as error:
                raise StoreError("UNSAFE_DELETE", "delete directory could not be enumerated") from error
            for child in children:
                inspect(child)

        roots = tuple(paths)
        _require(bool(roots), "NOT_FOUND", "delete target population is empty")
        for path in roots:
            _require(isinstance(path, Path) and path.is_absolute(), "UNSAFE_DELETE",
                     "delete target must be an absolute Path")
            inspect(path)
        return tuple(logical)

    def _recovery_records(
        self,
        records: dict[str, dict] | None,
    ) -> tuple[tuple[str, dict], ...]:
        if records is None:
            return ()
        _require(type(records) is dict and 0 < len(records) <= MAX_RECOVERY_RECORDS,
                 "RECORD_POPULATION", "recovery record population is outside its bound")
        validated: list[tuple[str, dict]] = []
        for logical, value in sorted(records.items()):
            _require(type(logical) is str and type(value) is dict,
                     "INVALID_RECORD", "recovery record is not a named object")
            path = PurePosixPath(logical)
            _require(
                not path.is_absolute()
                and 1 <= len(path.parts) <= 4
                and all(_IDENTIFIER.fullmatch(part) is not None for part in path.parts),
                "INVALID_RECORD",
                "recovery record name is unsafe",
            )
            _canonical(value)
            validated.append((path.as_posix(), value))
        return tuple(validated)

    def preview_owned_delete(
        self,
        paths: Iterable[Path],
        *,
        records: dict[str, dict] | None = None,
    ) -> tuple[str, ...]:
        """Enumerate the exact product-owned population before a destructive action."""

        files = self._owned_path_population(paths)
        archived = tuple(name for name, _value in self._recovery_records(records))
        return files + archived

    def preview_record_archive(self, records: dict[str, dict]) -> tuple[str, ...]:
        """Validate and enumerate database-only records before deletion."""

        return tuple(name for name, _value in self._recovery_records(records))

    def archive_records(
        self,
        *,
        kind: str,
        object_id: str,
        records: dict[str, dict],
        metadata: dict,
    ) -> dict:
        """Commit database-only product records to private recoverable trash."""

        safe_kind = _identifier(kind, "kind")
        safe_id = _identifier(object_id, "object_id")
        archived = self._recovery_records(records)
        _canonical(metadata)
        batch_id = f"{safe_kind}-{safe_id}-{uuid.uuid4().hex}"
        batch = self.root / "trash" / batch_id
        _private_directory(batch)
        try:
            for logical, value in archived:
                destination = batch / "records" / Path(*PurePosixPath(logical).parts)
                _private_directory(destination.parent)
                self._atomic_record(destination, value)
            names = [name for name, _value in archived]
            self._atomic_record(batch / "delete-receipt.json", {
                "schema": "kilix.voicebox.recoverable-delete/candidate-v1",
                "batch_id": batch_id,
                "kind": safe_kind,
                "object_id": safe_id,
                "moved": [],
                "archived": names,
                "metadata": metadata,
                "recorded_at": _timestamp(),
            })
            _fsync_directory(batch)
            return {"batch_id": batch_id, "files": names, "count": len(names)}
        except (OSError, StoreError) as error:
            shutil.rmtree(batch, ignore_errors=True)
            if isinstance(error, StoreError):
                raise
            raise StoreError("STORE_WRITE", "recovery record commit failed") from error

    def trash_owned_paths(
        self,
        *,
        kind: str,
        object_id: str,
        paths: Iterable[Path],
        metadata: dict,
        records: dict[str, dict] | None = None,
    ) -> dict:
        """Atomically move product paths to a private batch with a recovery receipt."""

        safe_kind = _identifier(kind, "kind")
        safe_id = _identifier(object_id, "object_id")
        roots = tuple(paths)
        moved_population = self._owned_path_population(roots)
        archived = self._recovery_records(records)
        _canonical(metadata)
        preview = moved_population + tuple(name for name, _value in archived)
        data_root = self.root.parent
        batch_id = f"{safe_kind}-{safe_id}-{uuid.uuid4().hex}"
        batch = self.root / "trash" / batch_id
        _private_directory(batch)
        moved: list[tuple[Path, Path]] = []
        try:
            for source in roots:
                relative = source.relative_to(data_root)
                target = batch / relative
                _private_directory(target.parent)
                os.replace(source, target)
                moved.append((source, target))
                _fsync_directory(source.parent)
            for logical, value in archived:
                destination = batch / "records" / Path(*PurePosixPath(logical).parts)
                _private_directory(destination.parent)
                self._atomic_record(destination, value)
            self._atomic_record(batch / "delete-receipt.json", {
                "schema": "kilix.voicebox.recoverable-delete/candidate-v1",
                "batch_id": batch_id,
                "kind": safe_kind,
                "object_id": safe_id,
                "moved": list(moved_population),
                "archived": [name for name, _value in archived],
                "metadata": metadata,
                "recorded_at": _timestamp(),
            })
            _fsync_directory(batch)
            return {"batch_id": batch_id, "files": list(preview), "count": len(preview)}
        except (OSError, StoreError) as error:
            for source, target in reversed(moved):
                try:
                    source.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    os.replace(target, source)
                except OSError:
                    pass
            shutil.rmtree(batch, ignore_errors=True)
            if isinstance(error, StoreError):
                raise
            raise StoreError("STORE_WRITE", "recoverable delete failed") from error

    def trash_generation(
        self,
        generation_id: str,
        *,
        records: dict[str, dict] | None = None,
    ) -> dict:
        names = self.preview_generation_delete(generation_id)
        _require(bool(names), "NOT_FOUND", "generation has no owned files")
        archived = self._recovery_records(records)
        identifier = _identifier(generation_id, "generation_id")
        batch_id = f"generation-{identifier}-{uuid.uuid4().hex}"
        batch = self.root / "trash" / batch_id
        _private_directory(batch)
        moved: list[str] = []
        try:
            for relative in names:
                source = self.root / relative
                target = batch / relative
                _private_directory(target.parent)
                os.replace(source, target)
                moved.append(relative)
            for logical, value in archived:
                destination = batch / "records" / Path(*PurePosixPath(logical).parts)
                _private_directory(destination.parent)
                self._atomic_record(destination, value)
            self._atomic_record(batch / "delete-receipt.json", {
                "schema": "kilix.voicebox.recoverable-delete/candidate-v1",
                "batch_id": batch_id,
                "kind": "generation",
                "object_id": identifier,
                "moved": list(names),
                "archived": [name for name, _value in archived],
                "recorded_at": _timestamp(),
            })
            _fsync_directory(self.generation_root)
            _fsync_directory(self.consent_root)
            _fsync_directory(batch)
            population = list(names) + [name for name, _value in archived]
            return {"batch_id": batch_id, "files": population, "count": len(population)}
        except (OSError, StoreError) as error:
            for relative in reversed(moved):
                try:
                    os.replace(batch / relative, self.root / relative)
                except OSError:
                    pass
            shutil.rmtree(batch, ignore_errors=True)
            if isinstance(error, StoreError):
                raise
            raise StoreError("TRASH_FAILED", "recoverable trash move failed") from error


def default_voice_store() -> PrivateVoiceStore:
    value = os.environ.get("XDG_DATA_HOME")
    root = Path(value) if value else Path.home() / ".local" / "share"
    return PrivateVoiceStore(root / "kilix-voicebox" / "privacy")

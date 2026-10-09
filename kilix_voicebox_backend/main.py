"""
FastAPI application for voicebox backend.

Handles voice cloning, generation history, and server mode.
"""

from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from typing import List, Optional
import asyncio
import uvicorn
import argparse
import tempfile
import io
import hmac
import os
import re
import stat
import threading
import time
from pathlib import Path

from . import database, models, profiles, history, tts, transcribe, config, export_import, channels, stories, __version__
from .database import get_db, Generation as DBGeneration, VoiceProfile as DBVoiceProfile
from .utils.cache import clear_voice_prompt_cache
from .kilix_provider import ProviderError
from .orchestrator import VoiceboxOrchestrator
from .managed_capture import CaptureBridge, CaptureRefusal, transcribe_owned, until_disconnected
from .private_store import (
    MAX_GENERATION_BYTES,
    MAX_SAMPLE_BYTES,
    PrivateVoiceStore,
    StoreError,
)

app = FastAPI(
    title="Kilix Voicebox API",
    description="Local Voicebox orchestration over independent Kilix speech providers",
    version=__version__,
)

MAX_PROFILE_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_AVATAR_BYTES = 10 * 1024 * 1024
SESSION_HEADER = "x-kilix-voicebox-session"
_SESSION_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_session_token: str | None = None
_installed_origin: str | None = None
_installed_ui: Path | None = None
_installed_assets: dict[str, bytes] = {}


def configure_installed_ui(directory: Path, port: int) -> None:
    """Select only verified public assets; credentials and product data stay private."""
    global _installed_origin, _installed_ui, _installed_assets
    if not directory.is_dir() or not 1 <= port <= 65535:
        raise ValueError("invalid installed UI")
    assets = {}
    total = 0
    for parent, dirs, names in os.walk(directory, followlinks=False):
        if any((Path(parent) / name).is_symlink() for name in dirs):
            raise ValueError('linked UI directory')
        for name in names:
            file = Path(parent) / name
            fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= 16 * 1024**2:
                    raise ValueError('invalid UI asset')
                with os.fdopen(os.dup(fd), 'rb') as source:
                    raw = source.read(info.st_size + 1)
                if len(raw) != info.st_size:
                    raise ValueError('UI asset changed')
                total += len(raw)
                if total > 64 * 1024**2:
                    raise ValueError('UI population exceeds bound')
                assets[file.relative_to(directory).as_posix()] = raw
            finally:
                os.close(fd)
    if 'index.html' not in assets:
        raise ValueError('UI entrypoint missing')
    _installed_assets = assets
    _installed_origin = f"http://127.0.0.1:{port}"
    _installed_ui = directory


@app.get('/ui/{asset:path}')
async def installed_ui(asset: str):
    import mimetypes
    if _installed_ui is None:
        raise HTTPException(404)
    parts = asset.split('/')
    if any(p in {'.', '..'} for p in parts) or '\\' in asset:
        raise HTTPException(404)
    name = asset
    if asset in {'', 'voices', 'stories', 'audio', 'models', 'server'}:
        name = 'index.html'
    if name not in _installed_assets:
        raise HTTPException(404)
    return Response(_installed_assets[name], media_type=mimetypes.guess_type(name)[0] or 'application/octet-stream', headers={
        'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    })


def configure_session_token(value: str) -> None:
    """Install one unguessable session token without persisting or logging it."""

    if type(value) is not str or _SESSION_TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError("Voicebox session token must be 32 random bytes in lowercase hex")
    global _session_token
    _session_token = value


_environment_token = os.environ.get("KILIX_VOICEBOX_SESSION_TOKEN")
if _environment_token is not None:
    configure_session_token(_environment_token)
del _environment_token


async def _read_bounded_upload(file: UploadFile, maximum: int) -> bytes:
    payload = bytearray()
    while True:
        chunk = await file.read(min(1024 * 1024, maximum + 1 - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > maximum:
            raise HTTPException(status_code=413, detail={"code": "INPUT_SIZE"})
    if not payload:
        raise HTTPException(status_code=400, detail={"code": "INPUT_EMPTY"})
    return bytes(payload)


def _stage_private_upload(payload: bytes, suffix: str) -> Path:
    with tempfile.NamedTemporaryFile(
        suffix=suffix,
        delete=False,
        dir=config.get_cache_dir(),
    ) as temporary:
        temporary.write(payload)
        temporary.flush()
        os.fsync(temporary.fileno())
        return Path(temporary.name)


def _owned_streaming_response(
    path: Path,
    *,
    maximum: int,
    media_type: str,
    filename: str | None = None,
) -> StreamingResponse:
    """Stream one same-user regular file through a no-follow descriptor."""

    descriptor = -1
    try:
        before = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or not 0 < before.st_size <= maximum
        ):
            raise OSError("unsafe product file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
        ):
            raise OSError("product file identity changed")
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        raise HTTPException(status_code=409, detail={"code": "DATA_INTEGRITY"}) from None

    def stream():
        try:
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            os.close(descriptor)

    headers = {"Content-Length": str(after.st_size)}
    if filename is not None:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return StreamingResponse(stream(), media_type=media_type, headers=headers)


ALLOWED_PRODUCT_ORIGINS = frozenset({
    "tauri://localhost",
    "http://tauri.localhost",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
})

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(ALLOWED_PRODUCT_ORIGINS),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Content-Type", "X-Kilix-Voicebox-Session"],
    expose_headers=["X-Kilix-Recording-Duration"],
)


def _media_credential(request: Request) -> str | None:
    path = request.url.path
    if request.method != "GET":
        return None
    if (
        path.startswith("/audio/")
        or path.startswith("/samples/")
        or (path.startswith("/profiles/") and path.endswith("/avatar"))
    ):
        return request.query_params.get("session")
    return None


@app.middleware("http")
async def enforce_session_credential(request: Request, call_next):
    """Require the per-process credential on every application request."""

    # The installed UI is a public immutable shell. It contains no credential,
    # data, or authenticated bootstrap endpoint. Every API/media request below
    # still requires the fresh launch secret.
    if _installed_ui is not None and request.method in {'GET', 'HEAD'} and request.url.path.startswith('/ui/'):
        return await call_next(request)
    if request.method == "OPTIONS" and request.headers.get("origin") in ALLOWED_PRODUCT_ORIGINS | {_installed_origin}:
        return await call_next(request)
    expected = _session_token
    provided = request.headers.get(SESSION_HEADER) or _media_credential(request)
    if expected is None:
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "SESSION_NOT_CONFIGURED"}},
        )
    if provided is None or not hmac.compare_digest(provided, expected):
        return JSONResponse(
            status_code=401,
            content={"detail": {"code": "SESSION_UNAUTHORIZED"}},
        )
    return await call_next(request)


@app.middleware("http")
async def enforce_product_origin(request: Request, call_next):
    """Reject browser origins outside the fixed desktop and development surfaces."""
    origin = request.headers.get("origin")
    if origin is not None and origin not in ALLOWED_PRODUCT_ORIGINS | {_installed_origin}:
        return JSONResponse(
            status_code=403,
            content={"detail": {"code": "ORIGIN_FORBIDDEN"}},
        )
    return await call_next(request)


# ============================================
# ROOT & HEALTH ENDPOINTS
# ============================================

@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Kilix Voicebox API", "version": __version__}


@app.get("/health", response_model=models.HealthResponse)
async def health():
    """Report provider reachability without importing or inspecting model runtimes."""
    qwen_result, transcribe_result = await asyncio.gather(
        tts.status(), transcribe.status(), return_exceptions=True
    )
    qwen_state = (
        qwen_result.get("provider_state", "ready")
        if isinstance(qwen_result, dict) else "unavailable"
    )
    transcribe_state = (
        transcribe_result.get("provider_state", "ready")
        if isinstance(transcribe_result, dict) else "unavailable"
    )
    return models.HealthResponse(
        status="healthy" if qwen_state != "unavailable" and transcribe_state != "unavailable"
        else "degraded",
        qwen_provider=qwen_state,
        transcribe_provider=transcribe_state,
    )


# ============================================
# VOICE PROFILE ENDPOINTS
# ============================================

@app.post("/profiles", response_model=models.VoiceProfileResponse)
async def create_profile(
    data: models.VoiceProfileCreate,
    db: Session = Depends(get_db),
):
    """Create a new voice profile."""
    try:
        return await profiles.create_profile(data, db)
    except Exception:
        raise HTTPException(status_code=400, detail={"code": "PROFILE_INVALID"}) from None


@app.get("/profiles", response_model=List[models.VoiceProfileResponse])
async def list_profiles(db: Session = Depends(get_db)):
    """List all voice profiles."""
    return await profiles.list_profiles(db)


@app.post("/profiles/import", response_model=models.VoiceProfileResponse)
async def import_profile(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Import a voice profile from a ZIP archive."""
    content = await _read_bounded_upload(file, MAX_PROFILE_ARCHIVE_BYTES)
    try:
        profile = await export_import.import_profile_from_zip(content, db)
        return profile
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "PROFILE_ARCHIVE_INVALID"}) from None
    except Exception:
        raise HTTPException(status_code=500, detail={"code": "VOICEBOX_INTERNAL"}) from None


@app.get("/profiles/{profile_id}", response_model=models.VoiceProfileResponse)
async def get_profile(
    profile_id: str,
    db: Session = Depends(get_db),
):
    """Get a voice profile by ID."""
    profile = await profiles.get_profile(profile_id, db)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@app.put("/profiles/{profile_id}", response_model=models.VoiceProfileResponse)
async def update_profile(
    profile_id: str,
    data: models.VoiceProfileCreate,
    db: Session = Depends(get_db),
):
    """Update a voice profile."""
    profile = await profiles.update_profile(profile_id, data, db)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@app.delete("/profiles/{profile_id}")
async def delete_profile(
    profile_id: str,
    db: Session = Depends(get_db),
):
    """Move a profile to recoverable trash after its preview is available."""
    try:
        receipt = await profiles.delete_profile(profile_id, db)
    except StoreError as error:
        raise HTTPException(status_code=409, detail={"code": error.code}) from None
    except ValueError:
        raise HTTPException(status_code=409, detail={"code": "PROFILE_IN_USE"}) from None
    if receipt is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"message": "Profile moved to recoverable trash", **receipt,
            "total": receipt["count"]}


@app.get("/profiles/{profile_id}/delete-preview")
async def preview_profile_delete(
    profile_id: str,
    db: Session = Depends(get_db),
):
    """Preview the exact profile population that delete moves to trash."""
    try:
        preview = await profiles.preview_profile_delete(profile_id, db)
    except StoreError as error:
        raise HTTPException(status_code=409, detail={"code": error.code}) from None
    except ValueError:
        raise HTTPException(status_code=409, detail={"code": "PROFILE_IN_USE"}) from None
    if preview is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {**preview, "total": preview["count"]}


@app.post("/profiles/{profile_id}/samples", response_model=models.ProfileSampleResponse)
async def add_profile_sample(
    profile_id: str,
    file: UploadFile = File(...),
    reference_text: str = Form(...),
    db: Session = Depends(get_db),
):
    """Add a sample to a voice profile."""
    content = await _read_bounded_upload(file, MAX_SAMPLE_BYTES)
    tmp_path = _stage_private_upload(content, ".wav")
    try:
        sample = await profiles.add_profile_sample(
            profile_id,
            str(tmp_path),
            reference_text,
            db,
        )
        return sample
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "SAMPLE_INVALID"}) from None
    finally:
        # Clean up temp file
        tmp_path.unlink(missing_ok=True)


@app.get("/profiles/{profile_id}/samples", response_model=List[models.ProfileSampleResponse])
async def get_profile_samples(
    profile_id: str,
    db: Session = Depends(get_db),
):
    """Get all samples for a profile."""
    return await profiles.get_profile_samples(profile_id, db)


@app.delete("/profiles/samples/{sample_id}")
async def delete_profile_sample(
    sample_id: str,
    db: Session = Depends(get_db),
):
    """Move a profile sample to recoverable trash."""
    try:
        receipt = await profiles.delete_profile_sample(sample_id, db)
    except StoreError as error:
        raise HTTPException(status_code=409, detail={"code": error.code}) from None
    if receipt is None:
        raise HTTPException(status_code=404, detail="Sample not found")
    return {"message": "Sample moved to recoverable trash", **receipt,
            "total": receipt["count"]}


@app.get("/profiles/samples/{sample_id}/delete-preview")
async def preview_profile_sample_delete(
    sample_id: str,
    db: Session = Depends(get_db),
):
    """Preview the exact sample population that delete moves to trash."""
    try:
        preview = await profiles.preview_profile_sample_delete(sample_id, db)
    except StoreError as error:
        raise HTTPException(status_code=409, detail={"code": error.code}) from None
    if preview is None:
        raise HTTPException(status_code=404, detail="Sample not found")
    return {**preview, "total": preview["count"]}


@app.put("/profiles/samples/{sample_id}", response_model=models.ProfileSampleResponse)
async def update_profile_sample(
    sample_id: str,
    data: models.ProfileSampleUpdate,
    db: Session = Depends(get_db),
):
    """Update a profile sample's reference text."""
    sample = await profiles.update_profile_sample(sample_id, data.reference_text, db)
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found")
    return sample


@app.post("/profiles/{profile_id}/avatar", response_model=models.VoiceProfileResponse)
async def upload_profile_avatar(
    profile_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload or update avatar image for a profile."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".jpeg", ".jpg", ".png", ".webp"}:
        raise HTTPException(status_code=400, detail={"code": "AVATAR_INVALID"})
    content = await _read_bounded_upload(file, MAX_AVATAR_BYTES)
    tmp_path = _stage_private_upload(content, suffix)

    try:
        profile = await profiles.upload_avatar(profile_id, str(tmp_path), db)
        return profile
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "AVATAR_INVALID"}) from None
    finally:
        # Clean up temp file
        tmp_path.unlink(missing_ok=True)


@app.get("/profiles/{profile_id}/avatar")
async def get_profile_avatar(
    profile_id: str,
    db: Session = Depends(get_db),
):
    """Get avatar image for a profile."""
    profile = await profiles.get_profile(profile_id, db)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if not profile.avatar_path:
        raise HTTPException(status_code=404, detail="No avatar found for this profile")

    try:
        avatar_path = profiles.owned_avatar_path(profile)
    except ValueError:
        raise HTTPException(status_code=409, detail={"code": "DATA_INTEGRITY"}) from None
    if avatar_path is None:
        raise HTTPException(status_code=404, detail="No avatar found for this profile")
    media_type = {
        ".jpeg": "image/jpeg",
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }[avatar_path.suffix.lower()]
    return _owned_streaming_response(
        avatar_path,
        maximum=MAX_AVATAR_BYTES,
        media_type=media_type,
    )


@app.delete("/profiles/{profile_id}/avatar")
async def delete_profile_avatar(
    profile_id: str,
    db: Session = Depends(get_db),
):
    """Move an avatar to recoverable trash."""
    try:
        receipt = await profiles.delete_avatar(profile_id, db)
    except StoreError as error:
        raise HTTPException(status_code=409, detail={"code": error.code}) from None
    if receipt is None:
        raise HTTPException(status_code=404, detail="Profile not found or no avatar to delete")
    return {"message": "Avatar moved to recoverable trash", **receipt,
            "total": receipt["count"]}


@app.get("/profiles/{profile_id}/avatar/delete-preview")
async def preview_profile_avatar_delete(
    profile_id: str,
    db: Session = Depends(get_db),
):
    """Preview the exact avatar population that delete moves to trash."""
    try:
        preview = await profiles.preview_avatar_delete(profile_id, db)
    except StoreError as error:
        raise HTTPException(status_code=409, detail={"code": error.code}) from None
    if preview is None:
        raise HTTPException(status_code=404, detail="Profile not found or no avatar to delete")
    return {**preview, "total": preview["count"]}


@app.get("/profiles/{profile_id}/export")
async def export_profile(
    profile_id: str,
    include_samples: bool = False,
    db: Session = Depends(get_db),
):
    """Export a voice profile as a ZIP archive."""
    try:
        # Get profile to get name for filename
        profile = await profiles.get_profile(profile_id, db)
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        
        # Export to ZIP
        zip_bytes = export_import.export_profile_to_zip(
            profile_id, db, include_samples=include_samples
        )
        
        # Create safe filename
        safe_name = "".join(c for c in profile.name if c.isalnum() or c in (' ', '-', '_')).strip()
        if not safe_name:
            safe_name = "profile"
        filename = f"profile-{safe_name}.voicebox.zip"
        
        # Return as streaming response
        return StreamingResponse(
            io.BytesIO(zip_bytes),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "PROFILE_EXPORT_INVALID"}) from None
    except Exception:
        raise HTTPException(status_code=500, detail={"code": "VOICEBOX_INTERNAL"}) from None


# ============================================
# AUDIO CHANNEL ENDPOINTS
# ============================================

@app.get("/channels", response_model=List[models.AudioChannelResponse])
async def list_channels(db: Session = Depends(get_db)):
    """List all audio channels."""
    return await channels.list_channels(db)


@app.post("/channels", response_model=models.AudioChannelResponse)
async def create_channel(
    data: models.AudioChannelCreate,
    db: Session = Depends(get_db),
):
    """Create a new audio channel."""
    try:
        return await channels.create_channel(data, db)
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "CHANNEL_INVALID"}) from None


@app.get("/channels/{channel_id}", response_model=models.AudioChannelResponse)
async def get_channel(
    channel_id: str,
    db: Session = Depends(get_db),
):
    """Get an audio channel by ID."""
    channel = await channels.get_channel(channel_id, db)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    return channel


@app.put("/channels/{channel_id}", response_model=models.AudioChannelResponse)
async def update_channel(
    channel_id: str,
    data: models.AudioChannelUpdate,
    db: Session = Depends(get_db),
):
    """Update an audio channel."""
    try:
        channel = await channels.update_channel(channel_id, data, db)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        return channel
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "CHANNEL_INVALID"}) from None


@app.delete("/channels/{channel_id}")
async def delete_channel(
    channel_id: str,
    db: Session = Depends(get_db),
):
    """Delete an audio channel."""
    try:
        success = await channels.delete_channel(channel_id, db)
        if not success:
            raise HTTPException(status_code=404, detail="Channel not found")
        return {"message": "Channel deleted successfully"}
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "CHANNEL_INVALID"}) from None


@app.get("/channels/{channel_id}/voices")
async def get_channel_voices(
    channel_id: str,
    db: Session = Depends(get_db),
):
    """Get list of profile IDs assigned to a channel."""
    try:
        profile_ids = await channels.get_channel_voices(channel_id, db)
        return {"profile_ids": profile_ids}
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "CHANNEL_INVALID"}) from None


@app.put("/channels/{channel_id}/voices")
async def set_channel_voices(
    channel_id: str,
    data: models.ChannelVoiceAssignment,
    db: Session = Depends(get_db),
):
    """Set which voices are assigned to a channel."""
    try:
        await channels.set_channel_voices(channel_id, data, db)
        return {"message": "Channel voices updated successfully"}
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "CHANNEL_INVALID"}) from None


@app.get("/profiles/{profile_id}/channels")
async def get_profile_channels(
    profile_id: str,
    db: Session = Depends(get_db),
):
    """Get list of channel IDs assigned to a profile."""
    try:
        channel_ids = await channels.get_profile_channels(profile_id, db)
        return {"channel_ids": channel_ids}
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "CHANNEL_INVALID"}) from None


@app.put("/profiles/{profile_id}/channels")
async def set_profile_channels(
    profile_id: str,
    data: models.ProfileChannelAssignment,
    db: Session = Depends(get_db),
):
    """Set which channels a profile is assigned to."""
    try:
        await channels.set_profile_channels(profile_id, data, db)
        return {"message": "Profile channels updated successfully"}
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "CHANNEL_INVALID"}) from None


# ============================================
# GENERATION ENDPOINTS
# ============================================


def _voice_orchestrator() -> VoiceboxOrchestrator:
    return VoiceboxOrchestrator(
        tts.get_tts_client(),
        transcribe.get_transcribe_client(),
        PrivateVoiceStore(config.get_privacy_dir()),
    )

@app.post("/generate", response_model=models.GenerationResponse)
async def generate_speech(
    data: models.GenerationRequest,
    db: Session = Depends(get_db),
    request: Request = None,
):
    """Generate speech from text using a voice profile."""
    gone = threading.Event()
    async def owned(function):
        if request is None:
            return await asyncio.to_thread(function)
        return await until_disconnected(request, function, gone,
                                        stopped_code='GENERATION_CANCELED')
    try:
        # Get profile
        profile = await profiles.get_profile(data.profile_id, db)
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        
        if data.mode == "prompt_clone":
            # Only cloning resolves a consented sample. Named/design modes
            # retain the profile as a history collection without reading it.
            prompt = await profiles.create_voice_prompt_for_profile(data.profile_id, db)
            generated = await owned(lambda: _voice_orchestrator().synthesize_clone(
                profile_id=data.profile_id,
                sample_path=Path(prompt["audio_path"]),
                text=data.text,
                language=data.language,
                consent_asserted=data.consent_asserted,
                instruction=data.instruct,
                seed=data.seed,
                cancelled=gone.is_set,
            ))
        else:
            generated = await owned(lambda: _voice_orchestrator().synthesize_generated(
                mode=data.mode, text=data.text, language=data.language,
                voice_id=data.voice_id, description=data.description,
                instruction=data.instruct, seed=data.seed,
                cancelled=gone.is_set,
            ))
        audio_path = config.get_privacy_dir() / "generations" / generated["stored_name"]
        duration = generated["duration_ms"] / 1000

        # Create history entry. If the relational commit fails, keep the
        # already-committed provider output recoverable instead of orphaning it.
        try:
            generation = await history.create_generation(
                profile_id=data.profile_id,
                text=data.text,
                language=data.language,
                audio_path=str(audio_path),
                duration=duration,
                seed=generated["seed"],
                db=db,
                instruct=data.instruct,
                generation_id=generated["generation_id"],
            )
        except Exception:
            db.rollback()
            PrivateVoiceStore(config.get_privacy_dir()).trash_generation(
                generated["generation_id"]
            )
            raise
        
        return generation
        
    except HTTPException:
        raise
    except StoreError as error:
        raise HTTPException(status_code=409 if error.code == 'CONSENT_REQUIRED' else 400,
                            detail={"code": error.code}) from None
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "GENERATION_INVALID"}) from None
    except (ProviderError, CaptureRefusal) as e:
        status_code = 409 if e.code in {"CONSENT_REQUIRED", "MODEL_MISSING", "RUNTIME_UNSELECTED"} else 503
        raise HTTPException(status_code=status_code, detail={"code": e.code}) from None
    except Exception:
        raise HTTPException(status_code=500, detail={"code": "VOICEBOX_INTERNAL"}) from None


# ============================================
# HISTORY ENDPOINTS
# ============================================

@app.get("/history", response_model=models.HistoryListResponse)
async def list_history(
    profile_id: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """List generation history with optional filters."""
    query = models.HistoryQuery(
        profile_id=profile_id,
        search=search,
        limit=limit,
        offset=offset,
    )
    return await history.list_generations(query, db)


@app.get("/history/stats")
async def get_stats(db: Session = Depends(get_db)):
    """Get generation statistics."""
    return await history.get_generation_stats(db)


@app.get("/history/{generation_id}", response_model=models.HistoryResponse)
async def get_generation(
    generation_id: str,
    db: Session = Depends(get_db),
):
    """Get a generation by ID."""
    # Get generation with profile name
    result = db.query(
        DBGeneration,
        DBVoiceProfile.name.label('profile_name')
    ).join(
        DBVoiceProfile,
        DBGeneration.profile_id == DBVoiceProfile.id
    ).filter(
        DBGeneration.id == generation_id
    ).first()
    
    if not result:
        raise HTTPException(status_code=404, detail="Generation not found")
    
    gen, profile_name = result
    return models.HistoryResponse(
        id=gen.id,
        profile_id=gen.profile_id,
        profile_name=profile_name,
        text=gen.text,
        language=gen.language,
        audio_path=gen.audio_path,
        duration=gen.duration,
        seed=gen.seed,
        instruct=gen.instruct,
        created_at=gen.created_at,
    )


@app.delete("/history/{generation_id}")
async def delete_generation(
    generation_id: str,
    db: Session = Depends(get_db),
):
    """Move a generation's exact product files to recoverable trash."""
    generation = db.query(DBGeneration).filter_by(id=generation_id).first()
    if not generation:
        raise HTTPException(status_code=404, detail="Generation not found")
    if history.generation_is_in_story(generation_id, db):
        raise HTTPException(status_code=409, detail={"code": "GENERATION_IN_USE"})
    store = PrivateVoiceStore(config.get_privacy_dir())
    records = history.generation_recovery_records(generation)
    preview = (
        store.preview_generation_delete(generation_id)
        + store.preview_record_archive(records)
    )
    if not preview:
        raise HTTPException(status_code=409, detail={"code": "NO_RECOVERABLE_DELETE"})
    try:
        receipt = store.trash_generation(generation_id, records=records)
    except StoreError as error:
        raise HTTPException(status_code=409, detail={"code": error.code}) from None
    success = await history.delete_generation(generation_id, db)
    if not success:
        raise HTTPException(status_code=404, detail="Generation not found")
    return {
        "message": "Generation moved to recoverable trash",
        **receipt,
        "total": receipt["count"],
    }


@app.get("/history/{generation_id}/delete-preview")
async def preview_generation_delete(
    generation_id: str,
    db: Session = Depends(get_db),
):
    """Preview the exact product-owned files that delete will move to trash."""
    generation = db.query(DBGeneration).filter_by(id=generation_id).first()
    if not generation:
        raise HTTPException(status_code=404, detail="Generation not found")
    if history.generation_is_in_story(generation_id, db):
        raise HTTPException(status_code=409, detail={"code": "GENERATION_IN_USE"})
    store = PrivateVoiceStore(config.get_privacy_dir())
    records = history.generation_recovery_records(generation)
    names = (
        store.preview_generation_delete(generation_id)
        + store.preview_record_archive(records)
    )
    return {
        "generation_id": generation_id,
        "files": list(names),
        "count": len(names),
        "total": len(names),
    }


@app.get("/history/{generation_id}/export")
async def export_generation(
    generation_id: str,
    db: Session = Depends(get_db),
):
    """Export a generation as a ZIP archive."""
    try:
        # Get generation to create filename
        generation = db.query(DBGeneration).filter_by(id=generation_id).first()
        if not generation:
            raise HTTPException(status_code=404, detail="Generation not found")
        
        # Export to ZIP
        zip_bytes = export_import.export_generation_to_zip(generation_id, db)
        
        # Create safe filename from text
        safe_text = "".join(c for c in generation.text[:30] if c.isalnum() or c in (' ', '-', '_')).strip()
        if not safe_text:
            safe_text = "generation"
        filename = f"generation-{safe_text}.voicebox.zip"
        
        # Return as streaming response
        return StreamingResponse(
            io.BytesIO(zip_bytes),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "GENERATION_EXPORT_INVALID"}) from None
    except Exception:
        raise HTTPException(status_code=500, detail={"code": "VOICEBOX_INTERNAL"}) from None


@app.get("/history/{generation_id}/export-audio")
async def export_generation_audio(
    generation_id: str,
    db: Session = Depends(get_db),
):
    """Export only the audio file from a generation."""
    generation = db.query(DBGeneration).filter_by(id=generation_id).first()
    if not generation:
        raise HTTPException(status_code=404, detail="Generation not found")
    
    try:
        audio_path = history.owned_generation_path(generation)
    except ValueError:
        raise HTTPException(status_code=409, detail={"code": "DATA_INTEGRITY"}) from None
    # Create safe filename from text
    safe_text = "".join(c for c in generation.text[:30] if c.isalnum() or c in (' ', '-', '_')).strip()
    if not safe_text:
        safe_text = "generation"
    suffix = audio_path.suffix.lower()
    filename = f"{safe_text}{suffix}"
    media_type = {
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".opus": "audio/ogg",
    }[suffix]

    return _owned_streaming_response(
        audio_path,
        maximum=MAX_GENERATION_BYTES,
        media_type=media_type,
        filename=filename,
    )


# ============================================
# TRANSCRIPTION ENDPOINTS
# ============================================

_capture_bridge = CaptureBridge()


class RecordingInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: str = Field(pattern=r"^[0-9a-f]{32}$", min_length=32, max_length=32)
    seconds: int = Field(ge=1, le=30)


@app.get("/recording/status")
async def recording_status():
    return JSONResponse(_capture_bridge.status(), headers={"Cache-Control": "no-store"})


@app.post("/recording/{identifier}/stop")
async def recording_stop(identifier: str):
    try:
        return _capture_bridge.control(identifier, cancel=False)
    except CaptureRefusal as error:
        raise HTTPException(409, detail={"code": error.code}) from None


@app.post("/recording/{identifier}/cancel")
async def recording_cancel(identifier: str):
    try:
        return _capture_bridge.control(identifier, cancel=True)
    except CaptureRefusal as error:
        raise HTTPException(409, detail={"code": error.code}) from None


@app.post("/recording")
async def recording_start(data: RecordingInput, request: Request):
    gone = threading.Event()
    bridge = _capture_bridge
    item = None
    response = None
    try:
        try:
            item = bridge.claim(data.id, data.seconds, gone)
            payload = await until_disconnected(request, lambda: bridge.record(
                item, defer_delivery=True), gone, pending_cancelled=lambda: (
                    item.cancel.is_set() or item.stop.is_set() or time.monotonic() >= item.deadline),
                stopped_code="RECORDING_REFUSED")
            if await request.is_disconnected():
                gone.set()
                raise CaptureRefusal("RECORDING_STOPPED")
            response = Response(payload, media_type="audio/wav", headers={
                "Cache-Control": "no-store", "X-Kilix-Recording-Duration": str(item.duration)})
        finally:
            if item is not None:
                # This is the final handler decision, after worker/waiter
                # cleanup and response construction. Cancel remains actionable
                # until this same-control acceptance/refusal releases the ID.
                bridge.finalize(item, deliver=response is not None)
        return response
    except CaptureRefusal as error:
        raise HTTPException(409, detail={"code": error.code}) from None
    except Exception:
        raise HTTPException(503, detail={"code": "RECORDING_FAILED"}) from None


@app.post("/transcribe", response_model=models.TranscriptionResponse)
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
):
    """Transcribe audio file to text."""
    # The original operation budget covers staging, client work and delivery.
    deadline = time.monotonic() + 300
    gone = threading.Event()
    maximum = 2_147_483_648
    tmp_path = None
    try:
        total = 0
        with tempfile.NamedTemporaryFile(
            suffix=".wav", delete=False, dir=config.get_cache_dir()
        ) as tmp:
            tmp_path = Path(tmp.name)
            while True:
                if time.monotonic() >= deadline or await request.is_disconnected():
                    raise CaptureRefusal("TRANSCRIPTION_STOPPED")
                chunk = await file.read(min(1024 * 1024, maximum + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise HTTPException(status_code=413, detail={"code": "INPUT_SIZE"})
                tmp.write(chunk)
        result = await until_disconnected(request, lambda: transcribe_owned(
            tmp_path, language, gone, deadline=deadline), gone)
        if time.monotonic() >= deadline or await request.is_disconnected():
            raise CaptureRefusal("TRANSCRIPTION_STOPPED")
        text = result.get("text")
        duration_ms = result.get("duration_ms", 0)
        if type(text) is not str or type(duration_ms) is not int or duration_ms < 0:
            raise ProviderError("INVALID_RESPONSE")
        return models.TranscriptionResponse(
            text=text,
            duration=duration_ms / 1000,
        )
    except (ProviderError, CaptureRefusal) as error:
        status_code = 409 if error.code in {"MODEL_MISSING", "RUNTIME_UNSELECTED"} else 503
        raise HTTPException(status_code=status_code, detail={"code": error.code}) from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail={"code": "VOICEBOX_INTERNAL"}) from None
    finally:
        # Clean up temp file
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


# ============================================
# STORY ENDPOINTS
# ============================================

@app.get("/stories", response_model=List[models.StoryResponse])
async def list_stories(db: Session = Depends(get_db)):
    """List all stories."""
    return await stories.list_stories(db)


@app.post("/stories", response_model=models.StoryResponse)
async def create_story(
    data: models.StoryCreate,
    db: Session = Depends(get_db),
):
    """Create a new story."""
    try:
        return await stories.create_story(data, db)
    except Exception:
        raise HTTPException(status_code=400, detail={"code": "STORY_INVALID"}) from None


@app.get("/stories/{story_id}", response_model=models.StoryDetailResponse)
async def get_story(
    story_id: str,
    db: Session = Depends(get_db),
):
    """Get a story with all its items."""
    try:
        story = await stories.get_story(story_id, db)
    except ValueError:
        raise HTTPException(
            status_code=409,
            detail={"code": "STORY_INTEGRITY"},
        ) from None
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")
    return story


@app.put("/stories/{story_id}", response_model=models.StoryResponse)
async def update_story(
    story_id: str,
    data: models.StoryCreate,
    db: Session = Depends(get_db),
):
    """Update a story."""
    story = await stories.update_story(story_id, data, db)
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")
    return story


@app.delete("/stories/{story_id}")
async def delete_story(
    story_id: str,
    db: Session = Depends(get_db),
):
    """Archive a story and its items in private recoverable trash."""
    try:
        receipt = await stories.delete_story(story_id, db)
    except StoreError as error:
        raise HTTPException(status_code=409, detail={"code": error.code}) from None
    except ValueError:
        raise HTTPException(status_code=409, detail={"code": "STORY_RECOVERY_LIMIT"}) from None
    if receipt is None:
        raise HTTPException(status_code=404, detail="Story not found")
    return {
        "message": "Story moved to recoverable trash",
        **receipt,
        "total": receipt["count"],
    }


@app.get("/stories/{story_id}/delete-preview")
async def preview_story_delete(
    story_id: str,
    db: Session = Depends(get_db),
):
    """Preview the exact story records archived by deletion."""
    try:
        preview = await stories.preview_story_delete(story_id, db)
    except StoreError as error:
        raise HTTPException(status_code=409, detail={"code": error.code}) from None
    except ValueError:
        raise HTTPException(status_code=409, detail={"code": "STORY_RECOVERY_LIMIT"}) from None
    if preview is None:
        raise HTTPException(status_code=404, detail="Story not found")
    return {**preview, "total": preview["count"]}


@app.post("/stories/{story_id}/items", response_model=models.StoryItemDetail)
async def add_story_item(
    story_id: str,
    data: models.StoryItemCreate,
    db: Session = Depends(get_db),
):
    """Add a generation to a story."""
    item = await stories.add_item_to_story(story_id, data, db)
    if not item:
        raise HTTPException(status_code=404, detail="Story or generation not found")
    return item


@app.delete("/stories/{story_id}/items/{item_id}")
async def remove_story_item(
    story_id: str,
    item_id: str,
    db: Session = Depends(get_db),
):
    """Remove a story item from a story."""
    success = await stories.remove_item_from_story(story_id, item_id, db)
    if not success:
        raise HTTPException(status_code=404, detail="Story item not found")
    return {"message": "Item removed successfully"}


@app.put("/stories/{story_id}/items/times")
async def update_story_item_times(
    story_id: str,
    data: models.StoryItemBatchUpdate,
    db: Session = Depends(get_db),
):
    """Update story item timecodes."""
    success = await stories.update_story_item_times(story_id, data, db)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid timecode update request")
    return {"message": "Item timecodes updated successfully"}


@app.put("/stories/{story_id}/items/reorder", response_model=List[models.StoryItemDetail])
async def reorder_story_items(
    story_id: str,
    data: models.StoryItemReorder,
    db: Session = Depends(get_db),
):
    """Reorder story items and recalculate timecodes."""
    items = await stories.reorder_story_items(story_id, data.item_ids, db)
    if items is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid reorder request - ensure all item IDs belong to this story",
        )
    return items


@app.put("/stories/{story_id}/items/{item_id}/move", response_model=models.StoryItemDetail)
async def move_story_item(
    story_id: str,
    item_id: str,
    data: models.StoryItemMove,
    db: Session = Depends(get_db),
):
    """Move a story item (update position and/or track)."""
    item = await stories.move_story_item(story_id, item_id, data, db)
    if item is None:
        raise HTTPException(status_code=404, detail="Story item not found")
    return item


@app.put("/stories/{story_id}/items/{item_id}/trim", response_model=models.StoryItemDetail)
async def trim_story_item(
    story_id: str,
    item_id: str,
    data: models.StoryItemTrim,
    db: Session = Depends(get_db),
):
    """Trim a story item (update trim_start_ms and trim_end_ms)."""
    item = await stories.trim_story_item(story_id, item_id, data, db)
    if item is None:
        raise HTTPException(status_code=404, detail="Story item not found or invalid trim values")
    return item


@app.post("/stories/{story_id}/items/{item_id}/split", response_model=List[models.StoryItemDetail])
async def split_story_item(
    story_id: str,
    item_id: str,
    data: models.StoryItemSplit,
    db: Session = Depends(get_db),
):
    """Split a story item at a given time, creating two clips."""
    items = await stories.split_story_item(story_id, item_id, data, db)
    if items is None:
        raise HTTPException(status_code=404, detail="Story item not found or invalid split point")
    return items


@app.post("/stories/{story_id}/items/{item_id}/duplicate", response_model=models.StoryItemDetail)
async def duplicate_story_item(
    story_id: str,
    item_id: str,
    db: Session = Depends(get_db),
):
    """Duplicate a story item, creating a copy with all properties."""
    item = await stories.duplicate_story_item(story_id, item_id, db)
    if item is None:
        raise HTTPException(status_code=404, detail="Story item not found")
    return item


@app.get("/stories/{story_id}/export-audio")
async def export_story_audio(
    story_id: str,
    db: Session = Depends(get_db),
):
    """Export story as single mixed audio file with timecode-based mixing."""
    try:
        # Get story to create filename
        story = db.query(database.Story).filter_by(id=story_id).first()
        if not story:
            raise HTTPException(status_code=404, detail="Story not found")
        
        # Export audio
        audio_bytes = await stories.export_story_audio(story_id, db)
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="Story has no audio items")
        
        # Create safe filename
        safe_name = "".join(c for c in story.name if c.isalnum() or c in (' ', '-', '_')).strip()
        if not safe_name:
            safe_name = "story"
        filename = f"{safe_name}.wav"
        
        # Return as streaming response
        return StreamingResponse(
            io.BytesIO(audio_bytes),
            media_type="audio/wav",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail={"code": "VOICEBOX_INTERNAL"}) from None


# ============================================
# FILE SERVING
# ============================================

@app.get("/audio/{generation_id}")
async def get_audio(generation_id: str, db: Session = Depends(get_db)):
    """Serve generated audio file."""
    generation = await history.get_generation(generation_id, db)
    if not generation:
        raise HTTPException(status_code=404, detail="Generation not found")
    
    try:
        audio_path = history.owned_generation_path(generation)
    except ValueError:
        raise HTTPException(status_code=409, detail={"code": "DATA_INTEGRITY"}) from None
    media_type = {
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".opus": "audio/ogg",
    }[audio_path.suffix.lower()]
    return _owned_streaming_response(
        audio_path,
        maximum=MAX_GENERATION_BYTES,
        media_type=media_type,
        filename=f"generation_{generation_id}{audio_path.suffix.lower()}",
    )


@app.get("/samples/{sample_id}")
async def get_sample_audio(sample_id: str, db: Session = Depends(get_db)):
    """Serve profile sample audio file."""
    from .database import ProfileSample as DBProfileSample
    
    sample = db.query(DBProfileSample).filter_by(id=sample_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found")
    
    try:
        audio_path = profiles.owned_sample_path(sample)
    except ValueError:
        raise HTTPException(status_code=409, detail={"code": "DATA_INTEGRITY"}) from None
    return _owned_streaming_response(
        audio_path,
        maximum=MAX_SAMPLE_BYTES,
        media_type="audio/wav",
        filename=f"sample_{sample_id}.wav",
    )


# ============================================
# MODEL MANAGEMENT
# ============================================

@app.post("/models/unload")
async def unload_model():
    """Ask both providers to release residency without touching model payloads."""
    results = await asyncio.gather(
        tts.unload_tts_model(),
        transcribe.unload_transcribe_model(),
        return_exceptions=True,
    )
    failures = sum(isinstance(result, Exception) for result in results)
    if failures:
        raise HTTPException(
            status_code=503,
            detail={"code": "PROVIDER_UNAVAILABLE", "failed": failures, "total": 2},
        )
    return {"message": "2/2 providers acknowledged unload"}


@app.get("/models/status", response_model=models.ModelStatusListResponse)
async def get_model_status():
    """Render provider-reported installed models; never inspect mutable caches."""
    populations = await asyncio.gather(tts.models(), transcribe.models(), return_exceptions=True)
    statuses = []
    catalog = {}
    if _installed_ui is not None:
        import json
        data = json.loads((_installed_ui.parent / 'content-catalog.json').read_text())
        catalog = {row['id']: row for row in data['assets'] if row.get('provider') in {'kilix-qwen-tts', 'kilix-transcribe'}}
    for provider, population in zip(("kilix-qwen-tts", "kilix-transcribe"), populations):
        if isinstance(population, Exception):
            continue
        for model in population:
            model_id = model.get("id")
            display_name = model.get("display_name", model_id)
            if type(model_id) is not str or type(display_name) is not str:
                continue
            selected = catalog.get(model_id, {})
            sizes = selected.get('sizes', {})
            license_rows = selected.get('licenses', [])
            statuses.append(models.ModelStatus(
                provider=provider,
                model_id=model_id,
                display_name=display_name,
                installed=bool(model.get("installed", False)),
                installed_bytes=(
                    model.get("installed_bytes")
                    if type(model.get("installed_bytes")) is int else None
                ),
                resident=bool(model.get("loaded", False)),
                model_revision=model.get('revision') if type(model.get('revision')) is str else None,
                asset_authority=model.get('asset_authority') if type(model.get('asset_authority')) is str else None,
                capabilities=[v for v in model.get('capabilities', []) if type(v) is str],
                release_qualified=model.get('release_qualified') is True,
                license_id=license_rows[0]['id'] if license_rows else None,
                download_bytes=sizes.get('download_bytes'), temporary_bytes=sizes.get('temporary_bytes'),
            ))
    reported = {row.model_id for row in statuses}
    for model_id, selected in catalog.items():
        if model_id not in reported:
            sizes = selected['sizes']
            statuses.append(models.ModelStatus(provider=selected['provider'], model_id=model_id,
                display_name=selected['label'], installed=False, model_revision=selected['version'],
                installed_bytes=sizes['installed_bytes'], download_bytes=sizes['download_bytes'],
                temporary_bytes=sizes['temporary_bytes'], license_id=selected['licenses'][0]['id']))
    return models.ModelStatusListResponse(models=statuses)


@app.post("/cache/clear")
async def clear_cache():
    """Clear all voice prompt caches (memory and disk)."""
    try:
        deleted_count = clear_voice_prompt_cache()
        return {
            "message": "Voice prompt cache cleared successfully",
            "files_deleted": deleted_count,
        }
    except Exception:
        raise HTTPException(status_code=500, detail={"code": "VOICEBOX_INTERNAL"}) from None


# ============================================
# STARTUP & SHUTDOWN
# ============================================

@app.on_event("startup")
async def startup_event():
    """Initialize private product storage; providers remain independently supervised."""
    config.get_data_dir()
    config.get_privacy_dir()
    database.init_db()


@app.on_event("shutdown")
async def shutdown_event():
    """Release this client's provider residency requests without deleting payloads."""
    _capture_bridge.shutdown()
    await asyncio.gather(
        tts.unload_tts_model(),
        transcribe.unload_transcribe_model(),
        return_exceptions=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kilix Voicebox local backend")
    parser.add_argument("--port", type=int, default=17493)
    parser.add_argument("--data-dir", type=str, default=None)
    args = parser.parse_args()
    if not 1 <= args.port <= 65_535:
        parser.error("--port must be within 1..65535")
    if args.data_dir:
        config.set_data_dir(args.data_dir)
    token = os.environ.get("KILIX_VOICEBOX_SESSION_TOKEN")
    if token is None:
        parser.error("KILIX_VOICEBOX_SESSION_TOKEN is required")
    configure_session_token(token)
    uvicorn.run(
        "kilix_voicebox_backend.main:app",
        host="127.0.0.1",
        port=args.port,
        reload=False,
        access_log=False,
    )

"""
Voice profile management module.
"""

from typing import List, Optional
from datetime import datetime
import uuid
import os
import re
from pathlib import Path
from sqlalchemy.orm import Session

from .models import (
    VoiceProfileCreate,
    VoiceProfileResponse,
    ProfileSampleResponse,
)
from .database import (
    VoiceProfile as DBVoiceProfile,
    ProfileSample as DBProfileSample,
    Generation as DBGeneration,
    ProfileChannelMapping,
)
from .utils.audio import validate_reference_audio, load_audio, save_audio
from .utils.images import validate_image, process_avatar
from .utils.cache import clear_profile_cache
from . import config
from .private_store import PrivateVoiceStore


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_identifier(value: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("unsafe product identifier")
    return value


def owned_sample_path(sample: DBProfileSample) -> Path:
    """Resolve a sample only when its database path matches its exact XDG slot."""

    expected = (
        _get_profiles_dir()
        / _safe_identifier(sample.profile_id)
        / f"{_safe_identifier(sample.id)}.wav"
    )
    if Path(sample.audio_path) != expected:
        raise ValueError("sample path is outside its owned slot")
    return expected


def owned_avatar_path(profile: DBVoiceProfile) -> Path | None:
    """Resolve an avatar only when it is an admitted file in its exact profile slot."""

    if not profile.avatar_path:
        return None
    candidate = Path(profile.avatar_path)
    expected_parent = _get_profiles_dir() / _safe_identifier(profile.id)
    if candidate.parent != expected_parent or candidate.name not in {
        "avatar.jpeg", "avatar.jpg", "avatar.png", "avatar.webp"
    }:
        raise ValueError("avatar path is outside its owned slot")
    return candidate


def _get_profiles_dir() -> Path:
    """Get profiles directory from config."""
    return config.get_profiles_dir()


def _profile_record(profile: DBVoiceProfile) -> dict:
    return {
        "schema": "kilix.voicebox.profile-recovery/candidate-v1",
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "language": profile.language,
        "avatar_name": Path(profile.avatar_path).name if profile.avatar_path else None,
        "created_at": profile.created_at.isoformat(),
        "updated_at": profile.updated_at.isoformat(),
    }


def _sample_record(sample: DBProfileSample) -> dict:
    return {
        "schema": "kilix.voicebox.profile-sample-recovery/candidate-v1",
        "id": sample.id,
        "profile_id": sample.profile_id,
        "audio_name": owned_sample_path(sample).name,
        "reference_text": sample.reference_text,
    }


def _profile_delete_records(
    profile: DBVoiceProfile,
    samples: list[DBProfileSample],
    mappings: list[ProfileChannelMapping],
) -> dict[str, dict]:
    records = {f"database/profiles/{profile.id}.json": _profile_record(profile)}
    records.update(
        (f"database/profile-samples/{sample.id}.json", _sample_record(sample))
        for sample in samples
    )
    records.update(
        (
            f"database/profile-channels/{mapping.profile_id}-{mapping.channel_id}.json",
            {
                "schema": "kilix.voicebox.profile-channel-recovery/candidate-v1",
                "profile_id": mapping.profile_id,
                "channel_id": mapping.channel_id,
            },
        )
        for mapping in mappings
    )
    return records


async def create_profile(
    data: VoiceProfileCreate,
    db: Session,
) -> VoiceProfileResponse:
    """
    Create a new voice profile.
    
    Args:
        data: Profile creation data
        db: Database session
        
    Returns:
        Created profile
    """
    # Create profile in database
    db_profile = DBVoiceProfile(
        id=str(uuid.uuid4()),
        name=data.name,
        description=data.description,
        language=data.language,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    
    db.add(db_profile)
    db.commit()
    db.refresh(db_profile)
    
    # Create profile directory
    profile_dir = _get_profiles_dir() / db_profile.id
    profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(profile_dir, 0o700, follow_symlinks=False)
    
    return VoiceProfileResponse.model_validate(db_profile)


async def add_profile_sample(
    profile_id: str,
    audio_path: str,
    reference_text: str,
    db: Session,
) -> ProfileSampleResponse:
    """
    Add a sample to a voice profile.
    
    Args:
        profile_id: Profile ID
        audio_path: Path to temporary audio file
        reference_text: Transcript of audio
        db: Database session
        
    Returns:
        Created sample
    """
    # Validate profile exists
    profile = db.query(DBVoiceProfile).filter_by(id=profile_id).first()
    if not profile:
        raise ValueError(f"Profile {profile_id} not found")
    
    # Validate audio
    is_valid, error_msg = validate_reference_audio(audio_path)
    if not is_valid:
        raise ValueError(f"Invalid reference audio: {error_msg}")
    
    # Create sample ID and directory
    sample_id = str(uuid.uuid4())
    profile_dir = _get_profiles_dir() / _safe_identifier(profile_id)
    profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(profile_dir, 0o700, follow_symlinks=False)
    
    # Copy audio file to profile directory
    dest_path = profile_dir / f"{sample_id}.wav"
    audio, sr = load_audio(audio_path, max_duration_seconds=30.0)
    save_audio(audio, str(dest_path), sr)
    os.chmod(dest_path, 0o600, follow_symlinks=False)
    
    # Create database entry
    db_sample = DBProfileSample(
        id=sample_id,
        profile_id=profile_id,
        audio_path=str(dest_path),
        reference_text=reference_text,
    )
    
    db.add(db_sample)
    
    # Update profile timestamp
    profile.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(db_sample)
    
    # Invalidate combined audio cache for this profile
    # Since a new sample was added, any cached combined audio is now stale
    clear_profile_cache(profile_id)
    
    return ProfileSampleResponse.model_validate(db_sample)


async def get_profile(
    profile_id: str,
    db: Session,
) -> Optional[VoiceProfileResponse]:
    """
    Get a voice profile by ID.
    
    Args:
        profile_id: Profile ID
        db: Database session
        
    Returns:
        Profile or None if not found
    """
    profile = db.query(DBVoiceProfile).filter_by(id=profile_id).first()
    if not profile:
        return None
    
    return VoiceProfileResponse.model_validate(profile)


async def get_profile_samples(
    profile_id: str,
    db: Session,
) -> List[ProfileSampleResponse]:
    """
    Get all samples for a profile.
    
    Args:
        profile_id: Profile ID
        db: Database session
        
    Returns:
        List of samples
    """
    samples = db.query(DBProfileSample).filter_by(profile_id=profile_id).all()
    return [ProfileSampleResponse.model_validate(s) for s in samples]


async def list_profiles(db: Session) -> List[VoiceProfileResponse]:
    """
    List all voice profiles.
    
    Args:
        db: Database session
        
    Returns:
        List of profiles
    """
    profiles = db.query(DBVoiceProfile).order_by(
        DBVoiceProfile.created_at.desc()
    ).all()
    
    return [VoiceProfileResponse.model_validate(p) for p in profiles]


async def update_profile(
    profile_id: str,
    data: VoiceProfileCreate,
    db: Session,
) -> Optional[VoiceProfileResponse]:
    """
    Update a voice profile.
    
    Args:
        profile_id: Profile ID
        data: Updated profile data
        db: Database session
        
    Returns:
        Updated profile or None if not found
    """
    profile = db.query(DBVoiceProfile).filter_by(id=profile_id).first()
    if not profile:
        return None
    
    # Update fields
    profile.name = data.name
    profile.description = data.description
    profile.language = data.language
    profile.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(profile)
    
    return VoiceProfileResponse.model_validate(profile)


async def preview_profile_delete(
    profile_id: str,
    db: Session,
) -> Optional[dict]:
    """Preview a profile deletion, refusing profiles referenced by generations."""

    profile = db.query(DBVoiceProfile).filter_by(id=profile_id).first()
    if not profile:
        return None
    if db.query(DBGeneration).filter_by(profile_id=profile_id).first() is not None:
        raise ValueError("PROFILE_IN_USE")
    samples = db.query(DBProfileSample).filter_by(profile_id=profile_id).all()
    mappings = db.query(ProfileChannelMapping).filter_by(profile_id=profile_id).all()
    records = _profile_delete_records(profile, samples, mappings)
    profile_dir = _get_profiles_dir() / _safe_identifier(profile_id)
    files = PrivateVoiceStore(config.get_privacy_dir()).preview_owned_delete(
        (profile_dir,), records=records
    )
    return {"files": list(files), "count": len(files)}


async def delete_profile(profile_id: str, db: Session) -> Optional[dict]:
    """Move a profile tree to recoverable trash, then remove its database index."""

    profile = db.query(DBVoiceProfile).filter_by(id=profile_id).first()
    if not profile:
        return None
    preview = await preview_profile_delete(profile_id, db)
    if preview is None:
        return None
    samples = db.query(DBProfileSample).filter_by(profile_id=profile_id).all()
    mappings = db.query(ProfileChannelMapping).filter_by(profile_id=profile_id).all()
    records = _profile_delete_records(profile, samples, mappings)
    receipt = PrivateVoiceStore(config.get_privacy_dir()).trash_owned_paths(
        kind="profile",
        object_id=profile_id,
        paths=(_get_profiles_dir() / _safe_identifier(profile_id),),
        metadata={
            "description": profile.description,
            "language": profile.language,
            "name": profile.name,
            "sample_count": len(samples),
            "channel_count": len(mappings),
        },
        records=records,
    )
    db.query(DBProfileSample).filter_by(profile_id=profile_id).delete()
    db.query(ProfileChannelMapping).filter_by(profile_id=profile_id).delete()
    db.delete(profile)
    db.commit()
    clear_profile_cache(profile_id)
    return receipt


async def preview_profile_sample_delete(
    sample_id: str,
    db: Session,
) -> Optional[dict]:
    """Preview the exact sample file moved by deletion."""

    sample = db.query(DBProfileSample).filter_by(id=sample_id).first()
    if not sample:
        return None
    records = {f"database/profile-samples/{sample.id}.json": _sample_record(sample)}
    files = PrivateVoiceStore(config.get_privacy_dir()).preview_owned_delete(
        (owned_sample_path(sample),), records=records
    )
    return {"files": list(files), "count": len(files)}


async def delete_profile_sample(sample_id: str, db: Session) -> Optional[dict]:
    """Move a sample to recoverable trash, then remove its database index."""

    sample = db.query(DBProfileSample).filter_by(id=sample_id).first()
    if not sample:
        return None
    profile_id = sample.profile_id
    records = {f"database/profile-samples/{sample.id}.json": _sample_record(sample)}
    receipt = PrivateVoiceStore(config.get_privacy_dir()).trash_owned_paths(
        kind="sample",
        object_id=sample_id,
        paths=(owned_sample_path(sample),),
        metadata={"profile_id": profile_id, "reference_text": sample.reference_text},
        records=records,
    )
    db.delete(sample)
    db.commit()
    clear_profile_cache(profile_id)
    return receipt


async def update_profile_sample(
    sample_id: str,
    reference_text: str,
    db: Session,
) -> Optional[ProfileSampleResponse]:
    """
    Update a profile sample's reference text.
    
    Args:
        sample_id: Sample ID
        reference_text: Updated reference text
        db: Database session
        
    Returns:
        Updated sample or None if not found
    """
    sample = db.query(DBProfileSample).filter_by(id=sample_id).first()
    if not sample:
        return None
    
    # Store profile_id before updating
    profile_id = sample.profile_id
    
    sample.reference_text = reference_text
    db.commit()
    db.refresh(sample)
    
    # Invalidate combined audio cache for this profile
    # Since the reference text changed, cache keys and combined text are now stale
    clear_profile_cache(profile_id)
    
    return ProfileSampleResponse.model_validate(sample)


async def create_voice_prompt_for_profile(
    profile_id: str,
    db: Session,
    use_cache: bool = True,
) -> dict:
    """
    Return provider-input metadata for one consentable source sample.

    Args:
        profile_id: Profile ID
        db: Database session
        use_cache: Retained compatibility argument; provider caches are not
            owned or inspected by Voicebox.

    Returns:
        A product-side descriptor. It contains no model tensors or pickle.
    """
    # Get all samples for profile
    samples = db.query(DBProfileSample).filter_by(profile_id=profile_id).all()

    if not samples:
        raise ValueError(f"No samples found for profile {profile_id}")

    if len(samples) != 1:
        raise ValueError(
            "Exactly one source sample is required until the provider protocol "
            "defines a bounded multi-sample descriptor population"
        )
    sample = samples[0]
    audio_path = owned_sample_path(sample)
    return {
        "sample_id": sample.id,
        "audio_path": str(audio_path),
        "reference_text": sample.reference_text,
        "provider_cache_owned": True,
    }


async def upload_avatar(
    profile_id: str,
    image_path: str,
    db: Session,
) -> VoiceProfileResponse:
    """
    Upload and process avatar image for a profile.

    Args:
        profile_id: Profile ID
        image_path: Path to uploaded image file
        db: Database session

    Returns:
        Updated profile
    """
    # Validate profile exists
    profile = db.query(DBVoiceProfile).filter_by(id=profile_id).first()
    if not profile:
        raise ValueError(f"Profile {profile_id} not found")

    # Validate image
    is_valid, error_msg = validate_image(image_path)
    if not is_valid:
        raise ValueError(error_msg)

    # Determine file extension from uploaded file
    from PIL import Image
    with Image.open(image_path) as img:
        # Normalize JPEG variants (MPO is multi-picture format from some cameras)
        img_format = img.format
        if img_format in ('MPO', 'JPG'):
            img_format = 'JPEG'
        
        ext_map = {
            'PNG': '.png',
            'JPEG': '.jpg',
            'WEBP': '.webp'
        }
        ext = ext_map.get(img_format, '.png')

    # Save processed image to profile directory
    profile_dir = _get_profiles_dir() / _safe_identifier(profile_id)
    profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(profile_dir, 0o700, follow_symlinks=False)
    output_path = profile_dir / f"avatar{ext}"
    temporary = profile_dir / f".avatar.{uuid.uuid4().hex}.partial{ext}"
    try:
        process_avatar(image_path, str(temporary))
        os.chmod(temporary, 0o600, follow_symlinks=False)
        if profile.avatar_path:
            old_avatar = owned_avatar_path(profile)
            if old_avatar is None:
                raise ValueError("avatar path is unavailable")
            if old_avatar.exists():
                PrivateVoiceStore(config.get_privacy_dir()).trash_owned_paths(
                    kind="avatar-replaced",
                    object_id=profile_id,
                    paths=(old_avatar,),
                    metadata={"profile_id": profile_id},
                )
        if output_path.exists():
            raise ValueError("avatar destination already exists")
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)

    # Update database
    profile.avatar_path = str(output_path)
    profile.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(profile)

    return VoiceProfileResponse.model_validate(profile)


async def preview_avatar_delete(
    profile_id: str,
    db: Session,
) -> Optional[dict]:
    """Preview the exact avatar file moved by deletion."""

    profile = db.query(DBVoiceProfile).filter_by(id=profile_id).first()
    if not profile or not profile.avatar_path:
        return None
    avatar_path = owned_avatar_path(profile)
    if avatar_path is None:
        return None
    records = {
        f"database/profile-avatar/{profile.id}.json": {
            "schema": "kilix.voicebox.profile-avatar-recovery/candidate-v1",
            "profile_id": profile.id,
            "avatar_name": avatar_path.name,
        }
    }
    files = PrivateVoiceStore(config.get_privacy_dir()).preview_owned_delete(
        (avatar_path,), records=records
    )
    return {"files": list(files), "count": len(files)}


async def delete_avatar(profile_id: str, db: Session) -> Optional[dict]:
    """Move an avatar to recoverable trash, then clear its database index."""

    profile = db.query(DBVoiceProfile).filter_by(id=profile_id).first()
    if not profile or not profile.avatar_path:
        return None

    avatar_path = owned_avatar_path(profile)
    if avatar_path is None:
        return None
    records = {
        f"database/profile-avatar/{profile.id}.json": {
            "schema": "kilix.voicebox.profile-avatar-recovery/candidate-v1",
            "profile_id": profile.id,
            "avatar_name": avatar_path.name,
        }
    }
    receipt = PrivateVoiceStore(config.get_privacy_dir()).trash_owned_paths(
        kind="avatar",
        object_id=profile_id,
        paths=(avatar_path,),
        metadata={"profile_id": profile_id},
        records=records,
    )
    profile.avatar_path = None
    profile.updated_at = datetime.utcnow()
    db.commit()
    return receipt

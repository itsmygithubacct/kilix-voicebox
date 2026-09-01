"""
Generation history management module.
"""

from typing import Optional
from datetime import datetime
import uuid
import re
from pathlib import Path
from sqlalchemy.orm import Session

from .models import GenerationResponse, HistoryQuery, HistoryResponse, HistoryListResponse
from .database import (
    Generation as DBGeneration,
    StoryItem as DBStoryItem,
    VoiceProfile as DBVoiceProfile,
)
from . import config


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def owned_generation_path(generation: object) -> Path:
    """Resolve generated audio only from its exact private provenance slot."""

    generation_id = getattr(generation, "id", None)
    audio_value = getattr(generation, "audio_path", None)
    if (
        type(generation_id) is not str
        or _IDENTIFIER.fullmatch(generation_id) is None
        or type(audio_value) is not str
    ):
        raise ValueError("generation path identity is invalid")
    audio_path = Path(audio_value)
    suffix = audio_path.suffix.lower()
    if suffix not in {".wav", ".flac", ".opus"}:
        raise ValueError("generation format is unsupported")
    expected = config.get_privacy_dir() / "generations" / f"{generation_id}{suffix}"
    if audio_path != expected:
        raise ValueError("generation path is outside its owned slot")
    return expected


def generation_recovery_records(generation: DBGeneration) -> dict[str, dict]:
    """Return the private catalog snapshot needed to restore one generation."""

    audio_path = owned_generation_path(generation)
    return {
        f"database/generations/{generation.id}.json": {
            "schema": "kilix.voicebox.generation-recovery/candidate-v1",
            "id": generation.id,
            "profile_id": generation.profile_id,
            "text": generation.text,
            "language": generation.language,
            "audio_name": audio_path.name,
            "duration": generation.duration,
            "seed": generation.seed,
            "instruct": generation.instruct,
            "created_at": generation.created_at.isoformat(),
        }
    }


def generation_is_in_story(generation_id: str, db: Session) -> bool:
    return db.query(DBStoryItem).filter_by(generation_id=generation_id).first() is not None


def _get_generations_dir() -> Path:
    """Get generations directory from config."""
    return config.get_generations_dir()


async def create_generation(
    profile_id: str,
    text: str,
    language: str,
    audio_path: str,
    duration: float,
    seed: Optional[int],
    db: Session,
    instruct: Optional[str] = None,
    generation_id: Optional[str] = None,
) -> GenerationResponse:
    """
    Create a new generation history entry.

    Args:
        profile_id: Profile ID used for generation
        text: Generated text
        language: Language code
        audio_path: Path where audio was saved
        duration: Audio duration in seconds
        seed: Random seed used (if any)
        db: Database session
        instruct: Natural language instruction used (if any)

    Returns:
        Created generation entry
    """
    db_generation = DBGeneration(
        id=generation_id or str(uuid.uuid4()),
        profile_id=profile_id,
        text=text,
        language=language,
        audio_path=audio_path,
        duration=duration,
        seed=seed,
        instruct=instruct,
        created_at=datetime.utcnow(),
    )

    db.add(db_generation)
    db.commit()
    db.refresh(db_generation)

    return GenerationResponse.model_validate(db_generation)


async def get_generation(
    generation_id: str,
    db: Session,
) -> Optional[GenerationResponse]:
    """
    Get a generation by ID.
    
    Args:
        generation_id: Generation ID
        db: Database session
        
    Returns:
        Generation or None if not found
    """
    generation = db.query(DBGeneration).filter_by(id=generation_id).first()
    if not generation:
        return None
    
    return GenerationResponse.model_validate(generation)


async def list_generations(
    query: HistoryQuery,
    db: Session,
) -> HistoryListResponse:
    """
    List generations with optional filters.
    
    Args:
        query: Query parameters (filters, pagination)
        db: Database session
        
    Returns:
        HistoryListResponse with items and total count
    """
    # Build base query with join to get profile name
    q = db.query(
        DBGeneration,
        DBVoiceProfile.name.label('profile_name')
    ).join(
        DBVoiceProfile,
        DBGeneration.profile_id == DBVoiceProfile.id
    )
    
    # Apply profile filter
    if query.profile_id:
        q = q.filter(DBGeneration.profile_id == query.profile_id)
    
    # Apply search filter (searches in text content)
    if query.search:
        search_pattern = f"%{query.search}%"
        q = q.filter(DBGeneration.text.like(search_pattern))
    
    # Get total count before pagination
    total_count = q.count()
    
    # Apply ordering (newest first)
    q = q.order_by(DBGeneration.created_at.desc())
    
    # Apply pagination
    q = q.offset(query.offset).limit(query.limit)
    
    # Execute query
    results = q.all()
    
    # Convert to HistoryResponse with profile_name
    items = []
    for generation, profile_name in results:
        items.append(HistoryResponse(
            id=generation.id,
            profile_id=generation.profile_id,
            profile_name=profile_name,
            text=generation.text,
            language=generation.language,
            audio_path=generation.audio_path,
            duration=generation.duration,
            seed=generation.seed,
            instruct=generation.instruct,
            created_at=generation.created_at,
        ))
    
    return HistoryListResponse(
        items=items,
        total=total_count,
    )


async def delete_generation(
    generation_id: str,
    db: Session,
) -> bool:
    """
    Delete a generation.
    
    Args:
        generation_id: Generation ID
        db: Database session
        
    Returns:
        True if deleted, False if not found
    """
    generation = db.query(DBGeneration).filter_by(id=generation_id).first()
    if not generation:
        return False
    
    # Product bytes must already have moved to recoverable trash. This function
    # only removes the relational index.
    db.delete(generation)
    db.commit()
    
    return True


async def get_generation_stats(db: Session) -> dict:
    """
    Get generation statistics.
    
    Args:
        db: Database session
        
    Returns:
        Statistics dictionary
    """
    from sqlalchemy import func
    
    total = db.query(func.count(DBGeneration.id)).scalar()
    
    total_duration = db.query(func.sum(DBGeneration.duration)).scalar() or 0
    
    # Get generations by profile
    by_profile = db.query(
        DBGeneration.profile_id,
        func.count(DBGeneration.id).label('count')
    ).group_by(DBGeneration.profile_id).all()
    
    return {
        "total_generations": total,
        "total_duration_seconds": total_duration,
        "generations_by_profile": {
            profile_id: count for profile_id, count in by_profile
        },
    }

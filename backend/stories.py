"""
Story management module.
"""

from typing import List, Optional
from datetime import datetime
import math
import uuid
import tempfile
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import func

from .models import (
    StoryCreate,
    StoryResponse,
    StoryDetailResponse,
    StoryItemDetail,
    StoryItemCreate,
    StoryItemBatchUpdate,
    StoryItemMove,
    StoryItemTrim,
    StoryItemSplit,
    MAX_STORY_ITEMS,
    MAX_STORY_TIMELINE_MS,
)
from .database import Story as DBStory, StoryItem as DBStoryItem, Generation as DBGeneration, VoiceProfile as DBVoiceProfile
from .utils.audio import load_audio, save_audio
from .history import owned_generation_path
from .private_store import PrivateVoiceStore
from . import config
import numpy as np


MAX_RECOVERABLE_STORY_ITEMS = 4_096
MAX_STORY_EXPORT_SAMPLES = 21_600_000


def _generation_duration_ms(generation: DBGeneration) -> int | None:
    duration = generation.duration
    if (
        type(duration) not in {float, int}
        or not math.isfinite(duration)
        or duration <= 0
    ):
        return None
    duration_ms = int(duration * 1_000)
    if not 0 < duration_ms <= MAX_STORY_TIMELINE_MS:
        return None
    return duration_ms


def _story_window(
    generation: DBGeneration,
    *,
    start_time_ms: int,
    trim_start_ms: int = 0,
    trim_end_ms: int = 0,
) -> tuple[int, int] | None:
    """Return bounded effective duration and end time, or refuse the state."""

    duration_ms = _generation_duration_ms(generation)
    if (
        duration_ms is None
        or type(start_time_ms) is not int
        or type(trim_start_ms) is not int
        or type(trim_end_ms) is not int
        or start_time_ms < 0
        or trim_start_ms < 0
        or trim_end_ms < 0
    ):
        return None
    effective_duration_ms = duration_ms - trim_start_ms - trim_end_ms
    end_time_ms = start_time_ms + effective_duration_ms
    if effective_duration_ms <= 0 or end_time_ms > MAX_STORY_TIMELINE_MS:
        return None
    return effective_duration_ms, end_time_ms


def _item_window(
    item: DBStoryItem,
    generation: DBGeneration,
    *,
    start_time_ms: int | None = None,
    trim_start_ms: int | None = None,
    trim_end_ms: int | None = None,
) -> tuple[int, int] | None:
    return _story_window(
        generation,
        start_time_ms=(item.start_time_ms if start_time_ms is None else start_time_ms),
        trim_start_ms=(
            getattr(item, "trim_start_ms", 0)
            if trim_start_ms is None else trim_start_ms
        ),
        trim_end_ms=(
            getattr(item, "trim_end_ms", 0)
            if trim_end_ms is None else trim_end_ms
        ),
    )


def _story_item_count(story_id: str, db: Session) -> int:
    value = db.query(func.count(DBStoryItem.id)).filter(
        DBStoryItem.story_id == story_id
    ).scalar()
    return int(value or 0)


def _bounded_story_items(
    story_id: str,
    db: Session,
) -> list[tuple[DBStoryItem, DBGeneration]] | None:
    """Load a complete story only when every persisted window is exportable."""

    item_count = _story_item_count(story_id, db)
    if item_count > MAX_STORY_ITEMS:
        return None
    rows = db.query(
        DBStoryItem,
        DBGeneration,
    ).join(
        DBGeneration,
        DBStoryItem.generation_id == DBGeneration.id,
    ).filter(
        DBStoryItem.story_id == story_id,
    ).limit(MAX_STORY_ITEMS + 1).all()
    if len(rows) != item_count:
        return None
    if any(_item_window(item, generation) is None for item, generation in rows):
        return None
    return rows


def _story_delete_records(story: DBStory, db: Session) -> dict[str, dict]:
    items = (
        db.query(DBStoryItem)
        .filter_by(story_id=story.id)
        .order_by(DBStoryItem.id)
        .limit(MAX_RECOVERABLE_STORY_ITEMS + 1)
        .all()
    )
    if len(items) > MAX_RECOVERABLE_STORY_ITEMS:
        raise ValueError("STORY_RECOVERY_LIMIT")
    records: dict[str, dict] = {
        f"database/stories/{story.id}.json": {
            "schema": "kilix.voicebox.story-recovery/candidate-v1",
            "id": story.id,
            "name": story.name,
            "description": story.description,
            "created_at": story.created_at.isoformat(),
            "updated_at": story.updated_at.isoformat(),
        }
    }
    for item in items:
        records[f"database/story-items/{item.id}.json"] = {
            "schema": "kilix.voicebox.story-item-recovery/candidate-v1",
            "id": item.id,
            "story_id": item.story_id,
            "generation_id": item.generation_id,
            "start_time_ms": item.start_time_ms,
            "track": item.track,
            "trim_start_ms": item.trim_start_ms,
            "trim_end_ms": item.trim_end_ms,
            "created_at": item.created_at.isoformat(),
        }
    return records


async def create_story(
    data: StoryCreate,
    db: Session,
) -> StoryResponse:
    """
    Create a new story.

    Args:
        data: Story creation data
        db: Database session

    Returns:
        Created story
    """
    db_story = DBStory(
        id=str(uuid.uuid4()),
        name=data.name,
        description=data.description,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    db.add(db_story)
    db.commit()
    db.refresh(db_story)

    # Get item count
    item_count = db.query(func.count(DBStoryItem.id)).filter(
        DBStoryItem.story_id == db_story.id
    ).scalar()

    response = StoryResponse.model_validate(db_story)
    response.item_count = item_count
    return response


async def list_stories(
    db: Session,
) -> List[StoryResponse]:
    """
    List all stories.

    Args:
        db: Database session

    Returns:
        List of stories with item counts
    """
    stories = db.query(DBStory).order_by(DBStory.updated_at.desc()).all()
    
    result = []
    for story in stories:
        item_count = db.query(func.count(DBStoryItem.id)).filter(
            DBStoryItem.story_id == story.id
        ).scalar()
        
        response = StoryResponse.model_validate(story)
        response.item_count = item_count
        result.append(response)
    
    return result


async def get_story(
    story_id: str,
    db: Session,
) -> Optional[StoryDetailResponse]:
    """
    Get a story with all its items.

    Args:
        story_id: Story ID
        db: Database session

    Returns:
        Story with items or None if not found
    """
    story = db.query(DBStory).filter_by(id=story_id).first()
    if not story:
        return None

    # Get all items ordered by start_time_ms
    items = db.query(
        DBStoryItem,
        DBGeneration,
        DBVoiceProfile.name.label('profile_name')
    ).join(
        DBGeneration,
        DBStoryItem.generation_id == DBGeneration.id
    ).join(
        DBVoiceProfile,
        DBGeneration.profile_id == DBVoiceProfile.id
    ).filter(
        DBStoryItem.story_id == story_id
    ).order_by(DBStoryItem.start_time_ms).all()

    # Build item details
    item_details = []
    for item, generation, profile_name in items:
        item_detail = StoryItemDetail(
            id=item.id,
            story_id=item.story_id,
            generation_id=item.generation_id,
            start_time_ms=item.start_time_ms,
            track=item.track,
            trim_start_ms=getattr(item, 'trim_start_ms', 0),
            trim_end_ms=getattr(item, 'trim_end_ms', 0),
            created_at=item.created_at,
            profile_id=generation.profile_id,
            profile_name=profile_name,
            text=generation.text,
            language=generation.language,
            audio_path=generation.audio_path,
            duration=generation.duration,
            seed=generation.seed,
            instruct=generation.instruct,
            generation_created_at=generation.created_at,
        )
        item_details.append(item_detail)

    response = StoryDetailResponse.model_validate(story)
    response.items = item_details
    return response


async def update_story(
    story_id: str,
    data: StoryCreate,
    db: Session,
) -> Optional[StoryResponse]:
    """
    Update a story.

    Args:
        story_id: Story ID
        data: Update data
        db: Database session

    Returns:
        Updated story or None if not found
    """
    story = db.query(DBStory).filter_by(id=story_id).first()
    if not story:
        return None

    story.name = data.name
    story.description = data.description
    story.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(story)

    # Get item count
    item_count = db.query(func.count(DBStoryItem.id)).filter(
        DBStoryItem.story_id == story.id
    ).scalar()

    response = StoryResponse.model_validate(story)
    response.item_count = item_count
    return response


async def preview_story_delete(
    story_id: str,
    db: Session,
) -> Optional[dict]:
    """Preview the exact database records archived by story deletion."""

    story = db.query(DBStory).filter_by(id=story_id).first()
    if not story:
        return None
    records = _story_delete_records(story, db)
    files = PrivateVoiceStore(config.get_privacy_dir()).preview_record_archive(records)
    return {"files": list(files), "count": len(files)}


async def delete_story(
    story_id: str,
    db: Session,
) -> Optional[dict]:
    """
    Archive a story and all its items to private recoverable trash.

    Args:
        story_id: Story ID
        db: Database session

    Returns:
        Recovery receipt, or None if not found
    """
    story = db.query(DBStory).filter_by(id=story_id).first()
    if not story:
        return None

    records = _story_delete_records(story, db)
    receipt = PrivateVoiceStore(config.get_privacy_dir()).archive_records(
        kind="story",
        object_id=story_id,
        records=records,
        metadata={"item_count": len(records) - 1, "name": story.name},
    )

    # Delete all items
    db.query(DBStoryItem).filter_by(story_id=story_id).delete()

    # Delete story
    db.delete(story)
    db.commit()

    return receipt


async def add_item_to_story(
    story_id: str,
    data: StoryItemCreate,
    db: Session,
) -> Optional[StoryItemDetail]:
    """
    Add a generation to a story.

    Args:
        story_id: Story ID
        data: Item creation data
        db: Database session

    Returns:
        Created item detail or None if story/generation not found
    """
    # Verify story exists
    story = db.query(DBStory).filter_by(id=story_id).first()
    if not story:
        return None

    # Verify generation exists
    generation = db.query(DBGeneration).filter_by(id=data.generation_id).first()
    if not generation:
        return None

    # Check if generation is already in story
    existing = db.query(DBStoryItem).filter_by(
        story_id=story_id,
        generation_id=data.generation_id
    ).first()
    if existing:
        if _item_window(existing, generation) is None:
            return None
        # Return existing item
        profile = db.query(DBVoiceProfile).filter_by(id=generation.profile_id).first()
        return StoryItemDetail(
            id=existing.id,
            story_id=existing.story_id,
            generation_id=existing.generation_id,
            start_time_ms=existing.start_time_ms,
            track=existing.track,
            trim_start_ms=getattr(existing, 'trim_start_ms', 0),
            trim_end_ms=getattr(existing, 'trim_end_ms', 0),
            created_at=existing.created_at,
            profile_id=generation.profile_id,
            profile_name=profile.name if profile else "Unknown",
            text=generation.text,
            language=generation.language,
            audio_path=generation.audio_path,
            duration=generation.duration,
            seed=generation.seed,
            instruct=generation.instruct,
            generation_created_at=generation.created_at,
        )

    existing_items = _bounded_story_items(story_id, db)
    if existing_items is None or len(existing_items) >= MAX_STORY_ITEMS:
        return None

    # Calculate start_time_ms if not provided
    if data.start_time_ms is not None:
        start_time_ms = data.start_time_ms
    else:
        if not existing_items:
            # First item starts at 0
            start_time_ms = 0
        else:
            max_end_time_ms = 0
            for item, gen in existing_items:
                window = _item_window(item, gen)
                if window is None:
                    return None
                _duration_ms, item_end_ms = window
                max_end_time_ms = max(max_end_time_ms, item_end_ms)
            
            # Add 200ms gap after the last item
            start_time_ms = max_end_time_ms + 200

    if _story_window(generation, start_time_ms=start_time_ms) is None:
        return None

    # Get track from data or default to 0
    track = data.track if data.track is not None else 0

    # Create item
    item = DBStoryItem(
        id=str(uuid.uuid4()),
        story_id=story_id,
        generation_id=data.generation_id,
        start_time_ms=start_time_ms,
        track=track,
        created_at=datetime.utcnow(),
    )

    db.add(item)
    
    # Update story updated_at
    story.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(item)

    # Get profile name
    profile = db.query(DBVoiceProfile).filter_by(id=generation.profile_id).first()

    return StoryItemDetail(
        id=item.id,
        story_id=item.story_id,
        generation_id=item.generation_id,
        start_time_ms=item.start_time_ms,
        track=item.track,
        trim_start_ms=getattr(item, 'trim_start_ms', 0),
        trim_end_ms=getattr(item, 'trim_end_ms', 0),
        created_at=item.created_at,
        profile_id=generation.profile_id,
        profile_name=profile.name if profile else "Unknown",
        text=generation.text,
        language=generation.language,
        audio_path=generation.audio_path,
        duration=generation.duration,
        seed=generation.seed,
        instruct=generation.instruct,
        generation_created_at=generation.created_at,
    )


async def move_story_item(
    story_id: str,
    item_id: str,
    data: StoryItemMove,
    db: Session,
) -> Optional[StoryItemDetail]:
    """
    Move a story item (update position and/or track).

    Args:
        story_id: Story ID
        item_id: Story item ID
        data: New position and track data
        db: Database session

    Returns:
        Updated item detail or None if not found
    """
    # Get the item
    item = db.query(DBStoryItem).filter_by(
        id=item_id,
        story_id=story_id,
    ).first()
    if not item:
        return None

    # Get the generation
    generation = db.query(DBGeneration).filter_by(id=item.generation_id).first()
    if not generation:
        return None

    if (
        _bounded_story_items(story_id, db) is None
        or _item_window(item, generation, start_time_ms=data.start_time_ms) is None
    ):
        return None

    # Update position and track
    item.start_time_ms = data.start_time_ms
    item.track = data.track

    # Update story updated_at
    story = db.query(DBStory).filter_by(id=story_id).first()
    if story:
        story.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(item)

    # Get profile name
    profile = db.query(DBVoiceProfile).filter_by(id=generation.profile_id).first()

    return StoryItemDetail(
        id=item.id,
        story_id=item.story_id,
        generation_id=item.generation_id,
        start_time_ms=item.start_time_ms,
        track=item.track,
        trim_start_ms=getattr(item, 'trim_start_ms', 0),
        trim_end_ms=getattr(item, 'trim_end_ms', 0),
        created_at=item.created_at,
        profile_id=generation.profile_id,
        profile_name=profile.name if profile else "Unknown",
        text=generation.text,
        language=generation.language,
        audio_path=generation.audio_path,
        duration=generation.duration,
        seed=generation.seed,
        instruct=generation.instruct,
        generation_created_at=generation.created_at,
    )


async def remove_item_from_story(
    story_id: str,
    item_id: str,
    db: Session,
) -> bool:
    """
    Remove a story item from a story.

    Args:
        story_id: Story ID
        item_id: Story item ID to remove
        db: Database session

    Returns:
        True if removed, False if not found
    """
    item = db.query(DBStoryItem).filter_by(
        id=item_id,
        story_id=story_id,
    ).first()
    if not item:
        return False

    # Delete item
    db.delete(item)

    # Update story updated_at
    story = db.query(DBStory).filter_by(id=story_id).first()
    if story:
        story.updated_at = datetime.utcnow()

    db.commit()
    return True


async def trim_story_item(
    story_id: str,
    item_id: str,
    data: StoryItemTrim,
    db: Session,
) -> Optional[StoryItemDetail]:
    """
    Trim a story item (update trim_start_ms and trim_end_ms).

    Args:
        story_id: Story ID
        item_id: Story item ID
        data: Trim data (trim_start_ms, trim_end_ms)
        db: Database session

    Returns:
        Updated item detail or None if not found
    """
    # Get the item
    item = db.query(DBStoryItem).filter_by(
        id=item_id,
        story_id=story_id,
    ).first()
    if not item:
        return None

    # Get the generation
    generation = db.query(DBGeneration).filter_by(id=item.generation_id).first()
    if not generation:
        return None

    if (
        _bounded_story_items(story_id, db) is None
        or _item_window(
            item,
            generation,
            trim_start_ms=data.trim_start_ms,
            trim_end_ms=data.trim_end_ms,
        ) is None
    ):
        return None

    # Update trim values
    item.trim_start_ms = data.trim_start_ms
    item.trim_end_ms = data.trim_end_ms

    # Update story updated_at
    story = db.query(DBStory).filter_by(id=story_id).first()
    if story:
        story.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(item)

    # Get profile name
    profile = db.query(DBVoiceProfile).filter_by(id=generation.profile_id).first()

    return StoryItemDetail(
        id=item.id,
        story_id=item.story_id,
        generation_id=item.generation_id,
        start_time_ms=item.start_time_ms,
        track=item.track,
        trim_start_ms=item.trim_start_ms,
        trim_end_ms=item.trim_end_ms,
        created_at=item.created_at,
        profile_id=generation.profile_id,
        profile_name=profile.name if profile else "Unknown",
        text=generation.text,
        language=generation.language,
        audio_path=generation.audio_path,
        duration=generation.duration,
        seed=generation.seed,
        instruct=generation.instruct,
        generation_created_at=generation.created_at,
    )


async def split_story_item(
    story_id: str,
    item_id: str,
    data: StoryItemSplit,
    db: Session,
) -> Optional[List[StoryItemDetail]]:
    """
    Split a story item at a given time, creating two clips.

    Args:
        story_id: Story ID
        item_id: Story item ID to split
        data: Split data (split_time_ms - time within clip to split at)
        db: Database session

    Returns:
        List of two updated item details (original and new) or None if not found/invalid
    """
    # Get the item
    item = db.query(DBStoryItem).filter_by(
        id=item_id,
        story_id=story_id,
    ).first()
    if not item:
        return None

    # Get the generation
    generation = db.query(DBGeneration).filter_by(id=item.generation_id).first()
    if not generation:
        return None

    story_items = _bounded_story_items(story_id, db)
    if story_items is None or len(story_items) >= MAX_STORY_ITEMS:
        return None

    # Calculate effective duration and validate split point
    current_trim_start = getattr(item, 'trim_start_ms', 0)
    current_trim_end = getattr(item, 'trim_end_ms', 0)
    original_duration_ms = _generation_duration_ms(generation)
    current_window = _item_window(item, generation)
    if original_duration_ms is None or current_window is None:
        return None
    effective_duration_ms, _current_end_ms = current_window

    # Validate split_time_ms is within the effective duration
    if data.split_time_ms <= 0 or data.split_time_ms >= effective_duration_ms:
        return None  # Invalid split point

    # Calculate the absolute time in the original audio where we're splitting
    absolute_split_ms = current_trim_start + data.split_time_ms

    original_trim_end_ms = original_duration_ms - absolute_split_ms
    new_start_time_ms = item.start_time_ms + data.split_time_ms
    if (
        _story_window(
            generation,
            start_time_ms=item.start_time_ms,
            trim_start_ms=current_trim_start,
            trim_end_ms=original_trim_end_ms,
        ) is None
        or _story_window(
            generation,
            start_time_ms=new_start_time_ms,
            trim_start_ms=absolute_split_ms,
            trim_end_ms=current_trim_end,
        ) is None
    ):
        return None

    # Update original clip: trim from the end
    item.trim_end_ms = original_trim_end_ms

    # Create new clip: starts after the split, trimmed from the start
    new_item = DBStoryItem(
        id=str(uuid.uuid4()),
        story_id=story_id,
        generation_id=item.generation_id,  # Same generation, different trim
        start_time_ms=new_start_time_ms,
        track=item.track,
        trim_start_ms=absolute_split_ms,
        trim_end_ms=current_trim_end,
        created_at=datetime.utcnow(),
    )

    db.add(new_item)

    # Update story updated_at
    story = db.query(DBStory).filter_by(id=story_id).first()
    if story:
        story.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(item)
    db.refresh(new_item)

    # Get profile name
    profile = db.query(DBVoiceProfile).filter_by(id=generation.profile_id).first()
    profile_name = profile.name if profile else "Unknown"

    # Build response items
    original_item_detail = StoryItemDetail(
        id=item.id,
        story_id=item.story_id,
        generation_id=item.generation_id,
        start_time_ms=item.start_time_ms,
        track=item.track,
        trim_start_ms=item.trim_start_ms,
        trim_end_ms=item.trim_end_ms,
        created_at=item.created_at,
        profile_id=generation.profile_id,
        profile_name=profile_name,
        text=generation.text,
        language=generation.language,
        audio_path=generation.audio_path,
        duration=generation.duration,
        seed=generation.seed,
        instruct=generation.instruct,
        generation_created_at=generation.created_at,
    )

    new_item_detail = StoryItemDetail(
        id=new_item.id,
        story_id=new_item.story_id,
        generation_id=new_item.generation_id,
        start_time_ms=new_item.start_time_ms,
        track=new_item.track,
        trim_start_ms=new_item.trim_start_ms,
        trim_end_ms=new_item.trim_end_ms,
        created_at=new_item.created_at,
        profile_id=generation.profile_id,
        profile_name=profile_name,
        text=generation.text,
        language=generation.language,
        audio_path=generation.audio_path,
        duration=generation.duration,
        seed=generation.seed,
        instruct=generation.instruct,
        generation_created_at=generation.created_at,
    )

    return [original_item_detail, new_item_detail]


async def duplicate_story_item(
    story_id: str,
    item_id: str,
    db: Session,
) -> Optional[StoryItemDetail]:
    """
    Duplicate a story item, creating a copy with all properties.

    Args:
        story_id: Story ID
        item_id: Story item ID to duplicate
        db: Database session

    Returns:
        New item detail or None if not found
    """
    # Get the original item
    original_item = db.query(DBStoryItem).filter_by(
        id=item_id,
        story_id=story_id,
    ).first()
    if not original_item:
        return None

    # Get the generation
    generation = db.query(DBGeneration).filter_by(id=original_item.generation_id).first()
    if not generation:
        return None

    story_items = _bounded_story_items(story_id, db)
    if story_items is None or len(story_items) >= MAX_STORY_ITEMS:
        return None

    # Calculate effective duration
    current_trim_start = getattr(original_item, 'trim_start_ms', 0)
    current_trim_end = getattr(original_item, 'trim_end_ms', 0)
    current_window = _item_window(original_item, generation)
    if current_window is None:
        return None
    _effective_duration_ms, current_end_time_ms = current_window
    new_start_time_ms = current_end_time_ms + 200
    if _story_window(
        generation,
        start_time_ms=new_start_time_ms,
        trim_start_ms=current_trim_start,
        trim_end_ms=current_trim_end,
    ) is None:
        return None

    # Create duplicate item - place it right after the original
    new_item = DBStoryItem(
        id=str(uuid.uuid4()),
        story_id=story_id,
        generation_id=original_item.generation_id,  # Same generation as original
        start_time_ms=new_start_time_ms,
        track=original_item.track,
        trim_start_ms=current_trim_start,
        trim_end_ms=current_trim_end,
        created_at=datetime.utcnow(),
    )

    db.add(new_item)

    # Update story updated_at
    story = db.query(DBStory).filter_by(id=story_id).first()
    if story:
        story.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(new_item)

    # Get profile name
    profile = db.query(DBVoiceProfile).filter_by(id=generation.profile_id).first()

    return StoryItemDetail(
        id=new_item.id,
        story_id=new_item.story_id,
        generation_id=new_item.generation_id,
        start_time_ms=new_item.start_time_ms,
        track=new_item.track,
        trim_start_ms=new_item.trim_start_ms,
        trim_end_ms=new_item.trim_end_ms,
        created_at=new_item.created_at,
        profile_id=generation.profile_id,
        profile_name=profile.name if profile else "Unknown",
        text=generation.text,
        language=generation.language,
        audio_path=generation.audio_path,
        duration=generation.duration,
        seed=generation.seed,
        instruct=generation.instruct,
        generation_created_at=generation.created_at,
    )


async def update_story_item_times(
    story_id: str,
    data: StoryItemBatchUpdate,
    db: Session,
) -> bool:
    """
    Update story item timecodes.

    Args:
        story_id: Story ID
        data: Batch update data with timecodes
        db: Database session

    Returns:
        True if updated, False if story not found or invalid
    """
    story = db.query(DBStory).filter_by(id=story_id).first()
    if not story:
        return False

    # Validate the complete persisted story before changing any item.
    items_with_gen = _bounded_story_items(story_id, db)
    if items_with_gen is None:
        return False
    item_map = {item.id: (item, generation) for item, generation in items_with_gen}
    update_ids = [update.item_id for update in data.updates]
    if len(update_ids) != len(set(update_ids)) or not set(update_ids) <= set(item_map):
        return False

    proposed_starts = {update.item_id: update.start_time_ms for update in data.updates}
    for item_id, (item, generation) in item_map.items():
        if _item_window(
            item,
            generation,
            start_time_ms=proposed_starts.get(item_id, item.start_time_ms),
        ) is None:
            return False

    for item_id, start_time_ms in proposed_starts.items():
        item_map[item_id][0].start_time_ms = start_time_ms

    # Update story updated_at
    story.updated_at = datetime.utcnow()

    db.commit()
    return True


async def reorder_story_items(
    story_id: str,
    item_ids: List[str],
    db: Session,
    gap_ms: int = 200,
) -> Optional[List[StoryItemDetail]]:
    """
    Reorder story items and recalculate timecodes.

    Args:
        story_id: Story ID
        item_ids: List of unique story item IDs in the desired order
        db: Database session
        gap_ms: Gap in milliseconds between items (default 200ms)

    Returns:
        Updated list of story items with new timecodes, or None if invalid
    """
    story = db.query(DBStory).filter_by(id=story_id).first()
    if not story:
        return None

    item_count = _story_item_count(story_id, db)
    if (
        item_count > MAX_STORY_ITEMS
        or type(gap_ms) is not int
        or not 0 <= gap_ms <= MAX_STORY_TIMELINE_MS
    ):
        return None

    # Get all items for this story with their generation data
    items_with_gen = db.query(
        DBStoryItem,
        DBGeneration,
        DBVoiceProfile.name.label('profile_name')
    ).join(
        DBGeneration,
        DBStoryItem.generation_id == DBGeneration.id
    ).join(
        DBVoiceProfile,
        DBGeneration.profile_id == DBVoiceProfile.id
    ).filter(
        DBStoryItem.story_id == story_id
    ).limit(MAX_STORY_ITEMS + 1).all()

    if len(items_with_gen) != item_count:
        return None

    # Create maps for quick lookup
    item_map = {
        item.id: (item, gen, profile_name)
        for item, gen, profile_name in items_with_gen
    }

    # Verify the request names each exact story item once.
    if (
        len(item_ids) != len(item_map)
        or len(item_ids) != len(set(item_ids))
        or set(item_ids) != set(item_map)
    ):
        return None

    # Plan every new timecode before mutating any database object.
    current_time_ms = 0
    planned_items = []

    for item_id in item_ids:
        item, generation, profile_name = item_map[item_id]
        window = _item_window(
            item,
            generation,
            start_time_ms=current_time_ms,
        )
        if window is None:
            return None
        _duration_ms, end_time_ms = window
        planned_items.append((item, generation, profile_name, current_time_ms))

        # Move to next position (current end + gap)
        current_time_ms = end_time_ms + gap_ms

    updated_items = []
    for item, generation, profile_name, start_time_ms in planned_items:
        item.start_time_ms = start_time_ms

        # Build the response item
        updated_items.append(StoryItemDetail(
            id=item.id,
            story_id=item.story_id,
            generation_id=item.generation_id,
            start_time_ms=item.start_time_ms,
            track=item.track,
            trim_start_ms=getattr(item, 'trim_start_ms', 0),
            trim_end_ms=getattr(item, 'trim_end_ms', 0),
            created_at=item.created_at,
            profile_id=generation.profile_id,
            profile_name=profile_name,
            text=generation.text,
            language=generation.language,
            audio_path=generation.audio_path,
            duration=generation.duration,
            seed=generation.seed,
            instruct=generation.instruct,
            generation_created_at=generation.created_at,
        ))

    # Update story updated_at
    story.updated_at = datetime.utcnow()

    db.commit()
    return updated_items


async def export_story_audio(
    story_id: str,
    db: Session,
) -> Optional[bytes]:
    """
    Export story as single mixed audio file with timecode-based mixing.

    Args:
        story_id: Story ID
        db: Database session

    Returns:
        Audio file bytes or None if story not found
    """
    story = db.query(DBStory).filter_by(id=story_id).first()
    if not story:
        return None

    item_count = _story_item_count(story_id, db)
    if item_count == 0:
        return None
    if item_count > MAX_STORY_ITEMS:
        raise ValueError("STORY_EXPORT_ITEM_BOUND")

    # Get all items ordered by start_time_ms
    items = db.query(
        DBStoryItem,
        DBGeneration
    ).join(
        DBGeneration,
        DBStoryItem.generation_id == DBGeneration.id
    ).filter(
        DBStoryItem.story_id == story_id
    ).order_by(DBStoryItem.start_time_ms).limit(MAX_STORY_ITEMS + 1).all()

    # Orphaned items must not silently disappear from the exported timeline.
    if len(items) != item_count:
        raise ValueError("STORY_EXPORT_SOURCE")

    clip_bounds: list[tuple[DBStoryItem, int, Path]] = []
    max_end_time_ms = 0
    for item, generation in items:
        window = _item_window(item, generation)
        if window is None:
            raise ValueError("STORY_EXPORT_BOUND")
        effective_duration_ms, end_time_ms = window
        try:
            audio_path = owned_generation_path(generation)
            if audio_path.is_symlink() or not audio_path.is_file():
                raise ValueError("STORY_EXPORT_SOURCE")
        except Exception:
            raise ValueError("STORY_EXPORT_SOURCE") from None
        max_end_time_ms = max(max_end_time_ms, end_time_ms)
        clip_bounds.append((item, effective_duration_ms, audio_path))

    sample_rate = 24_000
    total_samples = (max_end_time_ms * sample_rate + 999) // 1000
    if not 0 < total_samples <= MAX_STORY_EXPORT_SAMPLES:
        raise ValueError("STORY_EXPORT_BOUND")

    final_audio = np.zeros(total_samples, dtype=np.float32)
    for item, effective_duration_ms, audio_path in clip_bounds:
        try:
            audio, sr = load_audio(
                audio_path,
                sample_rate=sample_rate,
                max_duration_seconds=900.0,
            )
            if sr != sample_rate:
                raise ValueError("STORY_EXPORT_SAMPLE_RATE")
            
            # Get trim values
            trim_start_ms = getattr(item, 'trim_start_ms', 0)
            
            # Mix exactly the database-declared effective window. Extra
            # decoded frames are ignored; a short source refuses the export.
            trim_start_sample = (trim_start_ms * sample_rate) // 1000
            expected_samples = (effective_duration_ms * sample_rate) // 1000
            trim_stop_sample = trim_start_sample + expected_samples
            if (
                expected_samples <= 0
                or not 0 <= trim_start_sample < trim_stop_sample <= len(audio)
            ):
                raise ValueError("STORY_EXPORT_TRIM")
            trimmed_audio = audio[trim_start_sample:trim_stop_sample]

            start_sample = (item.start_time_ms * sample_rate) // 1000
            end_sample = start_sample + expected_samples
            if (
                start_sample >= total_samples
                or end_sample <= start_sample
                or end_sample > total_samples
            ):
                raise ValueError("STORY_EXPORT_BOUND")
            final_audio[start_sample:end_sample] += trimmed_audio
        except Exception:
            raise ValueError("STORY_EXPORT_SOURCE") from None

    # Normalize to prevent clipping
    max_val = np.abs(final_audio).max()
    if max_val > 1.0:
        final_audio = final_audio / max_val

    # Save to temporary file
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        save_audio(final_audio, tmp_path, sample_rate)

        # Read file bytes
        with open(tmp_path, 'rb') as f:
            audio_bytes = f.read()

        return audio_bytes
    finally:
        # Clean up temp file
        Path(tmp_path).unlink(missing_ok=True)

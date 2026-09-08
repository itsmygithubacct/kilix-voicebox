"""
Pydantic models for request/response validation.
"""

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_serializer, model_validator
from typing import Optional, List, Literal
from datetime import datetime


MAX_STORY_ITEMS = 4_096
MAX_STORY_TIMELINE_MS = 900_000


class VoiceProfileCreate(BaseModel):
    """Request model for creating a voice profile."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    language: str = Field(default="en", pattern="^(zh|en|ja|ko|de|fr|ru|pt|es|it)$")


class VoiceProfileResponse(BaseModel):
    """Response model for voice profile."""
    id: str
    name: str
    description: Optional[str]
    language: str
    avatar_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("avatar_path")
    def serialize_avatar_path(self, value: Optional[str]) -> Optional[str]:
        """Expose a loopback API resource, never the private filesystem path."""
        return f"/profiles/{self.id}/avatar" if value else None

    class Config:
        from_attributes = True


class ProfileSampleCreate(BaseModel):
    """Request model for adding a sample to a profile."""
    reference_text: str = Field(..., min_length=1, max_length=1000)


class ProfileSampleUpdate(BaseModel):
    """Request model for updating a profile sample."""
    reference_text: str = Field(..., min_length=1, max_length=1000)


class ProfileSampleResponse(BaseModel):
    """Response model for profile sample."""
    id: str
    profile_id: str
    audio_path: str
    reference_text: str

    @field_serializer("audio_path")
    def serialize_audio_path(self, _value: str) -> str:
        """Expose a loopback API resource, never the private filesystem path."""
        return f"/samples/{self.id}"

    class Config:
        from_attributes = True


class GenerationRequest(BaseModel):
    """Request model for voice generation."""
    profile_id: str
    text: str = Field(..., min_length=1, max_length=5000)
    language: str = Field(default="en", pattern="^(zh|en|ja|ko|de|fr|ru|pt|es|it)$")
    seed: Optional[StrictInt] = Field(None, ge=0, lt=2**63)
    instruct: Optional[str] = Field(None, max_length=500)
    consent_asserted: StrictBool = False
    mode: Literal["prompt_clone", "named_voice", "voice_design"] = "prompt_clone"
    voice_id: Optional[str] = Field(None, min_length=1, max_length=64)
    description: Optional[str] = Field(None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def validate_mode(self):
        if self.mode == "prompt_clone":
            if self.voice_id is not None or self.description is not None:
                raise ValueError("clone mode does not accept named/design conditioning")
        elif self.consent_asserted:
            raise ValueError("sample consent applies only to clone mode")
        elif self.mode == "named_voice":
            if self.voice_id is None or not self.voice_id.strip() or self.description is not None:
                raise ValueError("named voice mode requires only a voice ID")
        elif self.description is None or not self.description.strip() or self.voice_id is not None:
            raise ValueError("voice design mode requires only a description")
        return self


class GenerationResponse(BaseModel):
    """Response model for voice generation."""
    id: str
    profile_id: str
    text: str
    language: str
    audio_path: str
    duration: float
    seed: Optional[int]
    instruct: Optional[str]
    created_at: datetime

    @field_serializer("audio_path")
    def serialize_audio_path(self, _value: str) -> str:
        """Expose a loopback API resource, never the private filesystem path."""
        return f"/audio/{self.id}"

    class Config:
        from_attributes = True


class HistoryQuery(BaseModel):
    """Query model for generation history."""
    profile_id: Optional[str] = None
    search: Optional[str] = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class HistoryResponse(BaseModel):
    """Response model for history entry (includes profile name)."""
    id: str
    profile_id: str
    profile_name: str
    text: str
    language: str
    audio_path: str
    duration: float
    seed: Optional[int]
    instruct: Optional[str]
    created_at: datetime

    @field_serializer("audio_path")
    def serialize_audio_path(self, _value: str) -> str:
        """Expose a loopback API resource, never the private filesystem path."""
        return f"/audio/{self.id}"

    class Config:
        from_attributes = True


class HistoryListResponse(BaseModel):
    """Response model for history list."""
    items: List[HistoryResponse]
    total: int


class TranscriptionRequest(BaseModel):
    """Request model for audio transcription."""
    language: Optional[str] = Field(None, pattern="^(en|zh)$")


class TranscriptionResponse(BaseModel):
    """Response model for transcription."""
    text: str
    duration: float


class HealthResponse(BaseModel):
    """Response model for health check."""
    status: str
    qwen_provider: str = "unavailable"
    transcribe_provider: str = "unavailable"


class ModelStatus(BaseModel):
    """Read-only provider inventory projection."""
    provider: str
    model_id: str
    display_name: str
    installed: bool
    resident: bool = False
    installed_bytes: Optional[int] = None


class ModelStatusListResponse(BaseModel):
    """Response model for model status list."""
    models: List[ModelStatus]


class AudioChannelCreate(BaseModel):
    """Request model for creating an audio channel."""
    name: str = Field(..., min_length=1, max_length=100)
    device_ids: List[str] = Field(default_factory=list)


class AudioChannelUpdate(BaseModel):
    """Request model for updating an audio channel."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    device_ids: Optional[List[str]] = None


class AudioChannelResponse(BaseModel):
    """Response model for audio channel."""
    id: str
    name: str
    is_default: bool
    device_ids: List[str]
    created_at: datetime

    class Config:
        from_attributes = True


class ChannelVoiceAssignment(BaseModel):
    """Request model for assigning voices to a channel."""
    profile_ids: List[str]


class ProfileChannelAssignment(BaseModel):
    """Request model for assigning channels to a profile."""
    channel_ids: List[str]


class StoryCreate(BaseModel):
    """Request model for creating a story."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)


class StoryResponse(BaseModel):
    """Response model for story (list view)."""
    id: str
    name: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime
    item_count: int = 0

    class Config:
        from_attributes = True


class StoryItemDetail(BaseModel):
    """Detail model for story item with generation info."""
    id: str
    story_id: str
    generation_id: str
    start_time_ms: int
    track: int = 0
    trim_start_ms: int = 0
    trim_end_ms: int = 0
    created_at: datetime
    # Generation details
    profile_id: str
    profile_name: str
    text: str
    language: str
    audio_path: str
    duration: float
    seed: Optional[int]
    instruct: Optional[str]
    generation_created_at: datetime

    @field_serializer("audio_path")
    def serialize_audio_path(self, _value: str) -> str:
        """Expose a loopback API resource, never the private filesystem path."""
        return f"/audio/{self.generation_id}"

    class Config:
        from_attributes = True


class StoryDetailResponse(BaseModel):
    """Response model for story with items."""
    id: str
    name: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime
    items: List[StoryItemDetail] = []

    class Config:
        from_attributes = True


class StoryItemCreate(BaseModel):
    """Request model for adding a generation to a story."""
    generation_id: str
    start_time_ms: Optional[int] = Field(
        default=None, ge=0, le=MAX_STORY_TIMELINE_MS
    )
    track: Optional[int] = Field(default=0, ge=0, le=255)


class StoryItemUpdateTime(BaseModel):
    """Request model for updating a story item's timecode."""
    item_id: str
    start_time_ms: int = Field(..., ge=0, le=MAX_STORY_TIMELINE_MS)


class StoryItemBatchUpdate(BaseModel):
    """Request model for batch updating story item timecodes."""
    updates: List[StoryItemUpdateTime] = Field(
        ..., min_length=1, max_length=MAX_STORY_ITEMS
    )


class StoryItemReorder(BaseModel):
    """Request model for reordering story items."""
    item_ids: List[str] = Field(..., min_length=1, max_length=MAX_STORY_ITEMS)


class StoryItemMove(BaseModel):
    """Request model for moving a story item (position and/or track)."""
    start_time_ms: int = Field(..., ge=0, le=MAX_STORY_TIMELINE_MS)
    track: int = Field(default=0, ge=0, le=255)


class StoryItemTrim(BaseModel):
    """Request model for trimming a story item."""
    trim_start_ms: int = Field(..., ge=0)
    trim_end_ms: int = Field(..., ge=0)


class StoryItemSplit(BaseModel):
    """Request model for splitting a story item."""
    split_time_ms: int = Field(..., gt=0, le=MAX_STORY_TIMELINE_MS)

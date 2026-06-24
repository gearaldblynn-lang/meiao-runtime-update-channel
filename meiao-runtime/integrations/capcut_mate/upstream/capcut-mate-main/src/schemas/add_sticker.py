from pydantic import BaseModel, Field


class AddStickerRequest(BaseModel):
    """Add a sticker segment to a draft."""

    draft_url: str = Field(..., description="Draft URL")
    sticker_id: str = Field(..., description="Sticker resource ID")
    start: int = Field(..., description="Start time in microseconds")
    end: int = Field(..., description="End time in microseconds")
    scale: float = Field(default=1.0, description="Sticker scale")
    alpha: float = Field(default=1.0, ge=0.0, le=1.0, description="Sticker opacity, 0-1")
    transform_x: int = Field(default=0, description="X offset in pixels")
    transform_y: int = Field(default=0, description="Y offset in pixels")


class AddStickerResponse(BaseModel):
    """Add sticker response."""

    draft_url: str = Field(default="", description="Draft URL")
    sticker_id: str = Field(default="", description="Sticker resource ID")
    track_id: str = Field(default="", description="Track ID")
    segment_id: str = Field(default="", description="Segment ID")
    duration: int = Field(default=0, description="Sticker duration in microseconds")

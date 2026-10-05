from typing import Literal

from pydantic import BaseModel, Field


class VideoQuotaSnapshot(BaseModel):
    enabled: bool
    scope: Literal['shared_backend']
    day: str
    timezone: Literal['Asia/Shanghai']
    daily_video_seconds_limit: int | None
    reserved_video_seconds: int = Field(ge=0)
    remaining_video_seconds: int | None
    max_video_regenerations_per_episode: int | None
    episode_regenerations: dict[str, int]
    accounting: Literal['dispatch_reservations_not_billed_usage']

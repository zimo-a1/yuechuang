from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from api.schemas.task import TaskError


class ProjectStartRequest(BaseModel):
    idea: str
    file_path: Optional[str] = None
    style: Optional[str] = None
    video_ratio: Optional[str] = "9:16"
    video_resolution: Optional[str] = "720P"
    expand_idea: Optional[bool] = True
    llm_model: Optional[str] = None
    vlm_model: Optional[str] = None
    image_t2i_model: Optional[str] = None
    image_it2i_model: Optional[str] = None
    video_model: Optional[str] = None
    video_first_frame_model: Optional[str] = None
    video_start_end_model: Optional[str] = None
    video_reference_model: Optional[str] = None
    video_text_model: Optional[str] = None
    video_generation_mode: Optional[str] = "first_frame"
    enable_concurrency: Optional[bool] = True
    web_search: Optional[bool] = False
    episodes: Optional[int] = None
    pilot_mode: Optional[bool] = False
    controlled_episode: Optional[bool] = False


class InterventionRequest(BaseModel):
    stage: str
    modifications: Dict[str, Any]


class ProjectDraftRequest(ProjectStartRequest):
    model_config = ConfigDict(extra="forbid")
    idea: str = Field(default="", max_length=20000)
    title: str = Field(default="", max_length=120)
    episodes: int = Field(default=1, ge=1, le=24, strict=True)
    target_duration: int = Field(default=60, ge=15, le=600, strict=True)
    enable_concurrency: bool = False
    project_container: bool = False

    @field_validator("title", "idea")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()


class ProjectDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    idea: str | None = Field(default=None, max_length=20000)
    style: str | None = Field(default=None, max_length=80)
    video_ratio: str | None = Field(default=None, pattern=r"^(9:16|16:9|1:1|4:3|3:4|21:9|3:2|2:3)$")
    llm_model: str | None = Field(default=None, max_length=160)
    episodes: int | None = Field(default=None, ge=1, le=24, strict=True)
    target_duration: int | None = Field(default=None, ge=15, le=600, strict=True)

    @field_validator("title", "idea", "style", "llm_model", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class ProjectDraftResponse(BaseModel):
    session_id: str
    status: Dict[str, str]
    meta: Dict[str, Any]


class ProjectErrorResponse(BaseModel):
    error: TaskError


class EpisodeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="", max_length=120)
    video_resolution: str = Field(default="720P", pattern=r"^(480P|720P|768P|1080P)$")
    target_duration: int = Field(default=60, ge=15, le=600, strict=True)


class SourceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=200000)
    expected_revision: int = Field(ge=0, strict=True)
    file_path: str | None = None


class ScriptContentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=200000)
    expected_revision: int = Field(ge=0, strict=True)
    use_original: bool = False


class AssetMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str | None = Field(default=None, pattern=r"^(characters|settings|props)$")
    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+$", max_length=120)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, min_length=1, max_length=10000)
    selected: str | None = Field(default=None, max_length=2000)
    delete: bool = False


class ProjectOverviewResponse(BaseModel):
    project: Dict[str, Any]
    episodes: list[Dict[str, Any]]
    assets: list[Dict[str, Any]]


class StudioAssetsResponse(BaseModel):
    artifact: Dict[str, Any]
    status_map: Dict[str, str]


class ScriptContentResponse(StudioAssetsResponse):
    status: str
    meta: Dict[str, Any]


class StudioSegmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_segment: Dict[str, Any]
    segment: Dict[str, Any]


class StudioAssetImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_session_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=120)
    kind: str = Field(pattern=r"^(characters|settings|props)$")
    asset_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$", max_length=120)

"""Public novel workflow responses; all credentials remain server-side."""
from typing import Any, Literal
from pydantic import BaseModel, Field
from core.novel_contracts import Action, Direction, Outline, EpisodeScript, Bible
from api.schemas.task import TaskError


class CallUsage(BaseModel):
    kind: str
    input_characters: int
    status: Literal['sent','completed','error']
    input_tokens: int | None = None
    output_tokens: int | None = None
    elapsed_seconds: float | None = None
    cost: float | None = None
    finish_reason: str | None = None
    reasoning_tokens: int | None = None
    has_reasoning: bool | None = None
    content_characters: int | None = None
    diagnostic: str | None = None
    max_output_tokens: int | None = None
    attempt: int = 1


class NovelPlan(BaseModel):
    action: Literal['analysis','direction','outline','script']
    episode: int | None
    chapter_ids: list[str]
    revision: int
    model: str
    provider_host: str
    input_characters: int
    pending_chunks: int
    merge_requests: int
    max_requests: int
    base_requests: int | None = None
    max_output_tokens: int
    images: int
    video_seconds: int
    retries: int
    cost: float | None
    config_version: str
    signature: str
    plan_hash: str


class NovelJob(BaseModel):
    id: str
    project_id: str
    signature: str
    status: Literal['queued','running','stopping','completed','error','stopped','interrupted']
    created_at: str
    updated_at: str
    action: Action
    plan: NovelPlan
    step: str
    calls: list[CallUsage]
    error: TaskError | None
    recovery_hint: str = ''


class NovelEvent(BaseModel):
    id: str
    description: str
    quote: str
    start: int
    end: int
    chunk_id: str
    chapter_id: str
    chapter_title: str
    chunk_start: int
    chunk_end: int


class ChunkResult(BaseModel):
    summary: str
    characters: list[dict[str,Any]]
    relationships: list[dict[str,Any]]
    events: list[dict[str,Any]]


class ChunkState(BaseModel):
    id: str
    chapter_id: str
    title: str
    start: int
    end: int
    characters: int
    hard_split: bool
    sha256: str
    status: Literal['pending','running','completed','error','interrupted','stopped']
    result: ChunkResult | None


class SavedScript(BaseModel):
    value: EpisodeScript
    created_at: str
    outline_hash: str
    session_id: str | None
    stale: bool = False


class NovelVariant(BaseModel):
    model: str
    chunks: list[ChunkState]
    bible: Bible | None
    direction: Direction | None
    direction_confirmed: bool
    outline: Outline | None
    outline_confirmed: bool
    scripts: dict[str,SavedScript]


class NovelDetail(BaseModel):
    project_id: str
    title: str
    revision: int
    project_revision: int
    model: str
    target_duration: int
    episode_count: int
    binding: dict[str,Any]
    config_warning: str
    stale: bool
    saved_versions: int
    variant: NovelVariant | None
    events: list[NovelEvent]
    jobs: list[NovelJob]


class PublishedEpisode(BaseModel):
    session_id: str

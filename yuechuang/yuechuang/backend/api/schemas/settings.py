from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RevisionRequest(StrictSettings):
    revision: int = Field(ge=1, strict=True)


class ConnectionRequest(RevisionRequest):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["text", "image", "video"]
    provider: Literal["openai", "gemini", "deepseek", "tudou", "toapis", "dashscope", "ark", "kling", "agnes"]
    base_url: str = Field(min_length=1, max_length=500)
    models: list[str] = Field(min_length=1, max_length=100)
    enabled: bool = True
    enable_proxy: bool = False
    credentials: dict[str, str] = Field(default_factory=dict)
    clear_credentials: list[str] = Field(default_factory=list)


class ModelDefaults(StrictSettings):
    llm: str = ""
    vlm: str = ""
    image_t2i: str = ""
    image_it2i: str = ""
    video_first_frame: str = ""
    video_start_end: str = ""
    video_reference: str = ""
    video_text: str = ""


class Defaults(StrictSettings):
    style: Literal["comic-book", "anime", "realistic", "3d-animation", "watercolor", "oil-painting", "cyberpunk", "chinese-ink"]
    video_ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "3:2", "2:3"]
    video_resolution: Literal["480P", "720P", "768P", "1080P", "1K", "2K", "4K"]
    video_generation_mode: Literal["first_frame", "start_end_frame", "reference", "text"]
    target_duration: int = Field(ge=15, le=600, strict=True)
    language: Literal["zh-CN"] = "zh-CN"
    enable_concurrency: bool = False
    models: ModelDefaults


class DefaultsRequest(RevisionRequest):
    defaults: Defaults


class DefaultModelRequest(RevisionRequest):
    model: str = Field(min_length=1, max_length=160)


class ServerSettings(StrictSettings):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535, strict=True)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    access_log: bool


class CommonSettings(StrictSettings):
    print_model_input: bool
    proxy: str = Field(max_length=500)

    @field_validator("proxy")
    @classmethod
    def safe_proxy(cls, value):
        if value:
            from core.service_settings import _url
            return _url(value)
        return ""


class AdvancedRequest(RevisionRequest):
    server: ServerSettings
    common: CommonSettings


class ApplyDefaultsRequest(RevisionRequest):
    expected_project_revision: int = Field(ge=0, strict=True)

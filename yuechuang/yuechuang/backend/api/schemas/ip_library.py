from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from urllib.parse import urlsplit


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class IPMetadata(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    author: str = Field(default='', max_length=120)
    genre: str = Field(default='', max_length=80)
    synopsis: str = Field(default='', max_length=4000)
    rights_status: Literal['pending', 'owned', 'licensed'] = 'pending'
    rights_basis: str = Field(default='', max_length=2000)
    rights_scope: str = Field(default='', max_length=1000)


class ImportMetadata(IPMetadata):
    content_scope: Literal['full', 'excerpt', 'unknown'] = 'unknown'


class MetadataUpdate(IPMetadata):
    expected_revision: int = Field(ge=1, strict=True)


class ChapterBoundary(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    start_line: int = Field(ge=1, strict=True)


class ChaptersUpdate(StrictModel):
    version_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    expected_revision: int = Field(ge=1, strict=True)
    chapters: list[ChapterBoundary] = Field(min_length=1, max_length=10000)


class AdaptProject(StrictModel):
    version_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    expected_revision: int = Field(ge=1, strict=True)
    title: str = Field(min_length=1, max_length=120)
    first_chapter: int = Field(ge=1, strict=True)
    last_chapter: int = Field(ge=1, strict=True)
    direction: str = Field(min_length=1, max_length=4000)
    episodes: int = Field(default=1, ge=1, le=24, strict=True)
    target_duration: int = Field(default=60, ge=15, le=600, strict=True)
    style: Literal['comic-book', 'anime', 'realistic', '3d-animation', 'watercolor', 'oil-painting', 'cyberpunk', 'chinese-ink'] | None = None
    video_ratio: Literal['16:9', '9:16', '1:1', '4:3', '3:4', '21:9', '3:2', '2:3'] | None = None


class ChapterInfo(BaseModel):
    id: str
    title: str
    start: int
    end: int
    start_line: int
    characters: int


class VersionInfo(BaseModel):
    id: str
    number: int
    created_at: str
    filename: str
    sha256: str
    text_sha256: str
    content_scope: str
    characters: int
    line_count: int
    encoding: str
    warnings: list[str]
    chapters: list[ChapterInfo]


class IPCatalog(StrictModel):
    """Read-only provenance for locally cached catalog records, never novel rights."""
    source: str = Field(min_length=1, max_length=80)
    source_url: str = Field(max_length=2048)
    source_id: str = Field(min_length=1, max_length=120)
    position: int = Field(ge=1, le=50, strict=True)
    fetched_at: str = Field(min_length=1, max_length=40)
    cover_path: str = Field(default='', pattern=r'^(|/ip-covers/[a-zA-Z0-9_-]+\.(jpg|jpeg|png|webp))$')

    @field_validator('source_url')
    @classmethod
    def source_is_cmread(cls, value):
        url = urlsplit(value)
        if url.scheme != 'https' or url.netloc != 'www.cmread.com':
            raise ValueError('仅接受已支持的作品来源地址')
        return value


class IPItem(IPMetadata):
    catalog: IPCatalog | None = None
    cover_path: str = Field(default='', pattern=r'^(|/ip-covers/[a-zA-Z0-9_-]+\.(jpg|jpeg|png|webp))$')
    featured: bool = False
    id: str
    revision: int
    created_at: str
    updated_at: str
    latest_version: str | None
    version_count: int
    characters: int
    chapter_count: int
    content_scope: str
    availability: Literal['ready', 'missing', 'restricted']
    restriction: str


class IPDetail(BaseModel):
    item: IPItem
    versions: list[VersionInfo]
    projects: list[dict]


class IPList(BaseModel):
    items: list[IPItem]
    total: int
    genres: list[str]


class ChapterText(BaseModel):
    chapter: ChapterInfo
    text: str
    offset: int
    first_line: int
    next_offset: int | None
    total: int


class LibraryError(BaseModel):
    code: str
    message: str


class LibraryErrorResponse(BaseModel):
    error: LibraryError

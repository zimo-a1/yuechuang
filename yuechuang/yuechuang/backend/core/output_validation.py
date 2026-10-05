"""Typed boundaries for script/storyboard output. Never expose model text in errors."""

import json
import re
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
)
from core.shot_control import ShotControl, validate_shot_control


class ModelOutputError(ValueError):
    code = 'MODEL_OUTPUT_INVALID'
    message = '模型输出结构不符合要求，已停止本次生成；请重试或修改输入'

    def __init__(self, diagnostic='invalid_structure'):
        self.diagnostic = diagnostic
        super().__init__(self.message)


def _number(value):
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Number = Annotated[int, BeforeValidator(_number), Field(strict=True, gt=0)]
UnitId = Annotated[str, StringConstraints(pattern=r'^U\d{3,}$')]


class OutputModel(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)


class NamedAsset(OutputModel):
    name: Text
    description: Text


class CharacterAsset(NamedAsset):
    character_id: str = ''
    role: str = ''
    species: str = ''
    personality: list[str] = Field(default_factory=list)
    motivation: str = ''
    arc_description: str = ''
    occupation: str = ''
    age: str | int = ''


class SettingAsset(NamedAsset):
    setting_id: str = ''


class ScriptMetadata(OutputModel):
    title: Text
    characters: list[CharacterAsset]
    settings: list[SettingAsset]
    logline: str = ''
    genre: list[str] = Field(default_factory=list)
    mood: str = ''


class SequelMetadata(OutputModel):
    new_characters: list[CharacterAsset]
    new_settings: list[SettingAsset]


class ScriptEpisode(OutputModel):
    episode_number: Number
    content: Text
    act_title: str = ''


class ScriptOutput(ScriptMetadata):
    episodes: Annotated[list[ScriptEpisode], Field(min_length=1)]


class PlanItem(OutputModel):
    unit_ids: Annotated[list[UnitId], Field(min_length=1)]


class DesignedShot(OutputModel):
    unit_id: UnitId
    content: Text
    shot_type: Text


class SegmentDesign(OutputModel):
    shots: Annotated[list[DesignedShot], Field(min_length=1)]


class ContinuityPatch(OutputModel):
    segment_number: Number
    shot_number: Number
    content: Text


class ContinuityReview(OutputModel):
    patches: list[ContinuityPatch]
    issues: list[str] = Field(default_factory=list)


class SavedShot(OutputModel):
    shot_number: Number
    shot_type: Text
    duration: Number
    content: Text


class SavedSegment(OutputModel):
    segment_id: Text
    segment_number: Number
    total_duration: Number
    location: str = ''
    characters: list[str] = Field(default_factory=list)
    shots: Annotated[list[SavedShot], Field(min_length=1)]
    shot_control: ShotControl | None = None


class StoryboardEpisode(OutputModel):
    episode_number: Number
    episode_title: str = ''
    segments: Annotated[list[SavedSegment], Field(min_length=1)]


def _unique(values):
    if len(values) != len(set(values)):
        raise ModelOutputError('duplicate_identifiers')


def validate_as(schema, value):
    adapter = TypeAdapter(schema)
    try:
        parsed = adapter.validate_python(value)
    except ValidationError as exc:
        # No input values, custom field names, URLs or provider exception messages.
        codes = sorted({error['type'] for error in exc.errors(include_input=False, include_url=False)})
        raise ModelOutputError('schema:' + ','.join(codes)) from None
    return adapter.dump_python(parsed, mode='json', exclude_unset=True)


def decode_json(text: Any):
    if not isinstance(text, str) or not text.strip():
        raise ModelOutputError('empty_response')
    text = text.strip().lstrip('\ufeff')
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
    text = re.sub(r'\s*```$', '', text)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError('non-finite number')

    start = re.search(r'[\[{]', text)
    if not start:
        raise ModelOutputError('json_required')
    try:
        result, end = json.JSONDecoder(object_pairs_hook=pairs, parse_constant=invalid_constant).raw_decode(text[start.start():])
    except (ValueError, RecursionError):
        raise ModelOutputError('invalid_json') from None
    tail = text[start.start() + end:].strip()
    if re.search(r'[\[{]', tail):
        raise ModelOutputError('multiple_json_documents')
    return result


def validate_episodes(value):
    if isinstance(value, dict):
        # Only known wrappers are compatible; arbitrary first-property extraction loses data.
        if 'episodes' in value and 'new_episodes' in value:
            raise ModelOutputError('ambiguous_episode_wrapper')
        value = value.get('episodes', value.get('new_episodes'))
    episodes = validate_as(Annotated[list[ScriptEpisode], Field(min_length=1)], value)
    _unique([item['episode_number'] for item in episodes])
    for episode in episodes:
        episode['act_title'] = episode.get('act_title') or f"第{episode['episode_number']}集"
    return episodes


def validate_script(value):
    output = validate_as(ScriptOutput, value)
    output['episodes'] = validate_episodes(output['episodes'])
    return output


def validate_plan(value):
    result = validate_as(Annotated[list[PlanItem], Field(min_length=1)], value)
    _unique([unit for item in result for unit in item['unit_ids']])
    return result


def validate_design(value):
    result = validate_as(SegmentDesign, value)
    _unique([shot['unit_id'] for shot in result['shots']])
    return result


def validate_review(value):
    result = validate_as(ContinuityReview, value)
    _unique([(patch['segment_number'], patch['shot_number']) for patch in result['patches']])
    return result


def validate_storyboard(value):
    episodes = validate_as(Annotated[list[StoryboardEpisode], Field(min_length=1)], value)
    _unique([ep['episode_number'] for ep in episodes])
    _unique([seg['segment_id'] for ep in episodes for seg in ep['segments']])
    for ep in episodes:
        _unique([seg['segment_number'] for seg in ep['segments']])
        for segment in ep['segments']:
            if segment.get('shot_control') is not None:
                validate_shot_control(segment)
            _unique([shot['shot_number'] for shot in segment['shots']])
            if segment['total_duration'] != sum(shot['duration'] for shot in segment['shots']):
                raise ModelOutputError('duration_sum_mismatch')
    return episodes

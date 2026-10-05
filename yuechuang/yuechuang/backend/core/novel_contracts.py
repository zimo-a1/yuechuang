"""Novel adaptation contracts and deterministic provenance checks."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from core.output_validation import ModelOutputError

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Person(Contract):
    name: Name
    aliases: list[Name] = Field(max_length=20)
    description: Text


class Relationship(Contract):
    source: Name
    target: Name
    description: Text


class ExtractedEvent(Contract):
    description: Text
    quote: Annotated[str, StringConstraints(min_length=2, max_length=180)]


class ChunkAnalysis(Contract):
    summary: Text
    characters: list[Person] = Field(max_length=30)
    relationships: list[Relationship] = Field(max_length=50)
    events: list[ExtractedEvent] = Field(max_length=30)


class Bible(Contract):
    summary: Text
    characters: list[Person] = Field(max_length=100)
    relationships: list[Relationship] = Field(max_length=150)
    open_threads: list[Text] = Field(max_length=40)


class Direction(Contract):
    premise: Text
    tone: Text
    focus_characters: list[Name] = Field(min_length=1, max_length=30)
    changes: Text


class EpisodeOutline(Contract):
    number: int = Field(ge=1, le=24)
    title: Name
    summary: Text
    characters: list[Name] = Field(min_length=1, max_length=30)
    event_ids: list[Name] = Field(min_length=1, max_length=100)
    creative_additions: str = Field(max_length=2000)


class Omission(Contract):
    event_id: Name
    reason: Text


class Outline(Contract):
    episodes: list[EpisodeOutline] = Field(min_length=1, max_length=24)
    omissions: list[Omission] = Field(max_length=3000)


class Dialogue(Contract):
    speaker: Name
    text: Text
    creative_addition: bool


class Scene(Contract):
    location: Name
    characters: list[Name] = Field(min_length=1, max_length=30)
    duration: int = Field(ge=1, le=600)
    action: Text
    dialogue: list[Dialogue] = Field(max_length=50)
    event_ids: list[Name] = Field(max_length=100)
    creative_addition: bool


class EpisodeScript(Contract):
    title: Name
    scenes: list[Scene] = Field(min_length=1, max_length=30)


class Action(Contract):
    action: Literal['analysis', 'direction', 'outline', 'script']
    chapter_ids: list[Name] = Field(default_factory=list, max_length=10000)
    episode: int | None = Field(default=None, ge=1, le=24)


class Start(Action):
    expected_revision: int = Field(ge=0)
    plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    acknowledge_external: Literal[True]


class DirectionSave(Contract):
    expected_revision: int = Field(ge=0)
    value: Direction
    confirm: bool = False


class OutlineSave(Contract):
    expected_revision: int = Field(ge=0)
    value: Outline
    confirm: bool = False


class Revision(Contract):
    expected_revision: int = Field(ge=0)


def require(condition, diagnostic):
    if not condition:
        raise ModelOutputError(diagnostic)


def names(bible):
    return {p['name'] for p in bible['characters']}


def validate_people(value, allowed=None, allow_shared_aliases=False):
    canonical = [p['name'] for p in value['characters']]
    require(len(canonical) == len(set(canonical)), 'duplicate_people')
    for person in value['characters']:
        person['aliases'] = list(dict.fromkeys(n for n in person['aliases'] if n != person['name']))
    all_names = [n for p in value['characters'] for n in [p['name'], *p['aliases']]]
    if not allow_shared_aliases:
        require(len(all_names) == len(set(all_names)), 'ambiguous_aliases')
    if allowed is not None:
        require(set(all_names) == allowed, 'missing_or_invented_people')
    alias_owners = {}
    for person in value['characters']:
        for alias in person['aliases']:
            alias_owners.setdefault(alias, set()).add(person['name'])
    for relation in value['relationships']:
        for endpoint in ('source', 'target'):
            owners = alias_owners.get(relation[endpoint], set())
            if relation[endpoint] not in canonical and len(owners) == 1:
                relation[endpoint] = next(iter(owners))
    require(all(r['source'] in canonical and r['target'] in canonical for r in value['relationships']), 'unknown_relationship')
    return value


def validate_direction(value, bible):
    require(set(value['focus_characters']) <= names(bible), 'unknown_focus_character')
    return value


def validate_outline(value, bible, events, count):
    require([e['number'] for e in value['episodes']] == list(range(1, count+1)), 'episode_numbers_mismatch')
    used = set()
    for ep in value['episodes']:
        require(set(ep['characters']) <= names(bible), 'unknown_episode_character')
        require(len(ep['event_ids']) == len(set(ep['event_ids'])), 'duplicate_episode_events')
        used.update(ep['event_ids'])
    omitted = [o['event_id'] for o in value['omissions']]
    require(len(omitted) == len(set(omitted)), 'duplicate_omissions')
    require(not used.intersection(omitted), 'used_event_marked_omitted')
    require(used | set(omitted) == {e['id'] for e in events}, 'events_unaccounted_or_invented')
    return value


def validate_episode(value, outline, duration, events, text):
    seen = set()
    require(sum(s['duration'] for s in value['scenes']) == duration, 'duration_sum_mismatch')
    for scene in value['scenes']:
        require(set(scene['characters']) <= set(outline['characters']), 'unknown_scene_character')
        require(set(scene['event_ids']) <= set(outline['event_ids']), 'unknown_scene_event')
        require(scene['event_ids'] or scene['creative_addition'], 'unattributed_scene')
        seen.update(scene['event_ids'])
        for line in scene['dialogue']:
            require(line['speaker'] in scene['characters'], 'unknown_speaker')
            if not line['creative_addition']:
                chunks = [e for e in events if e['id'] in scene['event_ids']]
                require(any(line['text'] in text[e['chunk_start']:e['chunk_end']] for e in chunks), 'unmarked_new_dialogue')
    require(seen == set(outline['event_ids']), 'missing_planned_events')
    return value

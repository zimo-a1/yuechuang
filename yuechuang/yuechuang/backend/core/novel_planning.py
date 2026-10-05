"""Compact model contracts; saved outlines retain complete source accounting."""
import copy

from pydantic import Field
from core.novel_contracts import Contract, Text, EpisodeOutline, Omission, Outline, require, validate_outline
from core.output_validation import validate_as


class OutlineDraft(Contract):
    episodes: list[EpisodeOutline] = Field(min_length=1, max_length=24)
    # The model explicitly applies this reason to every unselected source event.
    # Per-event exceptions remain available; no reason is fabricated in code.
    omitted_event_reason: Text
    omissions: list[Omission] = Field(max_length=3000)


def event_catalog(events):
    return [{'id':f'E{i:04d}', 'chapter':e['chapter_title'], 'description':e['description']}
            for i,e in enumerate(events, 1)]


def outline_limits(duration):
    return {'max_events_per_episode':min(100, max(1, (duration+7)//8)),
            'max_characters_per_episode':min(30, max(2, (duration+14)//15))}


def expand_outline(draft, bible, events, count, duration=60):
    refs = {short['id']:event['id'] for short,event in zip(event_catalog(events), events)}
    episodes = copy.deepcopy(draft['episodes'])
    require([e['number'] for e in episodes] == list(range(1, count+1)), 'episode_numbers_mismatch')
    limits = outline_limits(duration)
    for episode in episodes:
        require(len(episode['event_ids']) <= limits['max_events_per_episode'], 'outline_event_capacity')
        require(len(episode['characters']) <= limits['max_characters_per_episode'], 'outline_character_capacity')
        require(all(ref in refs for ref in episode['event_ids']), 'unknown_outline_event')
        episode['event_ids'] = [refs[ref] for ref in episode['event_ids']]
    overrides = draft['omissions']
    require(all(o['event_id'] in refs for o in overrides), 'unknown_outline_event')
    require(len({o['event_id'] for o in overrides}) == len(overrides), 'duplicate_omissions')
    reasons = {refs[o['event_id']]:o['reason'] for o in overrides}
    used = {ref for episode in episodes for ref in episode['event_ids']}
    require(not used.intersection(reasons), 'used_event_marked_omitted')
    result = {'episodes':episodes, 'omissions':[
        {'event_id':e['id'], 'reason':reasons.get(e['id'], draft['omitted_event_reason'])}
        for e in events if e['id'] not in used]}
    return validate_outline(validate_as(Outline, result), bible, events, count)


def planning_schema(kind, schema, data):
    spec = schema.model_json_schema()
    if kind == 'outline':
        count = data['episode_count']
        spec['properties']['episodes'].update(minItems=count, maxItems=count,
            description=f'只能有 {count} 集，number 必须依次为 {list(range(1, count+1))}')
        spec['$defs']['EpisodeOutline']['properties']['number']['maximum'] = count
        spec['$defs']['EpisodeOutline']['properties']['event_ids']['items']['enum'] = [e['id'] for e in data['events']]
        limits = outline_limits(data['target_duration'])
        spec['$defs']['EpisodeOutline']['properties']['event_ids']['maxItems'] = limits['max_events_per_episode']
        spec['$defs']['EpisodeOutline']['properties']['characters']['maxItems'] = limits['max_characters_per_episode']
    return spec

"""Reconcile generated dialogue metadata against the approved cast and source."""
import copy

from pydantic import Field
from core.novel_contracts import (Contract, Name, Text, Scene, EpisodeScript,
                                  require, validate_episode)
from core.output_validation import validate_as


class WrittenDialogue(Contract):
    speaker: Name
    text: Text


class WrittenScene(Scene):
    dialogue: list[WrittenDialogue] = Field(max_length=50)


class WrittenEpisode(EpisodeScript):
    scenes: list[WrittenScene] = Field(min_length=1, max_length=30)


def finalize_script(draft, outline, duration, events, text, bible):
    result = copy.deepcopy(draft)
    allowed = set(outline['characters'])
    # Only unique aliases of approved characters are equivalent to their names.
    # Never infer an identity from a generic narrator/role or a performance suffix.
    owners = {}
    canonical_names = {p['name'] for p in bible['characters']}
    for person in bible['characters']:
        for alias in person['aliases']:
            owners.setdefault(alias, set()).add(person['name'])

    def canonical(name):
        candidates = owners.get(name, set())
        if name not in canonical_names and len(candidates) == 1 and candidates <= allowed:
            return next(iter(candidates))
        return name

    source = {e['id']:text[e['chunk_start']:e['chunk_end']] for e in events}
    for scene in result['scenes']:
        scene['characters'] = list(dict.fromkeys(canonical(n) for n in scene['characters']))
        require(set(scene['characters']) <= allowed, 'unknown_scene_character')
        require(set(scene['event_ids']) <= set(outline['event_ids']), 'unknown_scene_event')
        for line in scene['dialogue']:
            line['speaker'] = canonical(line['speaker'])
            require(line['speaker'] in allowed, 'unknown_speaker')
            # The scene cast is a manifest of participating characters, including
            # an approved character speaking off screen. Do not drop their line.
            if line['speaker'] not in scene['characters']:
                scene['characters'].append(line['speaker'])
            # A generated claim cannot make a rewritten line an original quote.
            # Exact wording must occur in a source chunk attributed to this scene.
            line['creative_addition'] = not any(line['text'] in source.get(ref, '') for ref in scene['event_ids'])
    result = validate_as(EpisodeScript, result)
    return validate_episode(result, outline, duration, events, text)


def script_schema(spec, data):
    selected = data['selected_episode']
    spec['$defs']['WrittenDialogue']['properties']['speaker']['enum'] = selected['characters']
    spec['$defs']['WrittenScene']['properties']['characters']['items']['enum'] = selected['characters']
    spec['$defs']['WrittenScene']['properties']['event_ids']['items']['enum'] = selected['event_ids']
    return spec

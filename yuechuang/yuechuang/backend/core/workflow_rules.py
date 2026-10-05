"""Explicit human confirmation is independent of generated artifact completeness."""

STAGES = ('script_generation', 'character_design', 'storyboard',
          'reference_generation', 'video_generation', 'post_production')


class WorkflowConflict(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def require_predecessors(state, stage):
    if stage not in STAGES:
        raise ValueError('无效的生成阶段')
    if any(previous not in state.confirmed_stages for previous in STAGES[:STAGES.index(stage)]):
        raise WorkflowConflict('CONFIRMATION_REQUIRED', '请先检查并确认前面的阶段，再开始本阶段')


def invalidate_confirmations(state, stage):
    if stage in STAGES:
        previous = STAGES[:STAGES.index(stage)]
        state.confirmed_stages = [item for item in state.confirmed_stages if item in previous]


def _stage_ready(state, stage):
    art = state.artifacts.get(stage) or {}
    if not isinstance(art, dict):
        return False
    if stage == 'script_generation':
        episodes = art.get('episodes') or []
        return bool(episodes) and not art.get('new_episodes') and all(ep.get('content') for ep in episodes)
    if stage == 'storyboard':
        episodes = art.get('episodes') or []
        expected = {ep.get('episode_number') for ep in (state.artifacts.get('script_generation') or {}).get('episodes', [])}
        return bool(episodes) and expected.issubset({ep.get('episode_number') for ep in episodes}) and all(ep.get('segments') and all(
            seg.get('segment_id') and seg.get('shots') for seg in ep['segments']) for ep in episodes)
    if stage == 'character_design':
        items = (art.get('characters') or []) + (art.get('settings') or []) + (art.get('props') or [])
        script = state.artifacts.get('script_generation') or {}
        for key, id_key in [('characters', 'character_id'), ('settings', 'setting_id'), ('props', 'prop_id')]:
            source = script.get(key) or []
            generated = art.get(key) or []
            expected = {item.get(id_key) or item.get('id') for item in source} - {None}
            if len(generated) < len(source) or not expected.issubset({item.get('id') for item in generated}):
                return False
        return bool(items) and all(item.get('selected') for item in items)
    if stage in ('reference_generation', 'video_generation'):
        key = 'scenes' if stage == 'reference_generation' else 'clips'
        items = art.get(key) or []
        expected = {seg['segment_id'] for ep in (state.artifacts.get('storyboard') or {}).get('episodes', [])
                    for seg in ep.get('segments', []) if seg.get('segment_id')}
        selected = {item.get('id') for item in items if item.get('selected')}
        return bool(items) and expected.issubset(selected) and all(item.get('selected') for item in items)
    if stage == 'post_production':
        videos = art.get('final_videos') or []
        expected = {ep.get('episode_number') for ep in (state.artifacts.get('storyboard') or {}).get('episodes', [])}
        if videos:
            return expected.issubset({v.get('episode') for v in videos}) and all(v.get('path') for v in videos)
        return bool(art.get('final_video')) and len(expected) <= 1
    return False


def stage_ready(state, stage):
    # Malformed manual edits are review failures, never internal server errors.
    try:
        return bool(_stage_ready(state, stage))
    except (AttributeError, TypeError, KeyError):
        return False

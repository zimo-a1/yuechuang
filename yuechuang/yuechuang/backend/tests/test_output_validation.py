import asyncio
import copy
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.agents.base_agent import AgentInterface
from core.agents.script_agent import ScriptWriterAgent
from core.agents.storyboard_agent import StoryboardAgent
from core.orchestrator import WorkflowEngine, WorkflowStage
from core.output_validation import (
    ModelOutputError,
    ScriptMetadata,
    decode_json,
    validate_as,
    validate_design,
    validate_episodes,
    validate_plan,
    validate_script,
    validate_storyboard,
)
from core.structured_query import max_output_attempts, query_structured
from core.tasks import TaskManager

META = {'title': '测试故事', 'characters': [{'name': '阿青', 'description': '穿蓝色外套'}],
        'settings': [{'name': '客厅', 'description': '明亮的客厅'}]}
TEXT = '# 第1集\n1-1 日 内 客厅\n阿青：我们走。'
EPISODES = [{'episode_number': 1, 'act_title': '出发', 'content': TEXT}]
SCRIPT = {**META, 'episodes': EPISODES}


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


class FakeLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def query(self, prompt, *args):
        self.calls.append(prompt)
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize('text', [encoded(META), '```json\n' + encoded(META) + '\n```',
                                  '以下是结果\n' + encoded(META) + '\n提取完毕'])
def test_wrapped_json_is_accepted_without_losing_fields(text):
    assert validate_as(ScriptMetadata, decode_json(text)) == META


@pytest.mark.parametrize('value', ['', None, '{broken', '{"a":1,"a":2}',
                                   '{"a": NaN}', '{"a":1} {"b":2}', '[{"a":1}'])
def test_malformed_or_ambiguous_json_is_rejected(value):
    with pytest.raises(ModelOutputError):
        decode_json(value)


@pytest.mark.parametrize('episodes', [[], [{'episode_number': True, 'content': 'x'}],
                                    [{'episode_number': 1.2, 'content': 'x'}],
                                    [{'episode_number': 0, 'content': 'x'}],
                                    [{'episode_number': 1, 'content': '  '}],
                                    [{'episode_number': 1, 'content': {'secret': 'text'}}],
                                    EPISODES + EPISODES, [None]])
def test_bad_episode_structure_cannot_be_saved(episodes):
    with pytest.raises(ModelOutputError):
        validate_episodes(episodes)


def test_compatible_wrappers_numeric_strings_and_extra_fields():
    result = validate_episodes({'new_episodes': [{'episode_number': '2', 'content': '正文', 'note': '保留'}]})
    assert result == [{'episode_number': 2, 'content': '正文', 'act_title': '第2集', 'note': '保留'}]
    with pytest.raises(ModelOutputError):
        validate_episodes({'unknown': EPISODES})
    with pytest.raises(ModelOutputError):
        validate_episodes({'episodes': EPISODES, 'new_episodes': EPISODES})


def test_validation_errors_do_not_expose_model_content():
    bad = {**META, 'characters': 'PRIVATE_INPUT_DO_NOT_LOG'}
    with pytest.raises(ModelOutputError) as error:
        validate_as(ScriptMetadata, bad)
    assert 'PRIVATE_INPUT_DO_NOT_LOG' not in str(error.value)
    assert 'PRIVATE_INPUT_DO_NOT_LOG' not in error.value.diagnostic


@pytest.mark.parametrize(('configured', 'expected'), [('0', 1), ('2', 2), ('999', 3), ('bad', 3)])
def test_retry_limit_is_clamped(monkeypatch, configured, expected):
    monkeypatch.setenv('YC_STRUCTURED_MAX_ATTEMPTS', configured)
    assert max_output_attempts() == expected


async def test_structure_retry_repairs_only_failed_extraction(caplog):
    prompts = []
    responses = iter([encoded({'title': 'PRIVATE_RESPONSE_DO_NOT_LOG'}), encoded(META)])
    async def query(prompt):
        prompts.append(prompt)
        return next(responses)
    result = await query_structured(query, 'original', lambda v: validate_as(ScriptMetadata, v),
                                    check_cancel=lambda: None, label='metadata')
    assert result == META
    assert len(prompts) == 2
    assert 'schema:' in prompts[1]
    assert 'PRIVATE_RESPONSE_DO_NOT_LOG' not in caplog.text
    assert 'PRIVATE_RESPONSE_DO_NOT_LOG' not in prompts[1]


async def test_retry_exhaustion_is_finite():
    calls = 0
    async def query(prompt):
        nonlocal calls
        calls += 1
        return '{}'
    with pytest.raises(ModelOutputError):
        await query_structured(query, 'prompt', validate_episodes, check_cancel=lambda: None, label='episodes')
    assert calls == 3


async def test_transport_error_is_not_retried_by_output_layer():
    calls = 0
    async def query(prompt):
        nonlocal calls
        calls += 1
        raise TimeoutError('provider detail')
    with pytest.raises(TimeoutError):
        await query_structured(query, 'prompt', validate_episodes, check_cancel=lambda: None, label='episodes')
    assert calls == 1


async def test_stop_during_response_prevents_any_repair():
    agent = ScriptWriterAgent()
    stopped = threading.Event()
    agent.set_cancellation_check(stopped.is_set)
    class LLM:
        calls = 0
        def query(self, *args):
            self.calls += 1
            stopped.set()
            return '{}'
    llm = LLM()
    with pytest.raises(RuntimeError, match='cancelled'):
        await agent._query_structured('prompt', validate_episodes, model='mock', sid='test', label='test', llm=llm)
    assert llm.calls == 1


async def test_sdk_construction_failure_is_not_retried(monkeypatch):
    factory = Mock(side_effect=RuntimeError('bad SDK configuration'))
    monkeypatch.setattr('models.llm_client.LLM', factory)
    with pytest.raises(RuntimeError):
        await ScriptWriterAgent()._query_structured('prompt', validate_episodes, model='mock', sid='test', label='test')
    assert factory.call_count == 1


async def test_script_process_repairs_metadata_and_episodes_before_save(monkeypatch):
    fake = FakeLLM([TEXT, '评价', '评价', TEXT, '{}', encoded(META), '[]', encoded({'episodes': EPISODES})])
    monkeypatch.setattr('models.llm_client.LLM', lambda: fake)
    agent = ScriptWriterAgent()
    saved = []
    monkeypatch.setattr(agent, '_save_result', lambda data, *args: saved.append(copy.deepcopy(data)))
    result = await agent.process({'session_id': 'test', 'idea': '中文故事', 'llm_model': 'mock', 'episodes': 1})
    assert len(fake.calls) == 8
    assert len(saved) == 1
    assert result['payload']['episodes'] == EPISODES
    assert validate_script(saved[0]) == saved[0]


async def test_script_failure_never_writes_an_empty_success(monkeypatch):
    fake = FakeLLM([TEXT, '评价', '评价', TEXT, '{}', '{}', '{}'])
    monkeypatch.setattr('models.llm_client.LLM', lambda: fake)
    agent = ScriptWriterAgent()
    saved = Mock()
    monkeypatch.setattr(agent, '_save_result', saved)
    with pytest.raises(ModelOutputError):
        await agent.process({'session_id': 'test', 'idea': '中文故事', 'llm_model': 'mock', 'episodes': 1})
    saved.assert_not_called()
    assert len(fake.calls) == 7


async def test_sequel_keeps_numbering_and_requires_confirmation(monkeypatch):
    sequel = '# 第2集\n阿青：我们回来了。'
    new_episodes = [{'episode_number': 2, 'content': sequel}]
    fake = FakeLLM([sequel, '评价', '评价', sequel,
                   encoded({'new_characters': [], 'new_settings': []}), encoded(new_episodes)])
    monkeypatch.setattr('models.llm_client.LLM', lambda: fake)
    agent = ScriptWriterAgent()
    monkeypatch.setattr(agent, '_save_result', Mock())
    result = await agent.process({**SCRIPT, 'session_id': 'test', 'llm_model': 'mock'},
                                 {'action': 'smart_continue', 'sequel_idea': '归来', 'episodes_to_add': 1})
    assert result['requires_intervention'] is True
    assert result['payload']['new_episodes'][0]['episode_number'] == 2
    assert result['payload']['episodes'] == EPISODES


async def test_invalid_manual_script_is_rejected_without_model_call(monkeypatch):
    factory = Mock(side_effect=AssertionError('must not call provider'))
    monkeypatch.setattr('models.llm_client.LLM', factory)
    with pytest.raises(ModelOutputError):
        await ScriptWriterAgent().process({'session_id': 'test'}, {'modified_script': '{}'})
    factory.assert_not_called()


def storyboard_fixture():
    return [{'episode_number': 1, 'segments': [{
        'segment_id': 'seg_01_01', 'segment_number': 1, 'total_duration': 5,
        'location': '客厅', 'characters': ['阿青'],
        'shots': [{'shot_number': 1, 'shot_type': '中景', 'duration': 5, 'content': '阿青站在窗前。'}],
    }]}]


def test_storyboard_rejects_duration_mismatch_and_duplicate_references():
    board = storyboard_fixture()
    board[0]['segments'][0]['total_duration'] = 10
    with pytest.raises(ModelOutputError):
        validate_storyboard(board)
    with pytest.raises(ModelOutputError):
        validate_plan([{'unit_ids': ['U001', 'U001']}])
    shot = {'unit_id': 'U001', 'shot_type': '中景', 'content': '描述'}
    with pytest.raises(ModelOutputError):
        validate_design({'shots': [shot, shot]})
    with pytest.raises(ModelOutputError):
        validate_design({'shots': [{**shot, 'content': {}}]})


def test_continuity_patches_are_all_or_nothing():
    segments = storyboard_fixture()[0]['segments']
    original = copy.deepcopy(segments)
    review = {'patches': [{'segment_number': 1, 'shot_number': 1, 'content': '改动'},
                          {'segment_number': 9, 'shot_number': 1, 'content': '未知片段'}]}
    with pytest.raises(ValueError):
        StoryboardAgent._apply_staging_continuity_patches(segments, review)
    assert segments == original


async def test_plan_repairs_business_constraints_with_one_retry_loop(monkeypatch):
    agent = StoryboardAgent()
    _, units = agent._annotate_episode_script('1-1 日 内 客厅\n阿青：我们走。', META['characters'], META['settings'])
    good = [{'unit_ids': [unit['unit_id'] for unit in units]}]
    fake = FakeLLM([encoded([{'unit_ids': ['U999']}]), encoded(good)])
    monkeypatch.setattr('models.llm_client.LLM', lambda: fake)
    plans = await agent._plan_episode_segments(1, '出发', '剧本', units, META['characters'], META['settings'], 'mock', 'test')
    assert len(fake.calls) == 2
    assert plans[0]['items'] == units
    assert not agent.validation_warnings


async def test_invalid_design_uses_explicit_validated_fallback(monkeypatch):
    fake = FakeLLM(['{}', '{}', '{}'])
    monkeypatch.setattr('models.llm_client.LLM', lambda: fake)
    agent = StoryboardAgent()
    _, units = agent._annotate_episode_script('1-1 日 内 客厅\n阿青：我们走。', META['characters'], META['settings'])
    plan = agent._segment_plan_from_units(1, 1, units)
    segment = await agent._design_one_segment(1, '出发', plan, 'realistic', 'mock', 'test')
    validate_storyboard([{'episode_number': 1, 'segments': [segment]}])
    assert agent.validation_warnings[0]['code'] == 'DETERMINISTIC_SHOT_DESIGN'
    assert len(fake.calls) == 3


async def test_storyboard_provider_failure_does_not_become_fallback(monkeypatch):
    fake = FakeLLM([TimeoutError('provider failed')])
    monkeypatch.setattr('models.llm_client.LLM', lambda: fake)
    agent = StoryboardAgent()
    with pytest.raises(TimeoutError):
        await agent._fix_episode_staging_continuity(1, 'title', storyboard_fixture()[0]['segments'], 'mock', 'test')
    assert not agent.validation_warnings
    assert len(fake.calls) == 1


@pytest.mark.parametrize('kind', ['gpt', 'gemini'])
@pytest.mark.parametrize('response', [SimpleNamespace(choices=[]),
                                     SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='  '))])])
def test_empty_provider_responses_exhaust_attempts(kind, response, monkeypatch):
    from models.llm_gemini import Gemini
    from models.llm_gpt import GPT
    cls = GPT if kind == 'gpt' else Gemini
    client = cls.__new__(cls)
    create = Mock(return_value=response)
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client.max_attempts, client.max_tokens = 3, 100
    sleep = Mock()
    monkeypatch.setattr('time.sleep', sleep)
    with pytest.raises(Exception, match='Max attempts|最大重试'):
        client.query('private prompt', model='mock')
    assert create.call_count == 3
    assert sleep.call_count == 2


async def test_invalid_output_is_persisted_as_specific_task_error(tmp_path):
    engine = WorkflowEngine(session_dir=str(tmp_path / 'legacy'), database_path=tmp_path / 'db.sqlite3')
    engine.create_session('test', {})
    engine.update_artifact('test', 'script_generation', SCRIPT)
    class BrokenAgent(AgentInterface):
        async def process(self, input_data, intervention=None):
            validate_script({'title': 'PRIVATE_RESPONSE'})
    engine.agent_factories[WorkflowStage.SCRIPT_GENERATION] = BrokenAgent
    manager = TaskManager(engine)
    try:
        job = manager.submit('test', 'script_generation', {})
        for _ in range(100):
            result = manager.get('test', job['task_id'])
            if result['status'] == 'error':
                break
            await asyncio.sleep(0.01)
        assert result['error']['code'] == 'MODEL_OUTPUT_INVALID'
        assert engine.get_state('test').artifacts['script_generation'] == SCRIPT
        assert engine.get_state('test').status['script_generation'] == 'error'
        assert 'PRIVATE_RESPONSE' not in encoded(result)
    finally:
        await manager.shutdown()
        engine.store.close()


async def test_full_storyboard_fallback_requires_review_and_persists_warning(monkeypatch):
    agent = StoryboardAgent()
    _, units = agent._annotate_episode_script(TEXT, META['characters'], META['settings'])
    # Stage 23: segment boundaries are planned by code, so the episode makes no
    # segment-plan request: 3 bounded design attempts + 1 continuity review.
    fake = FakeLLM(['{}', '{}', '{}', encoded({'patches': [], 'issues': []})])
    monkeypatch.setattr('models.llm_client.LLM', lambda: fake)
    events = []
    agent.set_progress_callback(lambda *args: events.append(args))
    result = await agent.process({'session_id': 'test', 'llm_model': 'mock',
                                  '_session_artifacts': {'script_generation': SCRIPT}})
    assert result['requires_intervention'] is True
    assert result['stage_completed'] is False
    assert result['payload']['validation_warnings'][0]['code'] == 'DETERMINISTIC_SHOT_DESIGN'
    validate_storyboard(result['payload']['episodes'])
    previews = [event[3]['assets_preview'] for event in events
                if event[3] and 'assets_preview' in event[3]]
    assert previews[-1]['validation_warnings'] == result['payload']['validation_warnings']
    assert len(fake.calls) == 4
    assert not any('划分成视频生成片段' in prompt for prompt in fake.calls)


async def test_storyboard_failure_cancels_other_episode_work(monkeypatch):
    agent = StoryboardAgent()
    waiting = asyncio.Event()
    cancelled = asyncio.Event()
    async def design(ep_n, *args, **kwargs):
        if ep_n == 1:
            await waiting.wait()
            raise ModelOutputError()
        waiting.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise
    monkeypatch.setattr(agent, '_design_episode_storyboard', design)
    script = {**SCRIPT, 'episodes': EPISODES + [{'episode_number': 2, 'content': '第二集'}]}
    with pytest.raises(ModelOutputError):
        await agent.process({'session_id': 'test', 'llm_model': 'mock',
                             '_session_artifacts': {'script_generation': script}})
    assert cancelled.is_set()


@pytest.mark.parametrize('kind', ['gpt', 'gemini'])
def test_provider_errors_and_prompt_are_not_logged(kind, caplog, monkeypatch):
    from models.llm_gemini import Gemini
    from models.llm_gpt import GPT
    cls = GPT if kind == 'gpt' else Gemini
    client = cls.__new__(cls)
    create = Mock(side_effect=RuntimeError('PRIVATE_PROVIDER_RESPONSE'))
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client.max_attempts, client.max_tokens = 2, 100
    monkeypatch.setattr('time.sleep', Mock())
    with pytest.raises(Exception):
        client.query('PRIVATE_USER_PROMPT', model='mock')
    assert create.call_count == 2
    assert 'PRIVATE_PROVIDER_RESPONSE' not in caplog.text
    assert 'PRIVATE_USER_PROMPT' not in caplog.text


def test_app_import_uses_isolated_database_without_real_sessions():
    import os
    from pathlib import Path

    from api.dependencies import workflow_engine
    from config import settings

    assert Path(workflow_engine._session_dir).parent == Path(os.environ['YC_STATE_DIR'])
    assert not Path(os.environ['YC_STATE_DIR']).is_relative_to(Path(settings.BASE_DIR))
    assert workflow_engine.list_saved_sessions() == []

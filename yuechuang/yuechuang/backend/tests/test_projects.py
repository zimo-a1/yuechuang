"""Phase 6 project lifecycle and script editing; all model execution is offline."""
import asyncio
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest

from core.agents.base_agent import AgentInterface
from core.orchestrator import WorkflowEngine, WorkflowStage
from core.tasks import TaskManager
from tests.test_output_validation import SCRIPT


@pytest.fixture
async def api(tmp_path, monkeypatch):
    from api.app import app
    from api.routers import workflow, sessions
    engine = WorkflowEngine(session_dir=str(tmp_path / 'legacy'), database_path=tmp_path / 'db.sqlite3')
    manager = TaskManager(engine)
    monkeypatch.setattr(workflow, 'workflow_engine', engine)
    monkeypatch.setattr(workflow, 'task_manager', manager)
    monkeypatch.setattr(sessions, 'workflow_engine', engine)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        try:
            yield client, engine, manager
        finally:
            await manager.shutdown()
            engine.store.close()


async def draft(client, **values):
    response = await client.post('/api/projects', json=values, headers={'Idempotency-Key': str(uuid4())})
    assert response.status_code == 201, response.text
    return response.json()


async def test_empty_draft_has_no_model_requirement_or_job_and_replays(api):
    client, engine, manager = api
    headers = {'Idempotency-Key': str(uuid4())}
    first = await client.post('/api/projects', json={}, headers=headers)
    assert first.status_code == 201
    item = first.json()
    sid = item['session_id']
    assert item['meta']['episodes'] == 1 and item['meta']['target_duration'] == 60
    assert engine.store.jobs(sid) == []
    assert (await client.post('/api/projects', json={}, headers=headers)).json()['session_id'] == sid
    assert (await client.post('/api/projects', json={'idea': '不同内容'}, headers=headers)).status_code == 409


def test_creation_and_edits_survive_restart(tmp_path):
    path = tmp_path / 'db.sqlite3'
    engine = WorkflowEngine(session_dir=str(tmp_path / 'legacy'), database_path=path)
    key = str(uuid4())
    item, _ = engine.create_draft({'title': '草稿', 'idea': '一个故事'}, key, 'hash')
    sid = item['session_id']
    engine.update_draft(sid, {'title': '保存后的项目名', 'target_duration': 90}, 1)
    engine.store.close()
    restored = WorkflowEngine(session_dir=str(tmp_path / 'legacy'), database_path=path)
    try:
        snapshot = restored.get_status_snapshot(sid)
        assert snapshot['meta']['title'] == '保存后的项目名'
        assert snapshot['meta']['target_duration'] == 90
        repeat, created = restored.create_draft({}, key, 'hash')
        assert not created and repeat['session_id'] == sid
        assert len(restored.list_saved_sessions()) == 1
    finally:
        restored.store.close()


def test_legacy_storage_timestamp_does_not_change_project_metadata_on_restart(tmp_path):
    path = tmp_path / 'db.sqlite3'
    engine = WorkflowEngine(session_dir=str(tmp_path / 'legacy'), database_path=path)
    engine.create_session('legacy', {'idea': '旧项目', 'episodes': 4})
    record = engine.store.sessions()[0]
    record['created_at'] = 1700000000
    engine.store.save_session(record)
    before = engine.get_status_snapshot('legacy')['meta']
    engine.store.close()
    restored = WorkflowEngine(session_dir=str(tmp_path / 'legacy'), database_path=path)
    try:
        assert restored.get_status_snapshot('legacy')['meta'] == before
    finally:
        restored.store.close()


def test_literal_boolean_text_and_empty_models_round_trip_as_a_draft(tmp_path):
    path = tmp_path / 'db.sqlite3'
    engine = WorkflowEngine(session_dir=str(tmp_path / 'legacy'), database_path=path)
    item, _ = engine.create_draft({'title': 'true', 'idea': 'false', 'llm_model': None, 'web_search': 'false'}, str(uuid4()), 'hash')
    assert item['meta']['title'] == 'true' and item['meta']['idea'] == 'false'
    assert item['meta']['web_search'] is False
    engine.store.close()
    restored = WorkflowEngine(session_dir=str(tmp_path / 'legacy'), database_path=path)
    try:
        assert restored.get_status_snapshot(item['session_id'])['meta'] == item['meta']
    finally:
        restored.store.close()


@pytest.mark.parametrize('body', [{'title': 'a' * 121}, {'episodes': 0}, {'episodes': True}, {'target_duration': 1}, {'unknown': 1}, []])
async def test_invalid_draft_does_not_create_session(api, body):
    client, engine, _ = api
    response = await client.post('/api/projects', json=body, headers={'Idempotency-Key': str(uuid4())})
    assert response.status_code == 422 and response.json()['error']['code'] == 'INVALID_PROJECT'
    assert not engine.sessions


async def test_update_rename_stale_revision_and_generation_settings(api):
    client, engine, _ = api
    item = await draft(client, idea='修表匠的一分钟')
    sid = item['session_id']
    path = f'/api/projects/{sid}'
    updated = await client.patch(path, json={'expected_revision': 1, 'title': '逆行怀表', 'idea': '挽回陌生人的一分钟', 'episodes': 2, 'target_duration': 90})
    assert updated.status_code == 200
    assert updated.json()['meta']['project_revision'] == 2
    assert (await client.patch(path, json={'expected_revision': 1, 'title': '旧页面覆盖'})).status_code == 409
    row = (await client.get('/api/sessions')).json()['sessions'][0]
    assert row['title'] == '逆行怀表' and row['confirmed_stages'] == []
    assert row['meta']['user_textbox_input'] == '挽回陌生人的一分钟'
    state = engine.get_state(sid)
    state.artifacts['script_generation'] = deepcopy(SCRIPT)
    assert (await client.patch(path, json={'expected_revision': 2, 'idea': '覆盖生成依据'})).status_code == 409
    assert (await client.patch(path, json={'expected_revision': 2, 'title': '已生成项目也可改名'})).status_code == 200
    assert state.artifacts['script_generation'] == SCRIPT


async def test_script_requires_only_text_and_returns_to_manual_review(api):
    client, engine, manager = api
    item = await draft(client, idea='原创一分钟短剧', llm_model='offline')
    sid = item['session_id']
    class OfflineScript(AgentInterface):
        async def process(self, values, intervention=None):
            assert values['target_duration'] == 60
            assert values['episodes'] == 1
            return {'payload': deepcopy(SCRIPT), 'requires_intervention': True}
    engine.agent_factories[WorkflowStage.SCRIPT_GENERATION] = OfflineScript
    response = await client.post(f'/api/project/{sid}/execute/script_generation', json={}, headers={'Prefer': 'respond-async', 'Idempotency-Key': str(uuid4())})
    assert response.status_code == 202, response.text
    for _ in range(200):
        job = manager.get(sid, response.json()['task_id'])
        if job['status'] in {'completed', 'error'}:
            break
        await asyncio.sleep(.01)
    assert job['status'] == 'completed', job
    status = engine.get_status_snapshot(sid)
    assert status['status']['script_generation'] == 'waiting'
    assert status['confirmed_stages'] == []
    assert len(engine.store.jobs(sid)) == 1
    assert status['status']['character_design'] == 'pending'


@pytest.mark.parametrize('values,code', [({'idea': '有创意', 'llm_model': ''}, 'INVALID_MODEL_CONFIG'), ({'llm_model': 'offline'}, 'IDEA_REQUIRED')])
async def test_missing_model_or_idea_rejected_before_task(api, values, code):
    client, engine, _ = api
    item = await draft(client, **values)
    sid = item['session_id']
    response = await client.post(f'/api/project/{sid}/execute/script_generation', json={}, headers={'Prefer': 'respond-async'})
    assert response.json()['error']['code'] == code
    assert not engine.store.jobs(sid)


@pytest.mark.parametrize('bad', [{'title': ''}, {'episodes': []}, {'episodes': [{'episode_number': 1, 'content': {}}]}, {'characters': 'invalid'}, []])
async def test_invalid_script_save_is_atomic(api, bad):
    client, engine, _ = api
    item = await draft(client)
    sid = item['session_id']
    engine.get_state(sid).artifacts['script_generation'] = deepcopy(SCRIPT)
    response = await client.patch(f'/api/project/{sid}/artifact/script_generation', json=bad)
    assert response.status_code == 422, response.text
    assert engine.get_state(sid).artifacts['script_generation'] == SCRIPT
    assert not engine.store.jobs(sid)


async def test_script_save_retains_media_revokes_confirmations_and_persists(api):
    client, engine, _ = api
    engine.create_session('legacy', {'idea': '旧项目', 'episodes': 4})
    state = engine.get_state('legacy')
    state.artifacts = {'script_generation': deepcopy(SCRIPT), 'character_design': {'characters': [{'id': 'old', 'selected': 'old.png'}]}}
    state.confirmed_stages = ['script_generation', 'character_design']
    original_media = deepcopy(state.artifacts['character_design'])
    changed = deepcopy(SCRIPT)
    changed['episodes'][0]['content'] = '修表匠：请等一分钟。'
    result = await client.patch('/api/project/legacy/artifact/script_generation', json=changed)
    assert result.status_code == 200, result.text
    assert state.confirmed_stages == []
    assert state.artifacts['character_design'] == original_media
    assert state.meta['episodes'] == 4 and not engine.store.jobs('legacy')
    persisted = engine.store.sessions()[0]
    assert persisted['artifacts']['script_generation']['episodes'][0]['content'] == changed['episodes'][0]['content']
    assert (await client.get('/api/sessions')).json()['sessions'][0]['source_type'] == 'legacy'


async def test_busy_project_cannot_be_renamed_or_edited(api, monkeypatch):
    client, engine, _ = api
    item = await draft(client)
    with monkeypatch.context() as patch:
        patch.setattr(engine.store, 'jobs', lambda sid: [{'status': 'queued'}])
        response = await client.patch(f"/api/projects/{item['session_id']}", json={'expected_revision': 1, 'title': '修改'})
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'TASK_CONFLICT'


async def test_disk_failure_does_not_leak_unsaved_edits_into_memory(api, monkeypatch):
    client, engine, _ = api
    item = await draft(client, idea='原始想法')
    sid = item['session_id']
    engine.update_artifact(sid, 'script_generation', deepcopy(SCRIPT))
    before = engine.get_status_snapshot(sid)
    def fail(*args):
        raise OSError('private path must not reach user')
    with monkeypatch.context() as patch:
        patch.setattr(engine.store, 'save_session', fail)
        response = await client.patch(f'/api/project/{sid}/artifact/script_generation', json={'title': '未落盘标题'})
        assert response.status_code == 500
        assert response.json()['error']['code'] == 'STORAGE_UNAVAILABLE'
        assert 'private' not in response.text
        assert engine.get_status_snapshot(sid) == before
        response = await client.patch(f'/api/projects/{sid}', json={'title':'未落盘项目名', 'expected_revision':before['meta']['project_revision']})
        assert response.status_code == 500
        assert engine.get_status_snapshot(sid) == before

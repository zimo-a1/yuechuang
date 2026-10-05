"""IP draft presentation settings: isolated local data, no model calls."""
from uuid import uuid4
import pytest
from tests.test_ip_library import api, upload  # noqa: F401


async def test_ip_project_defaults_override_replay_and_episode_inheritance(api, monkeypatch):
    from core import service_settings
    client, engine, _ = api
    defaults = service_settings.project_defaults()
    monkeypatch.setattr(service_settings, 'project_defaults', lambda: {**defaults, 'style': 'realistic', 'video_ratio': '16:9'})
    data = (await upload(client)).json()
    path = f"/api/ip/{data['item']['id']}/projects"
    body = {'version_id': data['versions'][0]['id'], 'expected_revision': 1, 'title': '范围测试',
            'direction': '聚焦主角', 'first_chapter': 2, 'last_chapter': 3}
    legacy = await client.post(path, json=body, headers={'Idempotency-Key': str(uuid4())})
    assert legacy.status_code == 201
    assert legacy.json()['meta']['style'] == 'realistic' and legacy.json()['meta']['video_ratio'] == '16:9'
    key = str(uuid4())
    custom = {**body, 'style': 'anime', 'video_ratio': '9:16'}
    first = await client.post(path, json=custom, headers={'Idempotency-Key': key})
    assert first.status_code == 201
    project = first.json()
    assert project['meta']['style'] == 'anime' and project['meta']['video_ratio'] == '9:16'
    assert project['meta']['ip_binding']['first_chapter'] == 2
    assert engine.store.jobs(project['session_id']) == []
    replay = await client.post(path, json=custom, headers={'Idempotency-Key': key})
    assert replay.status_code == 200 and replay.json()['session_id'] == project['session_id']
    changed = await client.post(path, json={**custom, 'video_ratio': '16:9'}, headers={'Idempotency-Key': key})
    assert changed.status_code == 409
    episode = await client.post(f"/api/projects/{project['session_id']}/episodes", json={}, headers={'Idempotency-Key': str(uuid4())})
    assert episode.status_code == 201
    assert episode.json()['meta']['style'] == 'anime' and episode.json()['meta']['video_ratio'] == '9:16'


@pytest.mark.parametrize('changes', [{'style': 'invalid'}, {'video_ratio': '0:0'}, {'style': 1}])
async def test_ip_project_rejects_unsupported_presentation_before_creating(api, changes):
    client, engine, _ = api
    data = (await upload(client)).json()
    response = await client.post(f"/api/ip/{data['item']['id']}/projects", json={
        'version_id': data['versions'][0]['id'], 'expected_revision': 1, 'title': '测试', 'direction': '主线',
        'first_chapter': 1, 'last_chapter': 1, **changes,
    }, headers={'Idempotency-Key': str(uuid4())})
    assert response.status_code == 422
    assert not engine.list_saved_sessions()


async def test_preexisting_ip_request_key_remains_replayable_without_new_fields(api):
    from core.ip_library import digest
    client, engine, _ = api
    data = (await upload(client)).json()
    body = {'version_id': data['versions'][0]['id'], 'expected_revision': 1, 'title': '旧请求',
            'direction': '聚焦来信', 'first_chapter': 2, 'last_chapter': 3,
            'episodes': 1, 'target_duration': 60}
    ip_id, key = data['item']['id'], str(uuid4())
    # Fingerprint produced before the optional style/ratio fields existed.
    previous_hash = digest({'ip_id': ip_id, **body})
    previous, _ = engine.create_draft({'title': body['title'], 'project_container': True}, key, previous_hash)
    response = await client.post(f'/api/ip/{ip_id}/projects', json=body, headers={'Idempotency-Key': key})
    assert response.status_code == 200
    assert response.json()['session_id'] == previous['session_id']
    assert len(engine.list_saved_sessions()) == 1

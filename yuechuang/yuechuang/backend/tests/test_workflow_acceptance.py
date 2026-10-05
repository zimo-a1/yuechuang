"""Offline acceptance: real API, task storage, confirmations and FFmpeg; no provider calls."""
import asyncio
import copy
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from core.agents.base_agent import AgentInterface
from core.agents.editor_agent import VideoEditorAgent
from core.orchestrator import WorkflowEngine, WorkflowStage
from core.tasks import TaskManager
from core.workflow_rules import STAGES, WorkflowConflict, stage_ready
from upload_validation import ffmpeg_executable

META = dict(llm_model='offline', vlm_model='offline', image_t2i_model='offline',
            image_it2i_model='offline', video_model='offline', idea='第四阶段离线联调')


def artifacts(clip='clip.mp4'):
    return {
        'script_generation': {'title': '离线测试', 'episodes': [{'episode_number': 1, 'content': '人物走向窗前'}],
                              'characters': [{'character_id': 'c1', 'name': '人物'}], 'settings': []},
        'character_design': {'characters': [{'id': 'c1', 'name': '人物', 'selected': 'character.png'}], 'settings': []},
        'storyboard': {'episodes': [{'episode_number': 1, 'act_title': '窗前', 'segments': [
            {'segment_id': 'seg_01_01', 'shots': [{'duration': 1, 'content': '人物走向窗前'}]}]}]},
        'reference_generation': {'scenes': [{'id': 'seg_01_01', 'selected': 'reference.png'}]},
        'video_generation': {'clips': [{'id': 'seg_01_01', 'episode': 1, 'selected': clip}]},
        'post_production': {'final_videos': [{'episode': 1, 'path': 'final.mp4'}]},
    }


def install_offline_agents(engine, payloads):
    for stage in STAGES[:-1]:
        def factory(key=stage):
            class OfflineAgent(AgentInterface):
                async def process(self, input_data, intervention=None):
                    return {'payload': copy.deepcopy(payloads[key]), 'stage_completed': True}
            return OfflineAgent()
        engine.agent_factories[WorkflowStage(stage)] = factory


@pytest.fixture
def engine(tmp_path):
    engine = WorkflowEngine(session_dir=str(tmp_path / 'legacy'), database_path=tmp_path / 'db.sqlite3')
    engine.create_session('demo', META)
    yield engine
    engine.store.close()


@pytest.fixture
def real_clip(tmp_path):
    path = tmp_path / "片段's.mp4"
    subprocess.run([ffmpeg_executable(), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=64x96:r=10:d=1',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)], check=True, timeout=20)
    return path


async def terminal(client, url):
    for _ in range(300):
        job = (await client.get(url)).json()
        if job['status'] in {'completed', 'error', 'stopped', 'interrupted'}:
            return job
        await asyncio.sleep(.02)
    pytest.fail('task did not terminate')


@pytest.fixture
async def api(engine, monkeypatch):
    from api.app import app
    from api.routers import workflow
    manager = TaskManager(engine)
    monkeypatch.setattr(workflow, 'workflow_engine', engine)
    monkeypatch.setattr(workflow, 'task_manager', manager)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield client, manager
    if not manager._closing:
        await manager.shutdown()


async def test_six_stage_api_confirm_export_and_restart(engine, api, real_clip, tmp_path, monkeypatch):
    from config import settings
    client, manager = api
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, 'CODE_DIR', str(tmp_path / 'code'))
    install_offline_agents(engine, artifacts(str(real_clip)))
    for index, stage in enumerate(STAGES):
        # Neither generation nor the next paid stage can bypass a human confirmation.
        if index:
            rejected = await client.post(f'/api/project/demo/execute/{stage}', json={}, headers={'Prefer': 'respond-async'})
            assert rejected.status_code == 409
            assert rejected.json()['error']['code'] == 'CONFIRMATION_REQUIRED'
            confirmation = await client.post('/api/project/demo/continue', json={'stage': STAGES[index - 1]})
            assert confirmation.status_code == 200
        accepted = await client.post(f'/api/project/demo/execute/{stage}', json={}, headers={'Prefer': 'respond-async'})
        assert accepted.status_code == 202, accepted.text
        job = await terminal(client, accepted.json()['status_url'])
        assert job['status'] == 'completed', job
        status = (await client.get('/api/project/demo/status')).json()
        assert status['status'][stage] == 'waiting'
        assert stage not in status['confirmed_stages']
    confirmed = await client.post('/api/project/demo/continue', json={'stage': STAGES[-1]})
    assert confirmed.json()['status'] == 'completed'
    assert confirmed.json()['confirmed_stages'] == list(STAGES)
    exported = await client.get('/api/project/demo/export/1')
    assert exported.status_code == 200
    assert exported.headers['content-type'] == 'video/mp4'
    assert 'attachment' in exported.headers['content-disposition']
    assert exported.content[4:8] == b'ftyp'
    await manager.shutdown()
    engine.store.close()
    restored = WorkflowEngine(session_dir=str(tmp_path / 'legacy'), database_path=tmp_path / 'db.sqlite3')
    try:
        assert restored.get_state('demo').confirmed_stages == list(STAGES)
        assert all(restored.get_state('demo').status[s] == 'completed' for s in STAGES)
        assert Path(restored.get_state('demo').artifacts['post_production']['final_video']).is_file()
    finally:
        restored.store.close()


@pytest.mark.parametrize('stage', STAGES)
async def test_incomplete_artifact_cannot_be_confirmed(engine, api, stage):
    state = engine.get_state('demo')
    state.confirmed_stages = list(STAGES[:STAGES.index(stage)])
    state.artifacts[stage] = {}
    client, _ = api
    response = await client.post('/api/project/demo/continue', json={'stage': stage})
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'STAGE_INCOMPLETE'


@pytest.mark.parametrize('bad', [[], 'bad', {'episodes': ['bad']}, {'episodes': {'invalid': 1}}])
def test_malformed_manual_content_is_not_ready(engine, bad):
    state = engine.get_state('demo')
    for stage in STAGES:
        state.artifacts[stage] = bad
        assert not stage_ready(state, stage)


async def test_edit_invalidates_downstream_confirmations_without_deleting_artifacts(engine):
    state = engine.get_state('demo')
    state.artifacts = artifacts()
    state.confirmed_stages = list(STAGES)
    state.status = {s: 'completed' for s in STAGES}
    before = copy.deepcopy(state.artifacts['post_production'])
    engine.update_artifact('demo', 'character_design', {'characters': [{'id': 'c1', 'selected': 'new.png'}]})
    assert state.confirmed_stages == ['script_generation']
    assert all(state.status[s] == 'waiting' for s in STAGES[1:])
    assert state.artifacts['post_production'] == before
    assert engine.store.sessions()[0]['confirmed_stages'] == ['script_generation']


async def test_partial_failure_is_retryable_and_preserves_success(engine, api):
    client, _ = api
    state = engine.get_state('demo')
    state.confirmed_stages = ['script_generation']
    attempts = []
    class PartialAgent(AgentInterface):
        async def process(self, input_data, intervention=None):
            attempts.append(1)
            return {'payload': {'characters': [
                {'id': 'a', 'selected': 'good.png', 'status': 'done'},
                {'id': 'b', 'selected': '' if len(attempts) == 1 else 'fixed.png',
                 'status': 'failed' if len(attempts) == 1 else 'done'}], 'settings': []}}
    engine.agent_factories[WorkflowStage.CHARACTER_DESIGN] = PartialAgent
    accepted = await client.post('/api/project/demo/execute/character_design', json={}, headers={'Prefer': 'respond-async'})
    job = await terminal(client, accepted.json()['status_url'])
    assert job['error']['code'] == 'STAGE_OUTPUT_INCOMPLETE'
    assert state.artifacts['character_design']['characters'][0]['selected'] == 'good.png'
    retry = await client.post(f"/api/project/demo/tasks/{job['task_id']}/retry", headers={'Prefer': 'respond-async'})
    assert (await terminal(client, retry.json()['status_url']))['status'] == 'completed'
    assert state.status['character_design'] == 'waiting'


async def test_confirm_and_edit_rejected_while_active(engine, api):
    client, _ = api
    engine.store.save_job({'task_id': 'busy', 'session_id': 'demo', 'status': 'running'})
    for url, method in [('/continue', client.post), ('/artifact/script_generation', client.patch)]:
        response = await method('/api/project/demo' + url, json={'stage': 'script_generation'})
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'TASK_CONFLICT'
    engine.store.save_job({'task_id': 'busy', 'session_id': 'demo', 'status': 'completed'})


async def test_download_cannot_escape_session_output(engine, api, tmp_path):
    client, _ = api
    outside = tmp_path / 'private.mp4'
    outside.write_bytes(b'private')
    state = engine.get_state('demo')
    state.artifacts['post_production'] = {'final_videos': [{'episode': 1, 'path': str(outside)}]}
    assert (await client.get('/api/project/demo/export/1')).status_code == 404
    assert (await client.get('/api/project/demo/export/2')).status_code == 404


async def test_editor_rejects_missing_clip_without_partial_export(tmp_path, monkeypatch, real_clip):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(WorkflowConflict, match='缺失'):
        await VideoEditorAgent().process({'session_id': 'demo', 'selected_clips': {'1': str(real_clip), '2': 'missing.mp4'}})
    assert not list(tmp_path.glob('code/result/video/demo/output/*.mp4'))


async def test_editor_uses_storyboard_order_and_retains_versions(tmp_path, monkeypatch, real_clip):
    monkeypatch.chdir(tmp_path)
    red = tmp_path / 'red.mp4'
    subprocess.run([ffmpeg_executable(), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=64x96:r=10:d=1',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(red)], check=True, timeout=20)
    data = artifacts(str(real_clip))
    data['storyboard']['episodes'][0]['segments'].append({'segment_id': 'seg_01_02', 'shots': [{}]})
    data['video_generation']['clips'].insert(0, {'id': 'seg_01_02', 'selected': str(red)})
    agent = VideoEditorAgent()
    first = await agent.process({'session_id': 'demo', '_session_artifacts': data})
    second = await agent.process({'session_id': 'demo', '_session_artifacts': data})
    one, two = [Path(result['payload']['final_video']) for result in (first, second)]
    assert one != two and one.is_file() and two.is_file()
    # Decode every frame; first second must be blue, second red, despite reversed clip input.
    raw = subprocess.run([ffmpeg_executable(), '-v', 'error', '-i', str(two), '-vf', 'scale=1:1',
                          '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], capture_output=True, check=True, timeout=20).stdout
    frames = [tuple(raw[i:i+3]) for i in range(0, len(raw), 3)]
    assert len(frames) == 20
    assert frames[0][2] > 200 and frames[-1][0] > 200
    assert not list(two.parent.glob('.export-*'))


@pytest.mark.parametrize('cancel', [False, True])
def test_editor_terminates_child_on_timeout_or_cancel(monkeypatch, cancel):
    processes = []
    original = subprocess.Popen
    def record(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(subprocess, 'Popen', record)
    agent = VideoEditorAgent()
    agent.set_cancellation_check(lambda: bool(processes) and cancel)
    with pytest.raises((WorkflowConflict, RuntimeError)):
        agent._run_ffmpeg([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=.05)
    assert processes[0].poll() is not None


def test_readiness_requires_matching_character_and_episode_ids(engine):
    state = engine.get_state('demo')
    state.artifacts = artifacts()
    state.artifacts['character_design']['characters'][0]['id'] = 'unrelated'
    assert not stage_ready(state, 'character_design')
    state.artifacts['storyboard']['episodes'][0]['episode_number'] = 2
    assert not stage_ready(state, 'storyboard')


@pytest.mark.parametrize('event_key', ['asset_complete', 'asset_update'])
async def test_failed_regeneration_does_not_hide_behind_previous_version(engine, api, event_key):
    client, _ = api
    state = engine.get_state('demo')
    state.artifacts = artifacts()
    state.confirmed_stages = ['script_generation']
    class FailedRegeneration(AgentInterface):
        async def process(self, input_data, intervention=None):
            self._report_progress('角色', '失败', 100, {event_key: {'type': 'characters', 'id': 'c1', 'status': 'failed'}})
            return {'payload': copy.deepcopy(state.artifacts['character_design'])}
    engine.agent_factories[WorkflowStage.CHARACTER_DESIGN] = FailedRegeneration
    accepted = await client.post('/api/project/demo/intervene', json={
        'stage': 'character_design', 'modifications': {'regenerate_characters': ['c1']}}, headers={'Prefer': 'respond-async'})
    job = await terminal(client, accepted.json()['status_url'])
    assert job['error']['code'] == 'STAGE_OUTPUT_INCOMPLETE'
    assert state.artifacts['character_design']['characters'][0]['selected'] == 'character.png'
    assert state.artifacts['character_design']['characters'][0]['generation_status'] == 'failed'
    assert state.stage_progress['character_design']['step'] == '执行失败'
    engine.update_artifact('demo', 'character_design', copy.deepcopy(state.artifacts['character_design']))
    assert state.error is None
    assert state.stage_progress['character_design']['step'] == '等待确认'


def test_legacy_completed_artifacts_are_not_assumed_human_confirmed(tmp_path):
    from core.state_store import StateStore
    db = tmp_path / 'legacy.sqlite3'
    store = StateStore(db)
    store.save_session({'session_id': 'legacy', 'status': {s: 'completed' for s in STAGES}, 'artifacts': artifacts()})
    store.close()
    loaded = WorkflowEngine(session_dir=str(tmp_path / 'legacy'), database_path=db)
    try:
        state = loaded.get_state('legacy')
        assert state.confirmed_stages == []
        assert all(state.status[s] == 'waiting' for s in STAGES)
        assert state.artifacts['post_production']['final_videos']
    finally:
        loaded.store.close()


def test_real_provider_smoke_defaults_to_plan_only(monkeypatch, capsys):
    from tests import manual_provider_smoke
    monkeypatch.setattr(sys, 'argv', ['manual_provider_smoke'])
    assert manual_provider_smoke.main() == 0
    assert '"creation_retries": 0' in capsys.readouterr().out

async def test_editor_selected_order_and_history(real_clip,tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload=artifacts(str(real_clip))
    payload['video_generation']['clips']=[{'id':'first','selected':str(real_clip)},{'id':'second','selected':str(real_clip)}]
    payload['storyboard']['episodes'][0]['segments']=[{'segment_id':'first'},{'segment_id':'second'}]
    agent=VideoEditorAgent()
    result=await agent.process({'session_id':'selected','_session_artifacts':payload},{'clip_ids':['second','first']})
    assert result['payload']['clip_ids']==['second','first']
    assert len(result['payload']['export_history'])==1
    assert Path(result['payload']['final_video']).is_file()
    for selection in ([],['unknown'],['first','first']):
        with pytest.raises(WorkflowConflict,match='请选择不重复|不存在'):
            await agent.process({'session_id':'invalid','_session_artifacts':payload},{'clip_ids':selection})

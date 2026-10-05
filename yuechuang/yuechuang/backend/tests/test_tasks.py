import asyncio
import json
import os
import subprocess
import sys
import threading
import time

import httpx
import pytest

from core.agents.base_agent import AgentInterface
from core.orchestrator import WorkflowEngine, WorkflowStage
from core.tasks import TaskConflict, TaskManager

STAGE = 'script_generation'
META = dict(llm_model='test', vlm_model='test', image_t2i_model='test',
            image_it2i_model='test', video_model='test')


@pytest.fixture
def engine(tmp_path):
    instance = WorkflowEngine(session_dir=str(tmp_path / 'sessions'), database_path=tmp_path / 'workflow.sqlite3')
    yield instance
    instance.store.close()


class ControlledAgent(AgentInterface):
    started = None
    release = None

    async def process(self, input_data, intervention=None):
        self._report_progress('生成', '检查点', 40, {'assets_preview': {'title': '已保存草稿'}})
        self.started.set()
        # A deliberately blocking SDK call: API responsiveness must be preserved.
        while not self.release.wait(0.01):
            self._check_cancel()
        return {'payload': {'title': '测试剧本', 'episodes': []}, 'requires_intervention': True}


def controlled(engine):
    started, release = threading.Event(), threading.Event()
    class Agent(ControlledAgent):
        pass
    Agent.started, Agent.release = started, release
    engine.agent_factories[WorkflowStage.SCRIPT_GENERATION] = Agent
    engine.create_session('demo', META)
    return started, release


async def wait_for(manager, task_id, statuses):
    for _ in range(300):
        job = manager.get('demo', task_id)
        if job['status'] in statuses:
            return job
        await asyncio.sleep(0.01)
    pytest.fail('task did not reach expected state')


async def test_durable_progress_disconnect_idempotency_and_retry(engine):
    started, release = controlled(engine)
    manager = TaskManager(engine)
    job = manager.submit('demo', STAGE, META, idempotency_key='same')
    assert manager.submit('demo', STAGE, META, idempotency_key='same')['task_id'] == job['task_id']
    with pytest.raises(TaskConflict):
        manager.submit('demo', STAGE, META)
    try:
        assert await asyncio.to_thread(started.wait, 2)
        durable = engine.store.sessions()[0]
        assert durable['stage_progress'][STAGE]['percent'] == 40
        assert durable['artifacts'][STAGE]['title'] == '已保存草稿'
        assert not engine.delete_session('demo')
        # No HTTP request/stream is kept alive while the worker runs.
        manager.stop('demo')
        release.set()
        result = await wait_for(manager, job['task_id'], {'stopped'})
        assert result['status'] == 'stopped'
        retried = manager.retry('demo', job['task_id'])
        result = await wait_for(manager, retried['task_id'], {'completed'})
        assert result['result']['requires_intervention'] is True
        assert engine.get_state('demo').status[STAGE] == 'waiting'
    finally:
        release.set()
        await manager.shutdown()


async def test_stop_queued_job_and_conflict_while_worker_stopping(engine):
    started, release = controlled(engine)
    engine.create_session('second', META)
    manager = TaskManager(engine, concurrency=1)
    first = manager.submit('demo', STAGE, META)
    second = manager.submit('second', STAGE, META)
    try:
        assert await asyncio.to_thread(started.wait, 2)
        manager.stop('second')
        assert manager.get('second', second['task_id'])['status'] == 'stopped'
        manager.stop('demo')
        assert manager.get('demo', first['task_id'])['status'] == 'stopping'
        with pytest.raises(TaskConflict):
            manager.submit('demo', STAGE, META)
        await wait_for(manager, first['task_id'], {'stopped'})
        assert engine.get_state('demo').status[STAGE] == 'stopped'
    finally:
        release.set()
        await manager.shutdown()


async def test_sdk_initialization_error_is_persisted_and_redacted(engine):
    def broken():
        raise RuntimeError('SECRET_MUST_NOT_LEAK')
    engine.create_session('demo', META)
    engine.agent_factories[WorkflowStage.SCRIPT_GENERATION] = broken
    manager = TaskManager(engine)
    job = manager.submit('demo', STAGE, META)
    result = await wait_for(manager, job['task_id'], {'error'})
    assert result['error']['code'] == 'TASK_FAILED'
    assert 'SECRET_MUST_NOT_LEAK' not in json.dumps(result)
    assert 'SECRET_MUST_NOT_LEAK' not in json.dumps(engine.store.sessions())
    assert engine.get_state('demo').status[STAGE] == 'error'
    await manager.shutdown()


def test_migration_restart_interruption_and_no_resurrection(tmp_path):
    legacy = tmp_path / 'sessions'
    legacy.mkdir()
    original = {'session_id': 'demo', 'current_stage': STAGE, 'status': {STAGE: 'running'},
                'idea': '旧项目', 'artifacts': {STAGE: {'title': '保留'}},
                'stage_progress': {STAGE: {'percent': 42}}}
    (legacy / 'demo.json').write_text(json.dumps(original))
    (legacy / 'broken.json').write_text('{')
    path = tmp_path / 'db.sqlite3'
    engine = WorkflowEngine(session_dir=str(legacy), database_path=path)
    assert engine.get_state('demo').meta['idea'] == '旧项目'
    assert engine.get_state('demo').status[STAGE] == 'error'
    assert engine.get_state('demo').stage_progress[STAGE]['percent'] == 42
    assert json.loads((legacy / 'demo.json').read_text()) == original
    engine.store.delete_session('demo')
    engine.store.close()
    restarted = WorkflowEngine(session_dir=str(legacy), database_path=path)
    try:
        assert restarted.get_state('demo') is None
    finally:
        restarted.store.close()


def test_database_rejects_second_process_owner(engine):
    with pytest.raises(RuntimeError, match='一个进程'):
        WorkflowEngine(session_dir=engine._session_dir,
                       database_path=os.path.join(os.path.dirname(engine._session_dir), 'workflow.sqlite3'))


def test_actual_process_crash_and_restart(tmp_path):
    """Abrupt exit skips cleanup; a fresh interpreter must recover state and task records."""
    script = '''
import asyncio, os, sys
from core.orchestrator import WorkflowEngine
from core.tasks import TaskManager
async def main():
    e = WorkflowEngine(session_dir=sys.argv[1], database_path=sys.argv[2])
    e.create_session('demo', {'idea': 'process restart'})
    e.update_artifact('demo', 'script_generation', {'title': 'checkpoint'})
    m = TaskManager(e)
    m.submit('demo', 'script_generation', {})
    os._exit(0)
asyncio.run(main())
'''
    legacy, database = str(tmp_path / 'sessions'), str(tmp_path / 'db.sqlite3')
    subprocess.run([sys.executable, '-c', script, legacy, database], check=True, timeout=10)
    e = WorkflowEngine(session_dir=legacy, database_path=database)
    try:
        manager = TaskManager(e)
        state = e.get_state('demo')
        assert state.status[STAGE] == 'error'
        assert state.artifacts[STAGE]['title'] == 'checkpoint'
        jobs = e.store.jobs('demo')
        assert jobs[0]['status'] == 'interrupted'
        assert not manager._workers  # No automatic provider re-submission.
    finally:
        e.store.close()


async def test_http_202_status_disconnect_and_validation(engine, monkeypatch):
    from api.app import app
    from api.routers import workflow
    started, release = controlled(engine)
    manager = TaskManager(engine)
    monkeypatch.setattr(workflow, 'workflow_engine', engine)
    monkeypatch.setattr(workflow, 'task_manager', manager)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            before = time.monotonic()
            response = await client.post('/api/project/demo/execute/script_generation', json={},
                                         headers={'Prefer': 'respond-async'})
            assert response.status_code == 202
            assert time.monotonic() - before < 1
            accepted = response.json()
            assert await asyncio.to_thread(started.wait, 2)
            status = await asyncio.wait_for(client.get(accepted['status_url']), timeout=1)
            assert status.json()['status'] == 'running'
            assert 'input' not in status.json()
            assert (await client.get('/api/project/other/tasks/' + accepted['task_id'])).status_code == 404
            assert (await client.post('/api/project/demo/execute/init', json={})).status_code == 400
            assert (await client.post('/api/project/missing/execute/script_generation', json={})).status_code == 404
            assert (await client.post('/api/project/demo/execute/script_generation', json=[])).status_code == 422
            assert (await client.post('/api/project/demo/execute/script_generation', json={})).status_code == 409
        # Client closed; background operation remains alive and completes.
        release.set()
        assert (await wait_for(manager, accepted['task_id'], {'completed'}))['result']['payload']['title'] == '测试剧本'
    finally:
        release.set()
        await manager.shutdown()


async def test_legacy_stream_has_terminal_event(engine, monkeypatch):
    from api.app import app
    from api.routers import workflow
    _, release = controlled(engine)
    release.set()
    manager = TaskManager(engine)
    monkeypatch.setattr(workflow, 'workflow_engine', engine)
    monkeypatch.setattr(workflow, 'task_manager', manager)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/project/demo/execute/script_generation', json={})
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1]['type'] == 'done'
        assert any(event['type'] == 'stage_complete' for event in events)
    await manager.shutdown()


async def test_parallel_different_cards_and_overlap_conflict(engine):
    engine.create_session('demo', META)
    engine.update_artifact('demo', 'character_design', {
        'characters': [{'id': 'a', 'selected': ''}, {'id': 'b', 'selected': ''}], 'settings': []})
    release = threading.Event()

    class CardAgent(AgentInterface):
        async def process(self, input_data, intervention=None):
            target = intervention['regenerate_characters'][0]
            while not release.wait(0.01):
                self._check_cancel()
            return {'payload': {'characters': [{'id': target, 'selected': f'{target}.png',
                                               'versions': [f'{target}.png'], 'status': 'done'}]},
                    'requires_intervention': True}

    engine.agent_factories[WorkflowStage.CHARACTER_DESIGN] = CardAgent
    manager = TaskManager(engine)
    engine.get_state('demo').confirmed_stages = ['script_generation']
    one = manager.submit('demo', 'character_design', {}, {'regenerate_characters': ['a']})
    two = manager.submit('demo', 'character_design', {}, {'regenerate_characters': ['b']})
    with pytest.raises(TaskConflict):
        manager.submit('demo', 'character_design', {}, {'regenerate_characters': ['a']})
    with pytest.raises(TaskConflict):
        manager.submit('demo', STAGE, {})
    release.set()
    await wait_for(manager, one['task_id'], {'completed'})
    await wait_for(manager, two['task_id'], {'completed'})
    chars = engine.get_state('demo').artifacts['character_design']['characters']
    assert {char['selected'] for char in chars} == {'a.png', 'b.png'}
    await manager.shutdown()


def test_process_crash_after_progress_checkpoint(tmp_path):
    script = '''
import asyncio, os, sys
from core.orchestrator import WorkflowEngine, WorkflowStage
from core.tasks import TaskManager
from core.agents.base_agent import AgentInterface
class Agent(AgentInterface):
    async def process(self, input_data, intervention=None):
        self._report_progress('test', 'checkpoint', 65, {'assets_preview': {'title': 'partial'}})
        os._exit(0)
async def main():
    e = WorkflowEngine(session_dir=sys.argv[1], database_path=sys.argv[2])
    e.create_session('demo', {})
    e.agent_factories[WorkflowStage.SCRIPT_GENERATION] = Agent
    m = TaskManager(e)
    m.submit('demo', 'script_generation', {})
    await asyncio.sleep(20)
asyncio.run(main())
'''
    legacy, database = str(tmp_path / 'sessions'), str(tmp_path / 'db.sqlite3')
    subprocess.run([sys.executable, '-c', script, legacy, database], check=True, timeout=10)
    e = WorkflowEngine(session_dir=legacy, database_path=database)
    try:
        TaskManager(e)
        state = e.get_state('demo')
        assert state.status[STAGE] == 'error'
        assert state.stage_progress[STAGE]['percent'] == 65
        assert state.artifacts[STAGE]['title'] == 'partial'
        assert e.store.jobs('demo')[0]['status'] == 'interrupted'
    finally:
        e.store.close()


async def test_failed_legacy_stream_ends_with_safe_error(engine, monkeypatch):
    from api.app import app
    from api.routers import workflow
    engine.create_session('demo', META)
    def broken():
        raise RuntimeError('secret-provider-response')
    engine.agent_factories[WorkflowStage.SCRIPT_GENERATION] = broken
    manager = TaskManager(engine)
    monkeypatch.setattr(workflow, 'workflow_engine', engine)
    monkeypatch.setattr(workflow, 'task_manager', manager)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/project/demo/execute/script_generation', json={})
        terminal = json.loads(response.text.splitlines()[-1])
        assert terminal['type'] == 'error'
        assert terminal['error']['code'] == 'TASK_FAILED'
        assert 'secret-provider-response' not in response.text
    await manager.shutdown()

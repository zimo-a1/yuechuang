import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from core.orchestrator import WorkflowEngine
from core.quota import QuotaExceeded, VideoQuota, reservation_seconds
from core.state_store import StateStore
from core.tasks import TaskManager


@pytest.fixture
def store(tmp_path):
    db = StateStore(tmp_path / 'quota.sqlite3')
    yield db
    db.close()


def reserve(quota, task='task1', segment='seg1', episode=1, regen=False, session='demo'):
    quota.reserve(task, session, episode, segment, 'agnes-video', 5, regen)


def test_disabled_defaults_still_record_usage(store, monkeypatch):
    monkeypatch.delenv('YC_DAILY_VIDEO_SECONDS', raising=False)
    monkeypatch.delenv('YC_MAX_VIDEO_REGENERATIONS_PER_EPISODE', raising=False)
    quota = VideoQuota.from_env(store)
    for i in range(5):
        reserve(quota, task=str(i))
    snap = quota.snapshot('demo')
    assert snap['enabled'] is False
    assert snap['reserved_video_seconds'] == 25
    assert snap['remaining_video_seconds'] is None
    assert snap['episode_regenerations'] == {'1': 4}


@pytest.mark.parametrize('value', ['bad', '-2', '1.5', ''])
def test_invalid_configuration_is_not_silently_unlimited(store, monkeypatch, value):
    monkeypatch.setenv('YC_DAILY_VIDEO_SECONDS', value)
    with pytest.raises(ValueError, match='YC_DAILY_VIDEO_SECONDS'):
        VideoQuota.from_env(store)


def test_shared_daily_budget_concurrent_admission(store):
    quota = VideoQuota(store, daily_seconds=10)
    def dispatch(i):
        try:
            reserve(quota, task=str(i), session=str(i))
            return True
        except QuotaExceeded as exc:
            assert exc.code == 'VIDEO_QUOTA_EXCEEDED'
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(dispatch, range(20))) == 2
    assert quota.snapshot('demo')['reserved_video_seconds'] == 10


def test_regeneration_counts_once_per_episode_per_task(store):
    quota = VideoQuota(store, max_regenerations=2)
    reserve(quota)
    for task in ('retry1', 'retry2'):
        reserve(quota, task=task)
        reserve(quota, task=task, segment='seg2', regen=True)
    with pytest.raises(QuotaExceeded, match='重生成'):
        reserve(quota, task='retry3', regen=True)
    reserve(quota, task='other_episode', segment='ep2_seg1', episode=2)
    assert quota.snapshot('demo')['episode_regenerations'] == {'1': 2, '2': 0}


def test_duplicate_dispatch_cannot_reuse_reservation(store):
    quota = VideoQuota(store)
    reserve(quota)
    with pytest.raises(QuotaExceeded) as error:
        reserve(quota)
    assert error.value.code == 'VIDEO_ALREADY_DISPATCHED'
    assert quota.snapshot('demo')['reserved_video_seconds'] == 5


def test_reservation_survives_restart_delete_and_day_rollover(tmp_path):
    path = tmp_path / 'quota.sqlite3'
    db = StateStore(path)
    db.save_session({'session_id': 'demo'})
    quota = VideoQuota(db, daily_seconds=5, day=lambda: '2026-09-29')
    reserve(quota)
    db.delete_session('demo')
    db.close()
    db = StateStore(path)
    try:
        quota = VideoQuota(db, daily_seconds=5, day=lambda: '2026-09-29')
        with pytest.raises(QuotaExceeded):
            reserve(quota, task='second', session='new')
        tomorrow = VideoQuota(db, daily_seconds=5, day=lambda: '2026-09-30')
        reserve(tomorrow, task='tomorrow')
        assert tomorrow.snapshot('demo')['reserved_video_seconds'] == 5
        assert tomorrow.snapshot('demo')['episode_regenerations'] == {'1': 1}
    finally:
        db.close()


def test_schema_upgrade_preserves_v1_sessions_and_jobs(tmp_path):
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, payload TEXT NOT NULL)')
        db.execute('INSERT INTO sessions VALUES (?, ?)', ('demo', '{"session_id":"demo","idea":"keep"}'))
        db.execute('INSERT INTO jobs VALUES (?, ?, ?)', ('old', 'demo', '{"task_id":"old","status":"completed"}'))
        db.execute('PRAGMA user_version=1')
    db = StateStore(path)
    try:
        assert db.sessions()[0]['idea'] == 'keep'
        assert db.get_job('old')['status'] == 'completed'
        assert db.db.execute('PRAGMA user_version').fetchone()[0] == 2
        assert VideoQuota(db).snapshot('demo')['reserved_video_seconds'] == 0
    finally:
        db.close()


def test_legacy_import_recovers_snapshot_written_after_initial_migration(tmp_path):
    path = tmp_path / 'workflow.sqlite3'
    db = StateStore(path)
    try:
        db.import_legacy([{'session_id': 'first'}])
        db.import_legacy([{'session_id': 'first'}, {'session_id': 'later'}])
        assert {session['session_id'] for session in db.sessions()} == {'first', 'later'}
    finally:
        db.close()


@pytest.mark.parametrize('duration', [0, -1, True, float('nan'), float('inf'), '5'])
def test_invalid_duration_rejected(duration):
    with pytest.raises(ValueError):
        reservation_seconds('agnes', duration)


@pytest.mark.parametrize(('model', 'duration', 'seconds'), [('agnes', 1, 4), ('agnes', 30, 12), ('kling', 1, 10), ('seedance', 12.5, 13)])
def test_duration_reservation_matches_conservative_contract(model, duration, seconds):
    assert reservation_seconds(model, duration) == seconds


@pytest.fixture
def video_engine(tmp_path, monkeypatch):
    from models import video_client
    monkeypatch.chdir(tmp_path)
    calls = []
    class FakeVideoClient:
        def generate_video(self, **kwargs):
            calls.append(kwargs)
            Path(kwargs['save_path']).write_bytes(b'offline-video')
    monkeypatch.setattr(video_client, 'VideoClient', FakeVideoClient)
    engine = WorkflowEngine(session_dir=str(tmp_path / 'sessions'), database_path=tmp_path / 'state.sqlite3')
    image = tmp_path / 'reference.png'
    image.write_bytes(b'reference')
    meta = dict(llm_model='fake', vlm_model='fake', image_t2i_model='fake', image_it2i_model='fake',
                video_model='agnes-video-2.5-flash', enable_concurrency=False)
    engine.create_session('demo', meta)
    state = engine.get_state('demo')
    state.confirmed_stages = ['script_generation', 'character_design', 'storyboard', 'reference_generation']
    state.artifacts = {
        'storyboard': {'episodes': [{'episode_number': 1, 'segments': [
            {'segment_id': f'seg{i}', 'total_duration': 5, 'shots': [{'duration': 5, 'content': 'test'}]}
            for i in (1, 2)]}]},
        'reference_generation': {'scenes': [{'id': f'seg{i}', 'selected': str(image)} for i in (1, 2)]},
    }
    engine.save_session_to_disk('demo')
    yield engine, meta, calls
    engine.store.close()


async def terminal(manager, job):
    for _ in range(300):
        result = manager.get('demo', job['task_id'])
        if result['status'] in {'completed', 'error', 'stopped'}:
            return result
        await asyncio.sleep(.01)
    pytest.fail('task did not finish')


async def test_provider_boundary_partial_artifact_and_retry_cannot_bypass(video_engine):
    engine, meta, calls = video_engine
    manager = TaskManager(engine, quota=VideoQuota(engine.store, daily_seconds=5))
    try:
        job = manager.submit('demo', 'video_generation', {**meta, 'session_id': 'demo'}, idempotency_key='one')
        assert manager.submit('demo', 'video_generation', {**meta, 'session_id': 'demo'}, idempotency_key='one')['task_id'] == job['task_id']
        result = await terminal(manager, job)
        assert result['error']['code'] == 'VIDEO_QUOTA_EXCEEDED'
        assert len(calls) == 1
        clips = engine.get_state('demo').artifacts['video_generation']['clips']
        assert sum(bool(clip.get('selected')) for clip in clips) == 1
        retry = await terminal(manager, manager.retry('demo', job['task_id']))
        assert retry['error']['code'] == 'VIDEO_QUOTA_EXCEEDED'
        assert len(calls) == 1
    finally:
        await manager.shutdown()


async def test_http_quota_and_intervention_report_safe_failure(video_engine, monkeypatch):
    from api.app import app
    from api.routers import workflow
    engine, meta, calls = video_engine
    manager = TaskManager(engine, quota=VideoQuota(engine.store, max_regenerations=0))
    monkeypatch.setattr(workflow, 'workflow_engine', engine)
    monkeypatch.setattr(workflow, 'task_manager', manager)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/project/demo/intervene', json={
                'stage': 'video_generation', 'modifications': {'regenerate_clips': ['seg1']}},
                headers={'Prefer': 'respond-async'})
            assert response.status_code == 202
            result = await terminal(manager, response.json())
            assert result['error']['code'] == 'VIDEO_REGENERATION_LIMIT'
            assert not calls
            snapshot = await client.get('/api/project/demo/quota')
            assert snapshot.json()['reserved_video_seconds'] == 0
            assert (await client.get('/api/project/missing/quota')).status_code == 404
    finally:
        await manager.shutdown()


async def test_missing_references_do_not_reserve(video_engine):
    engine, meta, calls = video_engine
    for scene in engine.get_state('demo').artifacts['reference_generation']['scenes']:
        scene['selected'] = '/nonexistent/reference.png'
    manager = TaskManager(engine, quota=VideoQuota(engine.store, daily_seconds=0))
    try:
        await terminal(manager, manager.submit('demo', 'video_generation', {**meta, 'session_id': 'demo'}))
        assert not calls
        assert manager.quota.snapshot('demo')['reserved_video_seconds'] == 0
    finally:
        await manager.shutdown()

"""Durable task admission and process-owned workers, independent of HTTP connections."""

import asyncio
import copy
import logging
import threading
import time
import uuid

from core.orchestrator import WorkflowStage
from core.output_validation import ModelOutputError
from core.script_interventions import require_ip_script_access
from core.shot_control import ShotPlanError
from models.video_tudou import VideoInputError
from core.quota import QuotaExceeded, VideoQuota
from core.workflow_rules import WorkflowConflict, invalidate_confirmations, require_predecessors
from core.configuration_runtime import capture_runtime, runtime_scope, ContextThreadPoolExecutor
from core.service_settings import SettingsError

logger = logging.getLogger(__name__)

ACTIVE = {"queued", "running", "stopping"}
TERMINAL = {"completed", "error", "stopped", "interrupted"}


class TaskConflict(ValueError):
    pass


class TaskManager:
    def __init__(self, engine, concurrency=2, quota=None):
        self.engine = engine
        self.store = engine.store
        self.quota = quota if quota is not None else VideoQuota.from_env(self.store)
        self._lock = threading.RLock()
        self._workers = set()
        self._runtime = {}
        self.concurrency = concurrency
        self._slots = asyncio.Semaphore(concurrency)
        self._closing = False
        for job in self.store.jobs():
            if job['status'] in ACTIVE:
                job.update(status='interrupted', updated_at=time.time(), error={
                    'code': 'TASK_INTERRUPTED',
                    'message': '服务重启，任务已中断；已保存产物，请确认后重试',
                })
                self.store.save_job(job)
                state = self.engine.get_state(job['session_id'])
                if state and self._scope(job['stage'], job['intervention']) is None:
                    state.status[job['stage']] = 'error'
                    state.error = job['error']['message']
                    self.engine.save_session_to_disk(job['session_id'])

    def _scope(self, stage, intervention):
        enum = WorkflowStage(stage)
        if isinstance(intervention, dict):
            for key in ('regenerate_characters', 'regenerate_settings', 'regenerate_props', 'regenerate_scenes', 'regenerate_clips'):
                if key in intervention and (
                    not isinstance(intervention[key], list)
                    or any(not isinstance(item, str) or not item for item in intervention[key])
                ):
                    raise ValueError('重生成素材编号必须是字符串列表')
        if not self.engine._is_background_item_regeneration(enum, intervention):
            return None
        targets = self.engine._background_regeneration_targets(enum, intervention)
        return {f'{stage}:{kind}:{item}' for kind, ids in targets.items() for item in ids} or None

    def submit(self, session_id, stage, input_data, intervention=None, idempotency_key=None):
        if stage not in self.engine.agent_factories:
            raise ValueError('无效的生成阶段')
        state = self.engine.get_state(session_id)
        if state is None:
            raise KeyError(session_id)
        if state.meta.get('project_container'):
            raise WorkflowConflict('EPISODE_REQUIRED', '请先添加剧集，再进入制作')
        require_ip_script_access(state, stage, intervention)
        with self._lock:
            if self._closing:
                raise TaskConflict('服务正在停止，请稍后重试')
            scope = self._scope(stage, intervention)
            existing = self.store.jobs(session_id)
            for previous in existing:
                if idempotency_key and previous.get('idempotency_key') == idempotency_key:
                    if (previous['stage'], previous['input'], previous['intervention']) != (stage, input_data, intervention):
                        raise TaskConflict('同一请求编号不能用于不同内容')
                    return self.public(previous)
                if previous['status'] in ACTIVE:
                    previous_scope = self._scope(previous['stage'], previous['intervention'])
                    if scope is None or previous_scope is None or scope & previous_scope:
                        raise TaskConflict('该项目已有相关任务执行中，请等待完成或停止后重试')
            if not any(job['status'] in ACTIVE for job in existing):
                self.engine.reset_stop_event(session_id)
            now = time.time()
            runtime = capture_runtime()
            job = {
                'task_id': uuid.uuid4().hex, 'session_id': session_id, 'stage': stage,
                'status': 'queued', 'created_at': now, 'updated_at': now,
                'input': copy.deepcopy(input_data), 'intervention': copy.deepcopy(intervention),
                'idempotency_key': idempotency_key, 'events': [], 'sequence': 0,
                'result': None, 'error': None,
                'config_version': runtime['version'],
            }
            with self.engine._state_lock:
                require_predecessors(state, stage)
                invalidate_confirmations(state, stage)
                self.store.save_job(job)
                self.engine.save_session_to_disk(session_id)
            if scope is None:
                with self.engine._state_lock:
                    state.current_stage = WorkflowStage(stage)
                    state.status[stage] = 'running'
                    state.error = None
                    state.stage_progress[stage] = {'phase': stage, 'step': '排队中', 'message': '排队中', 'percent': 0}
                    self.engine.save_session_to_disk(session_id)
            worker = asyncio.create_task(self._run(job['task_id']))
            self._runtime[job['task_id']] = runtime
            self._workers.add(worker)
            worker.add_done_callback(self._worker_done)
            return self.public(job)

    def _worker_done(self, worker):
        self._workers.discard(worker)
        if not worker.cancelled():
            failure = worker.exception()
            if failure:
                logger.error("Task worker could not persist its outcome (%s)", type(failure).__name__)

    def _update(self, task_id, **updates):
        with self._lock:
            job = self.store.get_job(task_id)
            job.update(updates, updated_at=time.time())
            self.store.save_job(job)
            return job

    def _progress(self, task_id, phase, step, percent, data=None):
        with self._lock:
            job = self.store.get_job(task_id)
            job['sequence'] += 1
            event = {'seq': job['sequence'], 'type': 'progress', 'phase': phase,
                     'step_desc': step, 'message': f'{phase}: {step}', 'percent': percent}
            if data:
                event['data'] = copy.deepcopy(data)
            job['events'] = (job['events'] + [event])[-200:]
            job['updated_at'] = time.time()
            self.store.save_job(job)

    async def _run(self, task_id):
        async with self._slots:
            job = self.store.get_job(task_id)
            if job['status'] not in ACTIVE:
                self._runtime.pop(task_id, None)
                return
            if self.engine.get_stop_event(job['session_id']).is_set():
                self._update(task_id, status='stopped', error={'code': 'TASK_STOPPED', 'message': '任务已停止'})
                self._runtime.pop(task_id, None)
                return
            self._update(task_id, status='running')
            state = self.engine.get_state(job['session_id'])
            try:
                # Some SDK/agent calls are synchronous; give each job its own worker loop.
                async def execute():
                    asyncio.get_running_loop().set_default_executor(ContextThreadPoolExecutor())
                    return await self.engine.execute_stage(
                        state, WorkflowStage(job['stage']), job['input'],
                        progress_callback=lambda *args: self._progress(task_id, *args),
                        intervention=job['intervention'],
                        video_usage_guard=lambda **values: self.quota.reserve(
                            task_id=task_id, session_id=job['session_id'], **values),
                    )
                def run():
                    with runtime_scope(self._runtime[task_id]):
                        return asyncio.run(execute())
                result = await asyncio.to_thread(run)
                stopped = self.engine.get_stop_event(job['session_id']).is_set()
                self._update(task_id, status='stopped' if stopped else 'completed',
                             result=None if stopped else result,
                             error={'code': 'TASK_STOPPED', 'message': '任务已停止'} if stopped else None)
            except (Exception, asyncio.CancelledError) as exc:
                stopped = self.engine.get_stop_event(job['session_id']).is_set()
                self._update(task_id, status='stopped' if stopped else 'error', error={
                    'code': 'TASK_STOPPED' if stopped else (exc.code if isinstance(exc, (ModelOutputError, QuotaExceeded, WorkflowConflict, ShotPlanError, VideoInputError, SettingsError)) else 'TASK_FAILED'),
                    'message': '任务已停止' if stopped else (exc.message if isinstance(exc, (ModelOutputError, QuotaExceeded, WorkflowConflict, ShotPlanError, VideoInputError, SettingsError)) else '任务执行失败，请检查模型配置后重试'),
                })
            finally:
                self._runtime.pop(task_id, None)

    def stop(self, session_id):
        if self.engine.get_state(session_id) is None:
            raise KeyError(session_id)
        with self._lock:
            self.engine.stop_session(session_id)
            for job in self.store.jobs(session_id):
                if job['status'] in ACTIVE:
                    self._update(job['task_id'], status='stopped' if job['status'] == 'queued' else 'stopping',
                                 error={'code': 'TASK_STOPPED', 'message': '任务已停止'})

    def retry(self, session_id, task_id, idempotency_key=None):
        job = self.store.get_job(task_id)
        if not job or job['session_id'] != session_id:
            raise KeyError(task_id)
        if job['status'] not in {'error', 'stopped', 'interrupted'}:
            raise TaskConflict('只有失败、停止或中断的任务可以重试')
        return self.submit(session_id, job['stage'], job['input'], job['intervention'], idempotency_key)

    @staticmethod
    def public(job):
        return {key: copy.deepcopy(value) for key, value in job.items()
                if key not in {'input', 'intervention', 'idempotency_key'}}

    def get(self, session_id, task_id, after=0):
        job = self.store.get_job(task_id)
        if not job or job['session_id'] != session_id:
            raise KeyError(task_id)
        result = self.public(job)
        result['events'] = [event for event in result['events'] if event['seq'] > after]
        return result

    async def shutdown(self):
        self._closing = True
        for job in self.store.jobs():
            if job['status'] in ACTIVE:
                self.stop(job['session_id'])
        # Cooperative stop; never cancel a to_thread wrapper while it is still writing.
        if self._workers:
            await asyncio.gather(*list(self._workers), return_exceptions=True)

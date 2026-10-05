"""Durable, explicitly admitted novel jobs; no provider calls during reads/saves."""
import asyncio
import copy
import json
import threading
import time
import uuid
from pathlib import Path

from core.configuration_runtime import capture_runtime
from core.ip_library import LibraryError, digest, now
from core.novel_chunks import split_chapters, event_index
from core.novel_contracts import (Action, ChunkAnalysis, Bible, Direction, Outline,
    validate_people, validate_direction, validate_outline, require)
from core.output_validation import ModelOutputError, validate_script
from core.novel_output import validate_response, legacy_recovery_hint
from core.novel_recovery import (REPAIR_ATTEMPTS, StorySummary, known_fields, extraction_fields,
    source_registry, assemble_summary, correction_feedback)
from core.novel_planning import OutlineDraft, event_catalog, expand_outline, planning_schema, outline_limits
from core.novel_script import WrittenEpisode, finalize_script, script_schema
from core.service_settings import SettingsError
from models.novel_text import text_connection, query_novel, bounded_env, request_options

ACTIVE = {'queued', 'running', 'stopping'}
PROMPTS = Path(__file__).resolve().parents[1] / 'prompts' / 'novel'
SUMMARY_PROMPT = PROMPTS.parent / 'novel_summary.txt'
PLANNING_PROMPT = PROMPTS.parent / 'novel_planning.txt'
GROUP = 8


class Stopped(Exception):
    pass


class NovelStore:
    def __init__(self, library):
        self.library = library
        with library.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS novel_projects(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS novel_jobs(id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    operation_key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS novel_jobs_project ON novel_jobs(project_id);''')

    def project(self, pid, db=None):
        if db is None:
            with self.library.connection() as conn:
                return self.project(pid, conn)
        row = db.execute('SELECT payload FROM novel_projects WHERE id=?', (pid,)).fetchone()
        return json.loads(row[0]) if row else {'revision':0, 'current':None, 'variants':{}}

    def jobs(self, pid=None):
        with self.library.connection() as db:
            rows = db.execute('SELECT payload FROM novel_jobs' + (' WHERE project_id=?' if pid else '') + ' ORDER BY rowid',
                              (pid,) if pid else ()).fetchall()
        return [json.loads(r[0]) for r in rows]

    def job(self, jid):
        with self.library.connection() as db:
            row = db.execute('SELECT payload FROM novel_jobs WHERE id=?', (jid,)).fetchone()
        if not row:
            raise LibraryError('TASK_NOT_FOUND', '找不到小说任务', 404)
        return json.loads(row[0])

    def save(self, pid, doc=None, job=None):
        with self.library.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if doc is not None:
                db.execute('INSERT OR REPLACE INTO novel_projects VALUES(?,?)', (pid, json.dumps(doc, ensure_ascii=False)))
            if job is not None:
                db.execute('INSERT OR REPLACE INTO novel_jobs VALUES(?,?,?,?)',
                    (job['id'], pid, job['key'], json.dumps(job, ensure_ascii=False)))


class NovelAdaptation:
    def __init__(self, engine, library, query=query_novel, runtime=capture_runtime):
        self.engine, self.library = engine, library
        self.store = NovelStore(library)
        self.query, self.runtime = query, runtime
        self.lock = threading.RLock()
        self.workers = set()
        self.slots = asyncio.Semaphore(1)
        self.closing = False
        self.engine.novel_busy = self.is_active
        for job in self.store.jobs():
            if job['status'] in ACTIVE:
                job.update(status='interrupted', updated_at=now(), error={'code':'TASK_INTERRUPTED',
                    'message':'服务重启，未完成的步骤已中断。已保存分析保留，请查看范围后恢复。'})
                doc = self.store.project(job['project_id'])
                variant = doc['variants'].get(job['signature'])
                if variant:
                    for chunk in variant['chunks']:
                        if chunk['status'] == 'running':
                            chunk['status'] = 'interrupted'
                doc['revision'] += 1
                self.store.save(job['project_id'], doc, job)

    def is_active(self, pid):
        return any(j['status'] in ACTIVE for j in self.store.jobs(pid))

    def task_summaries(self):
        summaries = {}
        for job in self.store.jobs():
            summaries[job['project_id']] = {'id':job['id'], 'status':job['status'], 'action':job['action']['action'],
                'step':job['step'], 'requests':len(job['calls']), 'max_requests':job['plan']['max_requests']}
        return summaries

    def _source(self, pid):
        state = self.engine.get_state(pid)
        if not state or not state.meta.get('ip_binding') or state.meta.get('parent_project_id'):
            raise LibraryError('IP_PROJECT_REQUIRED', '请从 IP 改编项目进入长篇改编', 404)
        meta = copy.deepcopy(state.meta)
        binding = meta['ip_binding']
        version = self.library.version(binding['ip_id'], binding['version_id'])
        # Reject corrupted references instead of analyzing another range/version.
        if (version['text_sha256'] != binding['text_sha256'] or
                version['chapters'][binding['first_chapter']-1:binding['last_chapter']] != binding['chapters']):
            raise LibraryError('SOURCE_CHANGED', '原文引用校验失败，请从 IP 详情重新绑定项目')
        return meta, version

    def _identity(self, meta, version, runtime):
        connection = text_connection(meta.get('llm_model', ''), runtime)
        prompts = {p.stem:p.read_text() for p in sorted(PROMPTS.glob('*.txt'))}
        limit = bounded_env('YC_NOVEL_CHUNK_CHARS', 6000, 1000, 12000)
        context_limit = bounded_env('YC_NOVEL_CONTEXT_CHARS', 100000, 10000, 200000)
        signature = digest({'schema':1, 'version':version['id'], 'text':version['text_sha256'],
            'chapters':meta['ip_binding']['chapters'], 'connection':connection['fingerprint'],
            'prompts':prompts, 'chunk_limit':limit, 'context_limit':context_limit,
            'episodes':meta['episodes'], 'duration':meta['target_duration'], 'direction':meta['ip_binding']['direction']})
        return signature, connection, prompts, limit, context_limit

    def _variant(self, doc, signature, meta, version, limit):
        return copy.deepcopy(doc['variants'].get(signature) or {'model':meta['llm_model'],
            'chunks':split_chapters(version['text'], meta['ip_binding']['chapters'], limit),
            'merges':[], 'bible':None, 'direction':None, 'direction_confirmed':False,
            'outline':None, 'outline_confirmed':False, 'scripts':{}, 'history':[]})

    @staticmethod
    def public_job(job):
        public = {k:copy.deepcopy(v) for k,v in job.items() if k not in {'key', 'request_hash'}}
        public['recovery_hint'] = legacy_recovery_hint(job)
        return public

    def detail(self, pid):
        with self.lock:
            meta, version = self._source(pid)
            doc = self.store.project(pid)
            warning, signature = '', doc['current']
            try:
                signature, _, _, limit, _ = self._identity(meta, version, self.runtime())
                variant = self._variant(doc, signature, meta, version, limit)
            except SettingsError as exc:
                warning = exc.message
                variant = copy.deepcopy(doc['variants'].get(doc['current']))
            return {'project_id':pid, 'title':meta['title'], 'revision':doc['revision'],
                'project_revision':meta.get('project_revision',0),
                'model':meta.get('llm_model',''), 'target_duration':meta['target_duration'], 'episode_count':meta['episodes'],
                'binding':meta['ip_binding'], 'config_warning':warning,
                'stale':bool(doc['current'] and signature != doc['current']), 'saved_versions':len(doc['variants']),
                'variant':variant, 'events':event_index(variant['chunks']) if variant else [],
                'jobs':[self.public_job(j) for j in self.store.jobs(pid)]}

    def _prepare(self, pid, action):
        meta, version = self._source(pid)
        item = self.library.detail(meta['ip_binding']['ip_id'])['item']
        if item['rights_status'] == 'pending' or not item['rights_basis'] or not item['rights_scope']:
            raise LibraryError('RIGHTS_REQUIRED', '作品使用依据已变更，请先在 IP 详情核对')
        runtime = self.runtime()
        signature, connection, prompts, limit, context_limit = self._identity(meta, version, runtime)
        # The new summary contract consumes existing verified chunks; it must not
        # invalidate paid extraction results or silently change old admissions.
        prompts['merge_summary'] = SUMMARY_PROMPT.read_text()
        prompts['planning_policy'] = PLANNING_PROMPT.read_text()
        doc = self.store.project(pid)
        variant = self._variant(doc, signature, meta, version, limit)
        chapters = action['chapter_ids']
        if len(chapters) != len(set(chapters)) or not set(chapters) <= {c['chapter_id'] for c in variant['chunks']}:
            raise LibraryError('INVALID_CHAPTERS', '请在绑定范围内选择章节', 422)
        if action['action'] != 'analysis' and chapters:
            raise LibraryError('INVALID_SCOPE', '只有全文分析可选择章节范围', 422)
        if action['action'] != 'script' and action['episode'] is not None:
            raise LibraryError('INVALID_SCOPE', '只有单集写作可选择集数', 422)
        pending = [c['id'] for c in variant['chunks'] if c['status'] != 'completed' and (not chapters or c['chapter_id'] in chapters)]
        merges = 0
        if action['action'] == 'analysis':
            all_after = all(c['status'] == 'completed' or c['id'] in pending for c in variant['chunks'])
            if all_after:
                merges = (len(variant['chunks'])+GROUP-1)//GROUP - len(variant['merges'])
            count = len(pending) + merges
            if not count:
                raise LibraryError('NOTHING_TO_ANALYZE', '所选范围已分析完成，没有待执行步骤')
            chars = sum(c['characters'] for c in variant['chunks'] if c['id'] in pending)
        else:
            if not variant['bible']:
                raise LibraryError('ANALYSIS_REQUIRED', '请先完成全部选定章节的分析和全局归并')
            if action['action'] in {'outline', 'script'} and not variant['direction_confirmed']:
                raise LibraryError('DIRECTION_REQUIRED', '请先保存并确认改编方向')
            if action['action'] == 'script' and not variant['outline_confirmed']:
                raise LibraryError('OUTLINE_REQUIRED', '请先保存并确认分集大纲')
            if action['action'] == 'script' and action['episode'] not in [e['number'] for e in variant['outline']['episodes']]:
                raise LibraryError('EPISODE_REQUIRED', '请选择大纲中的一集', 422)
            # Building now detects excessive context before admission or payment.
            data = self._prompt_data(action['action'], variant, meta, version, action['episode'])
            chars = len(json.dumps(data, ensure_ascii=False))
            self._prompt(action['action'], data, prompts, context_limit)
            count = 1
        retries = bounded_env('YC_NOVEL_REPAIR_ATTEMPTS', REPAIR_ATTEMPTS, 0, REPAIR_ATTEMPTS)
        max_requests = count * (1+retries)
        public = {'action':action['action'], 'episode':action['episode'], 'chapter_ids':chapters,
            'revision':doc['revision'], 'model':meta['llm_model'], 'provider_host':connection['host'],
            'input_characters':chars, 'pending_chunks':len(pending) if action['action']=='analysis' else 0,
            'merge_requests':merges, 'base_requests':count, 'max_requests':max_requests, 'max_output_tokens':max_requests*connection['max_tokens'],
            'images':0, 'video_seconds':0, 'retries':retries, 'cost':None,
            'analysis_policy':digest({'version':2, 'summary_prompt':prompts['merge_summary'], 'retries':retries}),
            'planning_policy':digest({'version':4, 'prompt':prompts['planning_policy'], 'retries':retries}) if action['action']!='analysis' else None,
            # Transport changes invalidate admissions, but already validated chunks
            # still satisfy the same content/schema contract and remain reusable.
            'request_options':request_options(connection),
            'config_version':runtime['version'], 'signature':signature}
        public['plan_hash'] = digest(public)
        return public, (doc, variant, meta, version, connection, prompts, context_limit, pending)

    def plan(self, pid, action):
        with self.engine._state_lock, self.lock:
            self.engine._require_idle(pid)
            return self._prepare(pid, action)[0]

    def submit(self, pid, request, key):
        with self.engine._state_lock, self.lock:
            if request.get('acknowledge_external') is not True:
                raise LibraryError('SCOPE_CONFIRMATION_REQUIRED', '请核对正文使用依据、供应商条件和本次请求范围', 422)
            request_hash = digest({'project_id':pid, **request})
            for previous in self.store.jobs():
                if previous['key'] == key:
                    if previous['request_hash'] != request_hash:
                        raise LibraryError('IDEMPOTENCY_CONFLICT', '同一操作标识不能用于不同生成内容')
                    return self.public_job(previous)
            if self.closing:
                raise LibraryError('SERVICE_STOPPING', '服务正在停止，请稍后重试')
            self.engine._require_idle(pid)
            action = Action.model_validate({k:request[k] for k in Action.model_fields}).model_dump()
            plan, args = self._prepare(pid, action)
            doc, variant, meta, version, connection, prompts, context_limit, pending = args
            if request['expected_revision'] != doc['revision'] or request['plan_hash'] != plan['plan_hash']:
                raise LibraryError('PLAN_CHANGED', '内容或模型配置已改变，请重新查看本次执行范围')
            jid = uuid.uuid4().hex
            job = {'id':jid, 'project_id':pid, 'key':key, 'request_hash':request_hash, 'signature':plan['signature'],
                'status':'queued', 'created_at':now(), 'updated_at':now(), 'action':action, 'plan':plan,
                'step':'排队中', 'calls':[], 'error':None}
            doc['current'] = plan['signature']
            doc['variants'][doc['current']] = variant
            doc['revision'] += 1
            self.store.save(pid, doc, job)
            worker = asyncio.create_task(self._run(jid, meta, version, connection, prompts, context_limit, pending))
            self.workers.add(worker)
            worker.add_done_callback(self._done)
            return self.public_job(job)

    def _done(self, worker):
        self.workers.discard(worker)
        if not worker.cancelled() and worker.exception():
            import logging
            logging.getLogger(__name__).error('Novel worker could not persist outcome (%s)', type(worker.exception()).__name__)

    def _check(self, jid):
        if self.store.job(jid)['status'] in {'stopping','stopped'}:
            raise Stopped()

    def _change(self, jid, change=None, **updates):
        with self.lock:
            job = self.store.job(jid)
            doc = self.store.project(job['project_id'])
            if change:
                change(doc['variants'][job['signature']])
            doc['revision'] += 1
            job.update(updates, updated_at=now())
            self.store.save(job['project_id'], doc, job)

    @staticmethod
    def _prompt(kind, data, prompts, limit):
        schemas = {'extract':ChunkAnalysis,'merge':StorySummary,'direction':Direction,'outline':OutlineDraft,'script':WrittenEpisode}
        policy = prompts.get('planning_policy', '') if kind in {'direction','outline','script'} else ''
        # Generation contracts evolved; legacy prompts continue to identify the
        # reusable analysis cache, but must not override current wire contracts.
        instruction = '' if kind in {'outline','script'} else prompts['merge_summary' if kind=='merge' else kind]
        spec = planning_schema(kind,schemas[kind],data)
        if kind=='script':
            spec = script_schema(spec,data)
        prompt = instruction + '\n' + policy + '\nSCHEMA\n' + json.dumps(spec, ensure_ascii=False) + '\nDATA\n' + json.dumps(data, ensure_ascii=False)
        if len(prompt) > limit:
            raise LibraryError('CONTEXT_TOO_LARGE', '本步骤上下文超出配置上限，未截断正文或提交请求；请缩小绑定范围或调整分集事件')
        return prompt

    async def _call(self, jid, kind, data, schema, validator, connection, prompts, limit):
        retries = self.store.job(jid)['plan']['retries']
        original_step = self.store.job(jid)['step']
        for attempt in range(retries+1):
            self._check(jid)
            try:
                return await self._call_once(jid, kind, data, schema, validator, connection, prompts, limit, attempt)
            except ModelOutputError as exc:
                if attempt >= retries or exc.diagnostic in {'content_filter', 'request_limit_exceeded'}:
                    if retries and attempt >= retries:
                        exc.message = f'已自动纠正 {retries} 次仍未通过。' + exc.message
                    raise
                self._check(jid)
                self._change(jid, step=f'{original_step} · 正在自动纠正 {attempt+1}/{retries}')
                data = {**data, 'output_correction':correction_feedback(exc.diagnostic, kind, data)}

    async def _call_once(self, jid, kind, data, schema, validator, connection, prompts, limit, attempt):
        self._check(jid)
        prompt = self._prompt(kind, data, prompts, limit)
        job = self.store.job(jid)
        require(len(job['calls']) < job['plan']['max_requests'], 'request_limit_exceeded')
        entry = {'kind':kind, 'input_characters':len(prompt), 'status':'sent', 'input_tokens':None,
                 'output_tokens':None, 'cost':None, 'elapsed_seconds':None,
                 'max_output_tokens':connection['max_tokens'], 'request_options':request_options(connection),
                 'attempt':attempt+1}
        self._change(jid, calls=job['calls']+[entry])
        started = time.monotonic()
        try:
            raw, usage = await asyncio.to_thread(self.query, prompt, connection)
            # Persist actual supplier usage even when structure/provenance is rejected.
            entry.update({k:usage.get(k) for k in ('input_tokens','output_tokens','elapsed_seconds','cost',
                'finish_reason','reasoning_tokens','has_reasoning','content_characters')})
            normalizer = (lambda value:extraction_fields(value, schema, data['text'])) if kind=='extract' else (lambda value:known_fields(value, schema))
            result = validate_response(raw, usage, schema, validator, normalizer)
            entry['status'] = 'completed'
            return result
        except Exception as exc:
            entry['status'] = 'error'
            if isinstance(exc, ModelOutputError):
                entry['diagnostic'] = exc.diagnostic
            entry['elapsed_seconds'] = round(time.monotonic()-started, 3)
            raise
        finally:
            job = self.store.job(jid)
            calls = job['calls']
            calls[-1] = entry
            self._change(jid, calls=calls)

    @staticmethod
    def _prompt_data(kind, variant, meta, version, number=None):
        data = {'task':kind, 'bible':variant['bible'], 'target_duration':meta['target_duration'], 'episode_count':meta['episodes']}
        if kind == 'direction':
            data['requested_direction'] = meta['ip_binding']['direction']
        else:
            data['direction'] = variant['direction']
            events = event_index(variant['chunks'])
            if kind == 'outline':
                data['bible'] = {key:variant['bible'][key] for key in ('summary','characters')}
                data['events'] = event_catalog(events)
                data['capacity'] = outline_limits(meta['target_duration'])
            else:
                outlines = variant['outline']['episodes']
                selected = next(e for e in outlines if e['number'] == number)
                data['bible'] = {'summary':variant['bible']['summary'],
                    'characters':[p for p in variant['bible']['characters'] if p['name'] in selected['characters']]}
                data['allowed_speakers'] = selected['characters']
                ids = {e['chunk_id'] for e in events if e['id'] in selected['event_ids']}
                previous = variant['scripts'].get(str(number-1), {})
                previous_value = previous.get('value') if not previous.get('stale') and previous.get('outline_hash') == digest(variant['outline']) else None
                data.update(selected_episode=selected, previous_outlines=outlines[:number-1],
                    previous_script=previous_value,
                    selected_events=[{'id':e['id'],'chapter':e['chapter_title'],'description':e['description']}
                                     for e in events if e['id'] in selected['event_ids']],
                    sources=[{'chapter_id':c['chapter_id'], 'start':c['start'], 'end':c['end'], 'text':version['text'][c['start']:c['end']]} for c in variant['chunks'] if c['id'] in ids])
        return data

    async def _run(self, jid, meta, version, connection, prompts, limit, pending):
        async with self.slots:
            chunk_id = None
            try:
                self._check(jid)
                self._change(jid, status='running', step='准备分析')
                job = self.store.job(jid)
                def current():
                    return self.store.project(job['project_id'])['variants'][job['signature']]
                if job['action']['action'] == 'analysis':
                    failures = []
                    for cid in pending:
                        self._check(jid)
                        chunk_id = cid
                        chunk = next(c for c in current()['chunks'] if c['id'] == cid)
                        def mark(v, status='running', result=None):
                            item = next(c for c in v['chunks'] if c['id'] == cid)
                            item.update(status=status, result=result)
                        self._change(jid, mark, step=f"分析 {chunk['title']} · {cid}")
                        source = version['text'][chunk['start']:chunk['end']]
                        def extracted(value):
                            validate_people(value)
                            for event in value['events']:
                                position = source.find(event['quote'])
                                require(position >= 0, 'quote_not_in_source')
                                event.update(start=chunk['start']+position, end=chunk['start']+position+len(event['quote']))
                            return value
                        try:
                            result = await self._call(jid, 'extract', {'chapter':chunk['title'],'text':source}, ChunkAnalysis, extracted, connection, prompts, limit)
                        except ModelOutputError as exc:
                            self._change(jid, lambda v:mark(v, 'error'))
                            failures.append(exc)
                            chunk_id = None
                            continue
                        self._change(jid, lambda v:mark(v,'completed',result))
                        chunk_id = None
                    if failures:
                        error = failures[-1]
                        error.message = f'已处理本次全部分片，其中 {len(failures)} 个经自动纠正仍未通过校验；其余结果已保存。' + error.message
                        raise error
                    variant = current()
                    if all(c['status']=='completed' for c in variant['chunks']):
                        groups = [variant['chunks'][i:i+GROUP] for i in range(0,len(variant['chunks']),GROUP)]
                        for group in groups[len(variant['merges']):]:
                            self._check(jid)
                            prior = current()['merges'][-1] if current()['merges'] else None
                            batch = [c['result'] for c in group]
                            registry, ambiguities = source_registry(prior, batch)
                            self._change(jid, step=f"归并人物与关系 · {len(current()['merges'])+1}/{len(groups)}")
                            result = await self._call(jid,'merge',{'prior':prior,
                                'batch':[{'summary':c['summary']} for c in batch], 'registry':registry},StorySummary,
                                lambda v:assemble_summary(v,registry,ambiguities),connection,prompts,limit)
                            self._change(jid, lambda v:v['merges'].append(result))
                        self._change(jid, lambda v:v.update(bible=v['merges'][-1]))
                else:
                    self._check(jid)
                    variant = current()
                    kind, number = job['action']['action'], job['action']['episode']
                    self._change(jid, step={'direction':'提出改编方向','outline':'规划分集与事件取舍','script':f'编写第 {number} 集'}[kind])
                    data = self._prompt_data(kind,variant,meta,version,number)
                    events = event_index(variant['chunks'])
                    schema = {'direction':Direction,'outline':OutlineDraft,'script':WrittenEpisode}[kind]
                    if kind == 'direction':
                        validator = lambda v:validate_direction(v,variant['bible'])
                    elif kind == 'outline':
                        validator = lambda v:expand_outline(v,variant['bible'],events,meta['episodes'],meta['target_duration'])
                    else:
                        validator = lambda v:finalize_script(v,data['selected_episode'],meta['target_duration'],events,version['text'],variant['bible'])
                    result = await self._call(jid,kind,data,schema,validator,connection,prompts,limit)
                    def save(v):
                        if kind == 'script':
                            old = v['scripts'].get(str(number))
                            if old:
                                v['history'].append({'kind':'script','number':number,**old})
                            if not old or old['value'] != result:
                                for other, saved in v['scripts'].items():
                                    if int(other) > number:
                                        saved['stale'] = True
                            v['scripts'][str(number)] = {'value':result,'created_at':now(),'outline_hash':digest(v['outline']), 'session_id':None}
                        else:
                            self._set_review(v,kind,result,False)
                    self._change(jid, save)
                self._check(jid)
                self._change(jid, status='completed', step='本次范围已完成')
            except (Exception, asyncio.CancelledError) as exc:
                stopped = isinstance(exc,Stopped) or self.store.job(jid)['status']=='stopping'
                def failed(v):
                    if chunk_id:
                        for c in v['chunks']:
                            if c['id'] == chunk_id and c['status'] != 'completed':
                                c['status'] = 'stopped' if stopped else 'error'
                code = 'TASK_STOPPED' if stopped else exc.code if isinstance(exc,(LibraryError,SettingsError,ModelOutputError)) else 'NOVEL_REQUEST_FAILED'
                message = '任务已停止，已完成结果保留。' if stopped else exc.message if isinstance(exc,(LibraryError,SettingsError,ModelOutputError)) else '文本请求失败，可能为连接、超时或服务配置问题；已完成结果保留，请查看范围后重试。'
                self._change(jid, failed, status='stopped' if stopped else 'error', error={'code':code,'message':message})

    @staticmethod
    def _set_review(variant, kind, value, confirm):
        changed = variant[kind] != value
        if variant[kind] is not None and changed:
            variant['history'].append({'kind':kind,'value':variant[kind],'created_at':now()})
        variant[kind] = value
        variant[kind+'_confirmed'] = confirm
        if kind == 'direction' and changed:
            variant['outline_confirmed'] = False
            variant['outline_direction_hash'] = None
        if kind == 'outline':
            variant['outline_direction_hash'] = digest(variant['direction'])
        if changed:
            # Preserve generated work, but an edited plan must not silently reuse it.
            for script in variant['scripts'].values():
                script['stale'] = True

    def review(self, pid, kind, value, revision, confirm):
        with self.engine._state_lock, self.lock:
            self.engine._require_idle(pid)
            meta, version = self._source(pid)
            signature,_,_,_,_ = self._identity(meta,version,self.runtime())
            doc = self.store.project(pid)
            if doc['revision'] != revision or signature != doc['current']:
                raise LibraryError('PROJECT_CHANGED','内容或模型配置已变化，请刷新后核对，输入已保留')
            variant = doc['variants'][signature]
            if not variant['bible']:
                raise LibraryError('ANALYSIS_REQUIRED','请先完成全文分析')
            if kind == 'direction':
                validate_direction(value,variant['bible'])
            else:
                if not variant['direction_confirmed']:
                    raise LibraryError('DIRECTION_REQUIRED','请先确认改编方向')
                validate_outline(value,variant['bible'],event_index(variant['chunks']),meta['episodes'])
            self._set_review(variant,kind,value,confirm)
            doc['revision'] += 1
            self.store.save(pid,doc)
            return self.detail(pid)

    def stop(self, pid, jid):
        with self.lock:
            job = self.store.job(jid)
            if job['project_id'] != pid:
                raise LibraryError('TASK_NOT_FOUND','找不到此项目的任务',404)
            if job['status'] in ACTIVE:
                self._change(jid,status='stopping',step='停止中，等待当前请求返回')
            return self.public_job(self.store.job(jid))

    def publish(self, pid, number, revision):
        with self.engine._state_lock, self.lock:
            self.engine._require_idle(pid)
            meta,version = self._source(pid)
            signature,_,_,_,_ = self._identity(meta,version,self.runtime())
            doc = self.store.project(pid)
            if doc['revision'] != revision or signature != doc['current']:
                raise LibraryError('PROJECT_CHANGED','内容或配置已变化，请刷新后重试')
            variant = doc['variants'][signature]
            script = variant['scripts'].get(str(number))
            if not script or script.get('stale') or not variant['outline_confirmed'] or not variant['direction_confirmed']:
                raise LibraryError('SCRIPT_REQUIRED','请先确认方向和大纲，并生成对应版本的本集剧本')
            if script.get('session_id') and self.engine.get_state(script['session_id']):
                return {'session_id':script['session_id']}
            # Deterministic ID makes a crash between the two databases recoverable.
            key = str(uuid.uuid5(uuid.NAMESPACE_URL,f"novel:{pid}:{signature}:{number}:{digest(script['value'])}"))
            result,created = self.engine.create_episode(pid,{'title':script['value']['title'],'target_duration':meta['target_duration']},key,digest({'novel':key}))
            sid = result['session_id']
            state = self.engine.get_state(sid)
            if created or not state.artifacts.get('script_generation'):
                artifact = self._artifact(sid,number,variant,script,meta)
                self.engine.update_artifact(sid,'script_generation',artifact)
            if not state.meta.get('novel_origin'):
                state.meta['novel_origin'] = {'project_id':pid,'episode':number,'signature':signature,'outline_hash':digest(variant['outline'])}
                state.meta['episode_number'] = number
                self.engine.save_session_to_disk(sid)
            script['session_id'] = sid
            doc['revision'] += 1
            self.store.save(pid,doc)
            return {'session_id':sid}

    @staticmethod
    def _artifact(sid, number, variant, script, meta):
        value = script['value']
        used_people = {name for scene in value['scenes'] for name in scene['characters']}
        locations = list(dict.fromkeys(s['location'] for s in value['scenes']))
        lines = []
        for i,scene in enumerate(value['scenes'],1):
            lines.append(f"场 {i} · {scene['location']} · {scene['duration']}秒" + ('【创作补充】' if scene['creative_addition'] else ''))
            lines.append(scene['action'])
            lines.extend(f"{d['speaker']}：{d['text']}"+('【改编对白】' if d['creative_addition'] else '') for d in scene['dialogue'])
        return validate_script({'title':value['title'],'characters':[{'name':p['name'],'description':p['description'],
            'character_id':f'char_{i}'} for i,p in enumerate(variant['bible']['characters']) if p['name'] in used_people],
            'settings':[{'name':name,'description':name,'setting_id':f'set_{i}'} for i,name in enumerate(locations)],
            'episodes':[{'episode_number':1,'act_title':value['title'],'content':'\n'.join(lines)}],
            'source_map':{'ip_id':meta['ip_binding']['ip_id'],'version_id':meta['ip_binding']['version_id'],
                'adaptation_episode':number,'scenes':value['scenes'],'events':[e for e in event_index(variant['chunks']) if e['id'] in {eid for s in value['scenes'] for eid in s['event_ids']}],
                'manually_edited':False},'session_id':sid})

    async def shutdown(self):
        self.closing = True
        for job in self.store.jobs():
            if job['status'] in ACTIVE:
                self.stop(job['project_id'],job['id'])
        if self.workers:
            await asyncio.gather(*list(self.workers), return_exceptions=True)

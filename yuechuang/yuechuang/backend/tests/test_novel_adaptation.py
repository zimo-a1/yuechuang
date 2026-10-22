"""Synthetic novel + deterministic model substitute. Never a real generation result."""
import asyncio
import copy
import json
import threading
from uuid import uuid4
import httpx
import pytest
from core.ip_library import IPLibrary, LibraryError, digest
from core.ip_parser import chapter_index
from core.orchestrator import WorkflowEngine
from core.novel_chunks import split_chapters
from core.novel_adaptation import NovelAdaptation
from core.novel_contracts import Action
from core.workflow_rules import WorkflowConflict

PERSON = {'name':'林舟','aliases':['小林'],'description':'负责灯塔修缮的青年。'}
RUNTIME = {'attributes':{'DEEPSEEK_BASE_URL':'https://test.invalid/v1','DEEPSEEK_API_KEY':'test-only-not-a-real-key','CONFIG':{}},
           'services':{'revision':1,'connections':[]},'version':'test-1'}
TEXT = '第一章 来信\n林舟收到一封信。\n第二章 出发\n林舟推开灯塔的门。\n'


class FakeModel:
    def __init__(self):
        self.calls=[]
        self.fail=None
        self.invalid=None
        self.wait=None

    def __call__(self,prompt,connection):
        data=json.loads(prompt.split('\nDATA\n')[1]);self.calls.append(data)
        if self.wait:
            self.wait[0].set();self.wait[1].wait(5)
        if self.fail and self.fail(data):
            raise RuntimeError('PRIVATE-UPSTREAM-BODY test-only-not-a-real-key')
        if 'text' in data:
            result={'summary':'林舟向灯塔出发。','characters':[PERSON],'relationships':[],
                    'events':[{'description':'章节关键动作','quote':data['text'].splitlines()[-1]}]}
        elif 'batch' in data:
            result={'summary':'林舟收到信后进入灯塔。','characters':[PERSON],'relationships':[], 'open_threads':['信是谁寄来的？']}
        elif 'requested_direction' in data:
            result={'premise':'追查信的来源','tone':'悬疑','focus_characters':['林舟'],'changes':'合并赶路过程，保留来信和入塔事件。'}
        elif 'events' in data:
            result={'episodes':[{'number':i+1,'title':f'灯塔第{i+1}集','summary':'林舟踏上旅途。',
                        'complete_event':f'第{i+1}集走完灯塔这一步','hook':'这封信是谁寄的' if i==0 else '',
                        'characters':['林舟'],
                        'event_ids':[e['id'] for e in data['events'][i::data['episode_count']]],'creative_additions':'增加自言自语。'} for i in range(data['episode_count'])], 'omitted_event_reason':'仅聚焦灯塔主线，其余事件暂不纳入。', 'omissions':[]}
        else:
            result={'title':data['selected_episode']['title'],'scenes':[{'location':'灯塔','characters':['林舟'],
                    'duration':data['target_duration'],'action':'林舟缓缓推开门。','dialogue':[{'speaker':'林舟','text':'我到了。','creative_addition':True}],
                    'event_ids':data['selected_episode']['event_ids'],'creative_addition':True}]}
        if self.invalid:
            result=self.invalid(result)
        return json.dumps(result,ensure_ascii=False),{'input_tokens':100,'output_tokens':50,'elapsed_seconds':0.1,'cost':None}


def make_service(tmp_path,text=TEXT,engine=None):
    lib=IPLibrary(tmp_path/'ip');engine=engine or WorkflowEngine(str(tmp_path/'legacy'),str(tmp_path/'workflow.sqlite3'))
    meta={'title':'合成测试 · 灯塔来信','author':'测试程序','genre':'悬疑','synopsis':'合成文本',
          'rights_status':'owned','rights_basis':'测试程序生成','rights_scope':'隔离离线测试','content_scope':'full'}
    parsed={'text':text,'characters':len(text),'line_count':len(text.splitlines()),'encoding':'utf-8','warnings':[],'chapters':chapter_index(text)}
    ipid,_=lib.import_file(meta,parsed,text.encode(),'synthetic.txt',str(uuid4()))
    version=lib.detail(ipid)['versions'][0]
    binding=lib.project_binding(ipid,{'version_id':version['id'],'expected_revision':1,'first_chapter':1,'last_chapter':len(version['chapters']),'direction':'保留灯塔悬疑'})
    project,_=engine.create_draft({'title':meta['title'],'ip_binding':binding,'source_type':'ip','project_container':True,
          'episodes':2,'target_duration':60,'llm_model':'deepseek-flash'},str(uuid4()),'test')
    fake=FakeModel();runtime=copy.deepcopy(RUNTIME)
    service=NovelAdaptation(engine,lib,query=fake,runtime=lambda:copy.deepcopy(runtime))
    return service,project['session_id'],fake,runtime


@pytest.fixture
def setup(tmp_path):
    values=make_service(tmp_path)
    yield values
    values[0].engine.store.close()


def request_for(service,pid,kind='analysis',chapters=None,episode=None):
    action=Action(action=kind,chapter_ids=chapters or [],episode=episode).model_dump()
    plan=service.plan(pid,action)
    return {**action,'expected_revision':plan['revision'],'plan_hash':plan['plan_hash'],'acknowledge_external':True}


async def run(service,pid,kind='analysis',chapters=None,episode=None):
    job=service.submit(pid,request_for(service,pid,kind,chapters,episode),str(uuid4()))
    await asyncio.gather(*list(service.workers))
    return service.store.job(job['id'])


def confirm(service,pid,kind):
    detail=service.detail(pid)
    return service.review(pid,kind,detail['variant'][kind],detail['revision'],True)


@pytest.mark.parametrize('length',[30000,50000,100000])
def test_lossless_ranges_including_long_paragraph_and_unicode(length):
    text='第一章 长章\n\n' + ('甲😀 乙\t丙\n'*length)[:length]+'\n第二章 无换行\n'+'甲'*13000+'\n  '
    chapters=chapter_index(text);chunks=split_chapters(text,chapters)
    assert ''.join(text[c['start']:c['end']] for c in chunks)==text
    assert all(c['characters']<=6000 for c in chunks)
    assert all(a['end']==b['start'] for a,b in zip(chunks,chunks[1:]))
    assert any(c['hard_split'] for c in chunks)


async def test_full_flow_selected_episode_confirmations_usage_and_restart(setup,tmp_path):
    service,pid,fake,_=setup
    for kind in ('direction','outline','script'):
        with pytest.raises(LibraryError):request_for(service,pid,kind,episode=1 if kind=='script' else None)
    assert (await run(service,pid))['status']=='completed'
    assert len(fake.calls)==3
    detail=service.detail(pid);assert len(detail['events'])==2
    for e in detail['events']:assert TEXT[e['start']:e['end']]==e['quote']
    assert (await run(service,pid,'direction'))['status']=='completed'
    with pytest.raises(LibraryError):request_for(service,pid,'outline')
    confirm(service,pid,'direction')
    assert (await run(service,pid,'outline'))['status']=='completed'
    with pytest.raises(LibraryError):request_for(service,pid,'script',episode=2)
    confirm(service,pid,'outline')
    assert (await run(service,pid,'script',episode=2))['status']=='completed'
    data=fake.calls[-1];assert data['selected_episode']['number']==2 and len(data['previous_outlines'])==1
    assert data['sources'][0]['text']=='第二章 出发\n林舟推开灯塔的门。\n'
    assert set(service.detail(pid)['variant']['scripts'])=={'2'}
    detail=service.detail(pid);published=service.publish(pid,2,detail['revision'])
    state=service.engine.get_state(published['session_id'])
    assert state.meta['episode_number']==2 and not state.confirmed_stages
    assert state.artifacts['script_generation']['source_map']['adaptation_episode']==2
    assert state.artifacts['script_generation']['source_map']['events'][0]['chapter_id']=='ch-2'
    assert len(fake.calls)==6 and len(service.engine.project_detail(pid)['episodes'])==1
    assert service.publish(pid,2,service.detail(pid)['revision'])==published
    before=service.detail(pid)
    restored=NovelAdaptation(service.engine,IPLibrary(tmp_path/'ip'),query=fake,runtime=lambda:copy.deepcopy(RUNTIME))
    assert restored.detail(pid)==before
    assert sum(len(j['calls']) for j in before['jobs'])==6
    assert all(c['input_tokens']==100 for j in before['jobs'] for c in j['calls'])


async def test_failure_retry_only_selected_missing_chapter_and_merge(setup):
    service,pid,fake,_=setup;fake.fail=lambda d:'text' in d and '第二章' in d['text']
    result=await run(service,pid)
    assert result['status']=='error' and 'PRIVATE' not in json.dumps(result)
    detail=service.detail(pid);assert [c['status'] for c in detail['variant']['chunks']]==['completed','error']
    assert detail['variant']['bible'] is None
    fake.fail=None
    plan=service.plan(pid,Action(action='analysis',chapter_ids=['ch-2']).model_dump())
    assert (plan['pending_chunks'],plan['merge_requests'])==(1,1)
    assert (await run(service,pid,chapters=['ch-2']))['status']=='completed'
    assert sum('text' in d and '第一章' in d['text'] for d in fake.calls)==1


async def test_partial_analysis_is_not_full_success(setup):
    service,pid,_,_=setup
    assert (await run(service,pid,chapters=['ch-1']))['status']=='completed'
    assert service.detail(pid)['variant']['bible'] is None
    with pytest.raises(LibraryError):request_for(service,pid,'direction')
    plan=service.plan(pid,Action(action='analysis').model_dump())
    assert plan['pending_chunks']==1


async def test_duplicate_admission_busy_delete_and_plan_changed(setup):
    service,pid,fake,runtime=setup
    request=request_for(service,pid);key=str(uuid4())
    runtime['version']='changed'
    with pytest.raises(LibraryError,match='改变'):service.submit(pid,request,key)
    assert not fake.calls
    request=request_for(service,pid)
    first=service.submit(pid,request,key)
    assert service.submit(pid,request,key)['id']==first['id']
    assert not service.engine.delete_session(pid)
    with pytest.raises(WorkflowConflict):service.engine.update_source(pid,'changed',0)
    with pytest.raises(WorkflowConflict):service.submit(pid,request,str(uuid4()))
    await asyncio.gather(*list(service.workers))
    assert len(fake.calls)==3
    assert service.submit(pid,request,key)['id']==first['id']
    with pytest.raises(LibraryError):service.submit(pid,{**request,'episode':2},key)


async def test_stop_current_request_saves_result_no_followup_calls(setup):
    service,pid,fake,_=setup;started,release=threading.Event(),threading.Event();fake.wait=(started,release)
    job=service.submit(pid,request_for(service,pid),str(uuid4()))
    assert await asyncio.to_thread(started.wait,2)
    service.stop(pid,job['id']);release.set();await asyncio.gather(*list(service.workers))
    assert service.store.job(job['id'])['status']=='stopped' and len(fake.calls)==1
    assert service.detail(pid)['variant']['chunks'][0]['status']=='completed'


async def test_restart_marks_inflight_and_preserves_completed(setup):
    service,pid,_,_=setup
    await run(service,pid,chapters=['ch-1'])
    doc=service.store.project(pid);job=service.store.jobs(pid)[-1]
    job['status']='running';doc['variants'][doc['current']]['chunks'][1]['status']='running';service.store.save(pid,doc,job)
    restored=NovelAdaptation(service.engine,service.library,query=service.query,runtime=service.runtime)
    detail=restored.detail(pid)
    assert detail['jobs'][-1]['status']=='interrupted'
    assert [c['status'] for c in detail['variant']['chunks']]==['completed','interrupted']
    assert restored.plan(pid,Action(action='analysis').model_dump())['pending_chunks']==1


async def test_configuration_invalidates_cache_old_version_retained(setup):
    service,pid,_,runtime=setup;await run(service,pid)
    runtime['version']='settings-2'
    detail=service.detail(pid);assert detail['stale'] and not detail['variant']['bible']
    await run(service,pid)
    assert service.detail(pid)['saved_versions']==2


@pytest.mark.parametrize('corrupt',[
    lambda r:{**r,'events':[{'description':'捏造','quote':'不存在的原文引文'}]},
    lambda r:{**r,'relationships':[{'source':'陌生人','target':'林舟','description':'兄弟'}]},
    lambda r:{**r,'characters':[PERSON,PERSON]},
])
async def test_invalid_extraction_never_marked_complete(setup,corrupt):
    service,pid,fake,_=setup;fake.invalid=corrupt
    result=await run(service,pid)
    assert result['status']=='error' and result['error']['code']=='MODEL_OUTPUT_INVALID'
    assert len(fake.calls)==6 and all(c['status']=='error' for c in service.detail(pid)['variant']['chunks'])
    assert [c['attempt'] for c in result['calls']]==[1,2,3,1,2,3]
    assert result['calls'][0]['input_tokens']==100


async def test_outline_omissions_unknown_people_revision_and_direction_invalidation(setup):
    service,pid,_,_=setup;await run(service,pid);await run(service,pid,'direction');confirm(service,pid,'direction');await run(service,pid,'outline')
    detail=service.detail(pid);value=copy.deepcopy(detail['variant']['outline']);value['episodes'][0]['event_ids']=['invented']
    with pytest.raises(ValueError):service.review(pid,'outline',value,detail['revision'],True)
    confirm(service,pid,'outline');await run(service,pid,'script',episode=1)
    with pytest.raises(LibraryError):service.review(pid,'outline',detail['variant']['outline'],detail['revision'],True)
    current=service.detail(pid);direction={**current['variant']['direction'],'tone':'温暖'}
    service.review(pid,'direction',direction,current['revision'],True)
    assert not service.detail(pid)['variant']['outline_confirmed']
    assert service.detail(pid)['variant']['scripts']['1']['stale']
    with pytest.raises(LibraryError):service.publish(pid,1,service.detail(pid)['revision'])


async def test_api_contract_errors_no_key_and_no_calls_on_plan(setup,monkeypatch):
    service,pid,fake,_=setup
    from api.app import app
    import api.routers.novel as router
    monkeypatch.setattr(router,'get_novel',lambda:service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        base=f'/api/projects/{pid}/adaptation'
        assert (await client.get(base)).status_code==200
        response=await client.post(base+'/plan',json={'action':'analysis'})
        assert response.status_code==200 and not fake.calls
        request=request_for(service,pid)
        denied=await client.post(base+'/tasks',json={**request,'acknowledge_external':False},headers={'Idempotency-Key':str(uuid4())})
        assert denied.status_code==422
        start=await client.post(base+'/tasks',json=request,headers={'Idempotency-Key':str(uuid4())})
        assert start.status_code==202,start.text
        await asyncio.gather(*list(service.workers))
        result=await client.get(base)
        assert result.status_code==200 and 'test-only-not-a-real-key' not in result.text
        assert result.json()['variant']['bible']
        assert (await client.get('/api/projects/nope/adaptation')).status_code==404


async def test_merge_failure_resumes_without_reanalyzing_nine_chapters(tmp_path):
    text=''.join(f'第{i}章 灯塔\n林舟收到第{i}封信。\n' for i in range(1,10))
    service,pid,fake,_=make_service(tmp_path,text)
    try:
        fake.fail=lambda data:'batch' in data and data['prior'] is not None
        failed=await run(service,pid)
        assert failed['status']=='error' and len(fake.calls)==11
        assert all(c['status']=='completed' for c in service.detail(pid)['variant']['chunks'])
        assert service.detail(pid)['variant']['bible'] is None
        fake.fail=None
        plan=service.plan(pid,Action(action='analysis').model_dump());assert plan['base_requests']==1 and plan['max_requests']==3
        assert (await run(service,pid))['status']=='completed'
        assert len(fake.calls)==12
    finally:service.engine.store.close()


@pytest.mark.parametrize('corrupt,reconciled',[
    (lambda r:{**r,'scenes':[{**r['scenes'][0],'duration':12}]},False),
    (lambda r:{**r,'scenes':[{**r['scenes'][0],'dialogue':[{'speaker':'陌生人','text':'错人','creative_addition':True}]}]},False),
    (lambda r:{**r,'scenes':[{**r['scenes'][0],'dialogue':[{'speaker':'林舟','text':'虚构的原文引语','creative_addition':False}]}]},True),
    (lambda r:{**r,'scenes':[{**r['scenes'][0],'event_ids':[]}]},False),
])
async def test_invalid_script_not_published(setup,corrupt,reconciled):
    service,pid,fake,_=setup;await run(service,pid);await run(service,pid,'direction');confirm(service,pid,'direction');await run(service,pid,'outline');confirm(service,pid,'outline')
    fake.invalid=corrupt;job=await run(service,pid,'script',episode=1)
    if reconciled:
        # The model's false attribution is no longer trusted. Preserve the line
        # as visibly adapted dialogue, never save it as an original quotation.
        assert job['status']=='completed' and len(job['calls'])==1
        line=service.detail(pid)['variant']['scripts']['1']['value']['scenes'][0]['dialogue'][0]
        assert line=={'speaker':'林舟','text':'虚构的原文引语','creative_addition':True}
    else:
        assert job['status']=='error' and job['error']['code']=='MODEL_OUTPUT_INVALID'
        assert not service.detail(pid)['variant']['scripts']
    assert not service.engine.project_detail(pid)['episodes']


async def test_changed_prior_episode_marks_later_script_stale(setup):
    service,pid,_,_=setup;await run(service,pid);await run(service,pid,'direction');confirm(service,pid,'direction');await run(service,pid,'outline');confirm(service,pid,'outline')
    await run(service,pid,'script',episode=2);await run(service,pid,'script',episode=1)
    assert service.detail(pid)['variant']['scripts']['2']['stale']


async def test_prompt_invalidation_and_context_preflight(setup,monkeypatch,tmp_path):
    service,pid,fake,_=setup;await run(service,pid)
    import core.novel_adaptation as module
    prompts=tmp_path/'prompts';prompts.mkdir()
    for p in module.PROMPTS.glob('*.txt'):(prompts/p.name).write_text(p.read_text()+'\n新版模板')
    monkeypatch.setattr(module,'PROMPTS',prompts)
    assert service.detail(pid)['stale'] and service.plan(pid,Action(action='analysis').model_dump())['pending_chunks']==2
    with pytest.raises(LibraryError,match='未截断'):module.NovelAdaptation._prompt('direction',{'text':'甲'*10001},{'direction':'test'},10000)
    assert len(fake.calls)==3


def test_transport_has_one_attempt_and_returns_usage(monkeypatch):
    import openai
    from models.novel_text import query_novel,text_connection
    from types import SimpleNamespace as NS
    calls=[]
    class Client:
        def __init__(self,**kwargs):
            assert kwargs['max_retries']==0 and kwargs['timeout']==180
            self.chat=NS(completions=NS(create=self.create))
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def create(self,**kwargs):
            calls.append(kwargs)
            return NS(choices=[NS(message=NS(content='{}'))],usage=NS(prompt_tokens=7,completion_tokens=9))
    monkeypatch.setattr(openai,'OpenAI',Client)
    raw,metrics=query_novel('synthetic',text_connection('deepseek-flash',RUNTIME))
    assert raw=='{}' and metrics['input_tokens']==7 and metrics['output_tokens']==9 and len(calls)==1
    assert calls[0]['response_format']=={'type':'json_object'}
    assert calls[0]['extra_body']=={'thinking':{'type':'disabled'}}


@pytest.mark.parametrize('raw,usage,code,diagnostic',[
    ('',{'finish_reason':'length','has_reasoning':True,'reasoning_tokens':11999},'MODEL_OUTPUT_TRUNCATED','output_truncated'),
    ('{}',{'finish_reason':'length'},'MODEL_OUTPUT_TRUNCATED','output_truncated'),
    ('',{'finish_reason':'stop','has_reasoning':True},'MODEL_REASONING_ONLY','reasoning_only'),
    ('',{'finish_reason':'stop'},'MODEL_OUTPUT_INVALID','empty_response'),
    ('{"summary":',{'finish_reason':'stop'},'MODEL_OUTPUT_INVALID','invalid_json'),
    ('{}',{'finish_reason':'aborted'},'MODEL_OUTPUT_INVALID','incomplete_response'),
    ('{}',{'finish_reason':'content_filter'},'MODEL_OUTPUT_INVALID','content_filter'),
])
async def test_incomplete_response_preserves_usage_with_bounded_repairs(setup,raw,usage,code,diagnostic):
    service,pid,fake,_=setup
    await run(service,pid,chapters=['ch-1'])
    saved=copy.deepcopy(service.detail(pid)['variant']['chunks'][0])
    requests=[]
    def failed_query(prompt,connection):
        requests.append(prompt)
        return raw,{'input_tokens':2499,'output_tokens':11999,'elapsed_seconds':1,'cost':None,**usage}
    service.query=failed_query
    job=await run(service,pid)
    assert job['status']=='error' and job['error']['code']==code
    assert len(requests)==(1 if diagnostic=='content_filter' else 3)
    call=job['calls'][0]
    assert call['diagnostic']==diagnostic and call['output_tokens']==11999
    assert call['finish_reason']==usage['finish_reason']
    assert service.detail(pid)['variant']['chunks'][0]==saved
    restored=NovelAdaptation(service.engine,service.library,query=fake,runtime=service.runtime)
    plan=restored.plan(pid,Action(action='analysis',chapter_ids=['ch-2']).model_dump())
    assert plan['pending_chunks']==1 and plan['merge_requests']==1
    assert (await run(restored,pid,chapters=['ch-2']))['status']=='completed'
    assert restored.detail(pid)['variant']['chunks'][0]==saved


def test_actual_sdk_serialization_and_safe_completion_diagnostics(monkeypatch):
    import httpx
    from models.novel_text import query_novel,text_connection,request_options
    sent=[]
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'synthetic','object':'chat.completion','created':1,'model':'deepseek-flash',
            'choices':[{'index':0,'message':{'role':'assistant','content':None,'reasoning_content':'PRIVATE REASONING'},'finish_reason':'length'}],
            'usage':{'prompt_tokens':2499,'completion_tokens':11999,'total_tokens':14498,'completion_tokens_details':{'reasoning_tokens':11999}}})
    original=httpx.Client
    class TestClient(original):
        def __init__(self,**kw):
            super().__init__(transport=httpx.MockTransport(handler),timeout=kw['timeout'])
    monkeypatch.setattr(httpx,'Client',TestClient)
    raw,usage=query_novel('Return JSON for synthetic sample',text_connection('deepseek-flash',RUNTIME))
    assert len(sent)==1 and sent[0]['thinking']=={'type':'disabled'}
    assert sent[0]['response_format']=={'type':'json_object'} and sent[0]['max_tokens']==12000
    assert raw=='' and usage['finish_reason']=='length' and usage['reasoning_tokens']==11999
    assert usage['has_reasoning'] and usage['content_characters']==0
    assert 'PRIVATE' not in json.dumps(usage)
    for provider in ('openai','agnes'):assert request_options({'provider':provider})=={}


async def test_transport_policy_invalidates_old_plan_not_validated_chunks(setup,monkeypatch):
    import core.novel_adaptation as module
    service,pid,fake,_=setup
    original=module.request_options
    monkeypatch.setattr(module,'request_options',lambda _: {})
    await run(service,pid,chapters=['ch-1'])
    before=service.detail(pid)
    request=request_for(service,pid)
    monkeypatch.setattr(module,'request_options',original)
    after=service.detail(pid)
    assert not after['stale'] and after['variant']==before['variant']
    with pytest.raises(LibraryError,match='改变'):service.submit(pid,request,str(uuid4()))
    assert len(fake.calls)==1
    assert service.plan(pid,Action(action='analysis').model_dump())['pending_chunks']==1


def test_old_failure_advice_is_inference_without_rewriting_history():
    job={'plan':{'model':'deepseek-flash','max_requests':14,'max_output_tokens':168000},
         'error':{'code':'MODEL_OUTPUT_INVALID'},'calls':[{'status':'error','output_tokens':11999}]}
    original=copy.deepcopy(job)
    public=NovelAdaptation.public_job(job)
    assert '可能' in public['recovery_hint'] and '旧记录' in public['recovery_hint']
    assert job==original and 'finish_reason' not in public['calls'][0]
    job['calls'][0]['finish_reason']='stop'
    assert not NovelAdaptation.public_job(job)['recovery_hint']


async def test_response_schema_keeps_failure_diagnostics(setup,monkeypatch):
    from api.app import app
    import api.routers.novel as router
    service,pid,_,_=setup
    service.query=lambda *_:('',{'finish_reason':'length','input_tokens':2499,'output_tokens':11999,
                                  'reasoning_tokens':11999,'has_reasoning':True,'content_characters':0})
    await run(service,pid)
    monkeypatch.setattr(router,'get_novel',lambda:service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.get(f'/api/projects/{pid}/adaptation')
    assert response.status_code==200
    job=response.json()['jobs'][-1]
    assert job['error']['code']=='MODEL_OUTPUT_TRUNCATED'
    assert job['calls'][0]['diagnostic']=='output_truncated'
    assert job['calls'][0]['reasoning_tokens']==11999 and job['calls'][0]['finish_reason']=='length'
    assert 'recovery_hint' in job


def test_non_text_service_rejected_before_dispatch():
    from models.novel_text import text_connection
    from core.service_settings import SettingsError
    with pytest.raises(SettingsError,match='文本模型'):
        text_connection('svc/connection/grok-imagine-video-1.5',RUNTIME)


def test_real_process_termination_and_resume(tmp_path):
    import subprocess,sys,time
    service,pid,_,_=make_service(tmp_path);service.engine.store.close()
    code='''import asyncio,json,sys,time
from pathlib import Path
from tests.test_novel_adaptation import FakeModel,RUNTIME,request_for
from core.novel_adaptation import NovelAdaptation
from core.ip_library import IPLibrary
from core.orchestrator import WorkflowEngine
from uuid import uuid4
root=Path(sys.argv[1]);pid=sys.argv[2]
fake=FakeModel()
def query(prompt,connection):
    if len(fake.calls)==1:
        (root/'inflight').write_text('second request')
        time.sleep(30)
    return fake(prompt,connection)
engine=WorkflowEngine(str(root/'legacy'),str(root/'workflow.sqlite3'))
service=NovelAdaptation(engine,IPLibrary(root/'ip'),query=query,runtime=lambda:RUNTIME)
async def work():
    service.submit(pid,request_for(service,pid),str(uuid4()))
    await asyncio.gather(*list(service.workers))
asyncio.run(work())
'''
    process=subprocess.Popen([sys.executable,'-c',code,str(tmp_path),pid],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+8
        while not (tmp_path/'inflight').exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.03)
        assert (tmp_path/'inflight').exists()
        process.kill();process.wait(timeout=5)
        engine=WorkflowEngine(str(tmp_path/'legacy'),str(tmp_path/'workflow.sqlite3'));fake=FakeModel()
        resumed=NovelAdaptation(engine,IPLibrary(tmp_path/'ip'),query=fake,runtime=lambda:RUNTIME)
        detail=resumed.detail(pid)
        assert [c['status'] for c in detail['variant']['chunks']]==['completed','interrupted']
        assert detail['jobs'][0]['status']=='interrupted'
        async def finish():
            job=await run(resumed,pid)
            assert job['status']=='completed'
        asyncio.run(finish());assert len(fake.calls)==2
        engine.store.close()
    finally:
        if process.poll() is None:process.kill();process.wait()

"""Regressions for real outline count/bounds failures and bounded downstream repair."""
import asyncio
import copy
import json
from uuid import uuid4

import pytest
from core.ip_library import LibraryError
from core.novel_adaptation import NovelAdaptation
from core.novel_contracts import Action
from core.novel_contracts import Outline, validate_outline
from core.novel_planning import OutlineDraft, event_catalog, expand_outline
from core.novel_output import validate_response
from core.output_validation import ModelOutputError
from tests.test_novel_adaptation import make_service, run, confirm, request_for, PERSON


BIBLE = {'summary':'灯塔故事', 'characters':[PERSON], 'relationships':[], 'open_threads':[]}
EVENTS = [{'id':f'ch-{i}-p1-e1', 'chapter_title':f'第{i}章', 'description':f'事件{i}', 'quote':'原文证据', 'start':i} for i in range(1,230)]
DRAFT = {'episodes':[{'number':1,'title':'来信','summary':'收到来信后推开灯塔门。',
    'complete_event':'收到来信并进门','hook':'信是谁寄来的','characters':['林舟'],
    'event_ids':['E0001','E0002'], 'creative_additions':''}],
    'omitted_event_reason':'本轮 60 秒聚焦来信与开门，后续调查事件暂不纳入。',
    'omissions':[{'event_id':'E0003','reason':'重复交代来信动机，合并到开场。'}]}


def parse(draft):
    return validate_response(json.dumps(draft,ensure_ascii=False),{},OutlineDraft,
        lambda value:expand_outline(value,BIBLE,EVENTS,1))


def test_large_source_catalog_preserves_every_event_and_model_omission_reason():
    original = copy.deepcopy(DRAFT)
    value = parse(DRAFT)
    assert value['episodes'][0]['event_ids'] == [e['id'] for e in EVENTS[:2]]
    assert len(value['omissions']) == 227
    assert value['omissions'][0]['reason'] == DRAFT['omissions'][0]['reason']
    assert all(o['reason']==DRAFT['omitted_event_reason'] for o in value['omissions'][1:])
    assert {e['id'] for e in EVENTS} == set(value['episodes'][0]['event_ids']) | {o['event_id'] for o in value['omissions']}
    assert event_catalog(EVENTS)[0] == {'id':'E0001','chapter':'第1章','description':'事件1'}
    assert DRAFT == original


@pytest.mark.parametrize('corrupt',[
    lambda d:{**d,'episodes':[d['episodes'][0],{**d['episodes'][0],'number':2}]},
    lambda d:{**d,'episodes':[{**d['episodes'][0],'number':2}]},
    lambda d:{**d,'episodes':[{**d['episodes'][0],'event_ids':['E9999']}]},
    lambda d:{**d,'episodes':[{**d['episodes'][0],'event_ids':['E0001','E0001']}]},
    lambda d:{**d,'episodes':[{**d['episodes'][0],'characters':['未知人']}]},
    lambda d:{**d,'episodes':[{**d['episodes'][0],'event_ids':['E0001']*101}]},
    lambda d:{**d,'episodes':[{**d['episodes'][0],'event_ids':[f'E{i:04d}' for i in range(1,76)]}]},
    lambda d:{**d,'episodes':[{**d['episodes'][0],'characters':['林舟']*5}]},
    lambda d:{**d,'omissions':[{'event_id':'E9999','reason':'未知事件'}]},
    lambda d:{**d,'omissions':d['omissions']*2},
    lambda d:{**d,'omissions':[{'event_id':'E0001','reason':'与已用冲突'}]},
    lambda d:{**d,'omitted_event_reason':''},
    lambda d:{k:v for k,v in d.items() if k!='omitted_event_reason'},
])
def test_compact_contract_does_not_hide_invalid_output(corrupt):
    with pytest.raises(ModelOutputError):parse(corrupt(copy.deepcopy(DRAFT)))


def test_older_outline_without_hook_fields_can_still_be_reconfirmed():
    events = EVENTS[:2]
    episode = {'number':1,'title':'来信','summary':'收到来信后推开灯塔门。','characters':['林舟'],
               'event_ids':[events[0]['id']],'creative_additions':''}
    value = {'episodes':[episode],'omissions':[{'event_id':events[1]['id'],'reason':'下一轮再拍'}]}
    assert validate_outline(value, BIBLE, events, 1)['episodes'][0]['title']=='来信'
    stored = Outline.model_validate(value).model_dump(exclude_unset=True)
    assert 'complete_event' not in stored['episodes'][0] and 'hook' not in stored['episodes'][0]


def test_new_outline_needs_one_finished_event_an_opening_hook_and_no_repeated_source():
    events = EVENTS[:2]
    first = {**DRAFT['episodes'][0], 'event_ids':['E0001']}
    second = {**first, 'number':2, 'title':'进门', 'complete_event':'推开塔门', 'hook':'', 'event_ids':['E0001']}
    reused = {'episodes':[first, second], 'omitted_event_reason':'没有其他事件。', 'omissions':[]}
    with pytest.raises(ModelOutputError) as caught:
        expand_outline(OutlineDraft.model_validate(reused).model_dump(), BIBLE, events, 2)
    assert caught.value.diagnostic=='event_reused_across_episodes'
    repeated = {'episodes':[{**first,'complete_event':'同一件事'}, {**second,'event_ids':['E0002'],'complete_event':'同一件事'}],
                'omitted_event_reason':'没有其他事件。','omissions':[]}
    with pytest.raises(ModelOutputError) as caught:
        expand_outline(OutlineDraft.model_validate(repeated).model_dump(), BIBLE, events, 2)
    assert caught.value.diagnostic=='repeated_episode_event'
    blank = copy.deepcopy(DRAFT)
    blank['episodes'][0]['hook'] = ''
    with pytest.raises(ModelOutputError) as caught:
        parse(blank)
    assert caught.value.diagnostic=='missing_opening_hook'


@pytest.fixture
def service_fixture(tmp_path):
    values = make_service(tmp_path)
    yield values
    values[0].engine.store.close()


async def ready(service,pid,kind):
    await run(service,pid)
    if kind in {'outline','script'}:
        await run(service,pid,'direction');confirm(service,pid,'direction')
    if kind == 'script':
        await run(service,pid,'outline');confirm(service,pid,'outline')


@pytest.mark.parametrize('kind',['direction','outline','script'])
async def test_downstream_one_admission_repairs_invalid_output_and_keeps_prior_work(service_fixture,kind):
    service,pid,fake,_=service_fixture
    await ready(service,pid,kind)
    before=copy.deepcopy(service.detail(pid)['variant'])
    first=len(fake.calls)+1
    def corrupt_once(value):
        if len(fake.calls)!=first:return value
        if kind=='direction':return {**value,'focus_characters':['未知人']}
        if kind=='outline':return {**value,'episodes':value['episodes'][:1]}
        return {**value,'scenes':[{**value['scenes'][0],'duration':1}]}
    fake.invalid=corrupt_once
    request=request_for(service,pid,kind,episode=1 if kind=='script' else None)
    key=str(uuid4());admission=service.submit(pid,request,key)
    await asyncio.gather(*list(service.workers))
    job=service.store.job(admission['id'])
    assert job['status']=='completed' and [c['attempt'] for c in job['calls']]==[1,2]
    assert job['plan']['max_requests']==3 and job['plan']['max_output_tokens']==36000
    assert 'output_correction' in fake.calls[-1]
    assert '60' in fake.calls[-1]['output_correction']
    after=service.detail(pid)['variant']
    assert after['chunks']==before['chunks'] and after['bible']==before['bible']
    if kind!='direction':assert after['direction']==before['direction'] and after['direction_confirmed']
    if kind=='script':assert after['outline']==before['outline'] and after['outline_confirmed']
    if kind=='outline':assert not after['outline_confirmed']
    assert service.submit(pid,request,key)['id']==job['id'] and not service.workers
    assert not service.engine.project_detail(pid)['episodes']


async def test_outline_exhausts_bounded_repairs_without_saving_or_reanalysing(service_fixture):
    service,pid,fake,_=service_fixture
    await ready(service,pid,'outline')
    before=copy.deepcopy(service.detail(pid)['variant'])
    fake.invalid=lambda v:{**v,'episodes':v['episodes'][:1]}
    job=await run(service,pid,'outline')
    assert job['status']=='error' and len(job['calls'])==3
    assert all(c['kind']=='outline' for c in job['calls'])
    assert '集数或编号' in job['error']['message']
    assert service.detail(pid)['variant']==before


async def test_new_planning_policy_invalidates_admission_not_paid_analysis(service_fixture,monkeypatch,tmp_path):
    import core.novel_adaptation as module
    service,pid,fake,_=service_fixture
    await ready(service,pid,'outline')
    before=service.detail(pid)
    request=request_for(service,pid,'outline')
    policy=tmp_path/'planning.txt';policy.write_text(module.PLANNING_PROMPT.read_text()+'\n新的执行规则')
    monkeypatch.setattr(module,'PLANNING_PROMPT',policy)
    with pytest.raises(LibraryError,match='改变'):service.submit(pid,request,str(uuid4()))
    after=service.detail(pid)
    assert not after['stale'] and after['variant']==before['variant']
    assert (await run(service,pid,'outline'))['status']=='completed'
    data=fake.calls[-1]
    assert all(set(e)=={'id','chapter','description'} for e in data['events'])
    assert len(data['events'])==len(after['events'])


def test_outline_prompt_expresses_exact_count_and_has_all_short_ids():
    data={'episode_count':1,'target_duration':60,'events':event_catalog(EVENTS)}
    prompt=NovelAdaptation._prompt('outline',data,{'planning_policy':'执行规则'},100000)
    schema=json.loads(prompt.split('\nSCHEMA\n')[1].split('\nDATA\n')[0])
    assert schema['properties']['episodes']['minItems']==schema['properties']['episodes']['maxItems']==1
    assert len(schema['$defs']['EpisodeOutline']['properties']['event_ids']['items']['enum'])==229
    assert schema['$defs']['EpisodeOutline']['properties']['event_ids']['maxItems']==8
    assert schema['$defs']['EpisodeOutline']['properties']['characters']['maxItems']==4
    assert schema['$defs']['EpisodeOutline']['properties']['complete_event']['minLength']==2
    assert 'complete_event' in schema['$defs']['EpisodeOutline']['required']
    assert 'hook' in schema['$defs']['EpisodeOutline']['required']


async def test_lower_repair_budget_is_enforced_and_changes_admission(service_fixture,monkeypatch):
    service,pid,fake,_=service_fixture
    await ready(service,pid,'outline')
    request=request_for(service,pid,'outline')
    monkeypatch.setenv('YC_NOVEL_REPAIR_ATTEMPTS','1')
    with pytest.raises(LibraryError,match='改变'):service.submit(pid,request,str(uuid4()))
    fake.invalid=lambda v:{**v,'episodes':v['episodes'][:1]}
    job=await run(service,pid,'outline')
    assert job['status']=='error' and job['plan']['max_requests']==2 and len(job['calls'])==2


async def test_supplementary_planning_metadata_is_ignored_without_hiding_required_errors(service_fixture):
    service,pid,fake,_=service_fixture
    await ready(service,pid,'outline')
    fake.invalid=lambda v:{**v,'target_duration':60,'episodes':[{**e,'notes':'补充说明'} for e in v['episodes']]}
    job=await run(service,pid,'outline')
    assert job['status']=='completed' and len(job['calls'])==1
    assert 'notes' not in service.detail(pid)['variant']['outline']['episodes'][0]
    fake.invalid=lambda v:{**v,'target_duration':60,'episodes':[{k:x for k,x in e.items() if k!='summary'} for e in v['episodes']]}
    job=await run(service,pid,'outline')
    assert job['status']=='error' and len(job['calls'])==3
    assert all(c['diagnostic']=='schema:missing' for c in job['calls'])

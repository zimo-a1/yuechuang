"""Regression cases for errors observed in real chapter and merge jobs."""
import copy
import json
import asyncio
import threading
from uuid import uuid4
import pytest

from core.novel_contracts import ChunkAnalysis, validate_people, Action
from core.novel_recovery import source_registry, assemble_summary, extraction_fields, known_fields, StorySummary
from core.novel_output import validate_response
from core.output_validation import ModelOutputError
from core.novel_adaptation import NovelAdaptation
from tests.test_novel_adaptation import make_service, run, request_for, PERSON


def person(name, aliases=()):
    return {'name':name,'aliases':list(aliases),'description':f'{name}的原文资料'}


def chapter(people, relations=()):
    return {'summary':'章节摘要','characters':people,'relationships':list(relations),'events':[]}


def test_registry_preserves_minor_names_and_resolves_explicit_alias_identity():
    batch=[chapter([person('林舟',['小林']),person('高总'),person('路人')],
                   [{'source':'林舟','target':'高总','description':'交谈'}]),
           chapter([person('高森林',['高总']),person('林舟',['小林','阿舟'])])]
    original=copy.deepcopy(batch)
    registry,notes=source_registry(None,batch)
    summary=validate_response(json.dumps({'summary':'全局摘要','open_threads':[],
        'characters':[person('凭空创造的人')],'events':['非必要字段']}),{},StorySummary,
        lambda value:assemble_summary(value,registry,notes),lambda value:known_fields(value,StorySummary))
    assert {p['name'] for p in summary['characters']}=={'林舟','高森林','路人'}
    assert {n for p in summary['characters'] for n in [p['name'],*p['aliases']]}=={'林舟','小林','阿舟','高森林','高总','路人'}
    assert summary['relationships'][0]['target']=='高森林' and batch==original
    # Next merge also retains earlier minority characters without asking the model.
    again,_=source_registry(summary,[chapter([person('新同事')])])
    assert {p['name'] for p in again['characters']}=={'林舟','高森林','路人','新同事'}


def test_shared_generic_alias_does_not_collapse_different_people():
    registry,notes=source_registry(None,[chapter([person('苏桐',['师姐'])]),chapter([person('李娜',['师姐'])])])
    assert {p['name'] for p in registry['characters']}=={'苏桐','李娜'}
    assert len(notes)==1 and '暂不合并' in notes[0]
    assert notes==assemble_summary({'summary':'摘要','open_threads':[]},registry,notes)['open_threads']


def test_duplicate_own_alias_and_unique_relationship_alias_are_equivalent():
    result=chapter([person('林舟',['林舟','小林','小林']),person('同事')],
        [{'source':'小林','target':'同事','description':'交谈'}])
    validate_people(result)
    assert result['characters'][0]['aliases']==['小林']
    assert result['relationships'][0]['source']=='林舟'
    result['relationships'][0]['source']='未登记的人'
    with pytest.raises(ModelOutputError):validate_people(result)


@pytest.mark.parametrize('quote,source,expected',[
    ('他说:"你好!"','他说：“你好！”','他说：“你好！”'),
    ('林舟推开门。','林舟\n  推开门。','林舟\n  推开门。'),
    ('林舟推开窗。','林舟推开门。','林舟推开窗。'),
    ('林舟离开。收到信。','林舟离开。很久以后收到信。','林舟离开。收到信。'),
    ('甲乙','甲 乙。甲\n乙','甲乙'),
])
def test_quotes_only_normalize_unique_contiguous_source_span(quote,source,expected):
    value={'summary':'摘要','characters':[],'relationships':[],
           'events':[{'description':'事件','quote':quote,'extra':'非必要资料'}],'extra':'非必要资料'}
    normalized=extraction_fields(value,ChunkAnalysis,source)
    assert normalized['events'][0]['quote']==expected
    assert 'extra' not in normalized and 'extra' not in normalized['events'][0]


def test_long_quote_becomes_bounded_original_excerpt_but_required_fields_stay_required():
    quote='甲'*190+'乙'
    value={'summary':'摘要','characters':[],'relationships':[],'events':[{'description':'事件','quote':quote}]}
    normalized=extraction_fields(value,ChunkAnalysis,quote)
    assert normalized['events'][0]['quote']==quote[:180]
    with pytest.raises(ModelOutputError):
        validate_response('{"summary":"缺必填字段","extra":1}',{},ChunkAnalysis,lambda v:v,
                          lambda v:extraction_fields(v,ChunkAnalysis,'原文'))


async def test_one_start_repairs_then_continues_and_merges_without_losing_names(tmp_path):
    service,pid,fake,_=make_service(tmp_path)
    try:
        def invalid_once(value):
            if len(fake.calls)==1:return {**value,'events':[{'description':'事件','quote':'捏造引文'}]}
            if 'open_threads' in value:return {'summary':'故事梗概','open_threads':[],'extra':'不再因补充字段失败'}
            return value
        fake.invalid=invalid_once
        plan=service.plan(pid,Action(action='analysis').model_dump())
        assert plan['base_requests']==3 and plan['max_requests']==9 and plan['retries']==2
        job=await run(service,pid)
        assert job['status']=='completed' and len(job['calls'])==4
        assert job['calls'][0]['status']=='error' and job['calls'][1]['attempt']==2
        assert 'output_correction' in fake.calls[1]
        assert all(c['status']=='completed' for c in service.detail(pid)['variant']['chunks'])
        assert service.detail(pid)['variant']['bible']['characters']==[PERSON]
        restored=NovelAdaptation(service.engine,service.library,query=fake,runtime=service.runtime)
        assert restored.detail(pid)==service.detail(pid)
    finally:service.engine.store.close()


async def test_exhausted_chapter_does_not_block_remaining_chapters_or_pretend_success(tmp_path):
    service,pid,fake,_=make_service(tmp_path)
    try:
        fake.invalid=lambda v:{**v,'events':[{'description':'事件','quote':'捏造引文'}]} if len(fake.calls)<=3 else v
        result=await run(service,pid)
        assert result['status']=='error' and len(fake.calls)==4
        detail=service.detail(pid)
        assert [c['status'] for c in detail['variant']['chunks']]==['error','completed']
        assert detail['variant']['bible'] is None
        assert service.plan(pid,Action(action='analysis').model_dump())['pending_chunks']==1
    finally:service.engine.store.close()


async def test_stop_after_invalid_response_prevents_automatic_repair(tmp_path):
    service,pid,fake,_=make_service(tmp_path)
    try:
        started,release=threading.Event(),threading.Event()
        fake.wait=(started,release)
        fake.invalid=lambda v:{**v,'events':[{'description':'事件','quote':'捏造引文'}]}
        job=service.submit(pid,request_for(service,pid),str(uuid4()))
        assert await asyncio.to_thread(started.wait,2)
        service.stop(pid,job['id']);release.set()
        await asyncio.gather(*list(service.workers))
        assert service.store.job(job['id'])['status']=='stopped' and len(fake.calls)==1
    finally:service.engine.store.close()

"""Source labels and speaker manifests must not depend on model self-checks."""
import copy
import json

import pytest
from core.novel_contracts import validate_episode
from core.novel_script import WrittenEpisode, finalize_script
from core.novel_recovery import known_fields
from core.novel_output import validate_response
from core.novel_adaptation import NovelAdaptation
from core.output_validation import ModelOutputError
from tests.test_novel_adaptation import make_service, run, confirm


TEXT = '林舟说：“灯亮了。”\n小禾点点头。\n第二章\n明天再来。'
END = TEXT.index('第二章')
EVENTS = [{'id':'first','chunk_start':0,'chunk_end':END},
          {'id':'second','chunk_start':END,'chunk_end':len(TEXT)}]
BIBLE = {'characters':[{'name':'林舟','aliases':['小林'],'description':'修灯青年'},
                       {'name':'小禾','aliases':['阿禾'],'description':'好友'}]}
OUTLINE = {'characters':['林舟','小禾'],'event_ids':['first']}
DRAFT = {'title':'灯塔','scenes':[{'location':'灯塔','characters':['小禾'], 'duration':60,
    'action':'林舟点灯，小禾看向他。','event_ids':['first'],'creative_addition':True,
    'dialogue':[{'speaker':'小林','text':'灯亮了。','creative_addition':False},
                {'speaker':'阿禾','text':'明天再来。','creative_addition':False},
                {'speaker':'林舟','text':'我们一起修好它。','creative_addition':False}]}]}


def parse(draft, bible=BIBLE):
    return validate_response(json.dumps(draft,ensure_ascii=False),{},WrittenEpisode,
        lambda v:finalize_script(v,OUTLINE,60,EVENTS,TEXT,bible),lambda v:known_fields(v,WrittenEpisode))


def test_quotes_are_derived_only_from_referenced_source_and_cast_keeps_every_line():
    before=copy.deepcopy(DRAFT);result=parse(DRAFT);scene=result['scenes'][0]
    assert scene['characters']==['小禾','林舟']
    assert [d['speaker'] for d in scene['dialogue']]==['林舟','小禾','林舟']
    assert [d['text'] for d in scene['dialogue']]==[d['text'] for d in DRAFT['scenes'][0]['dialogue']]
    assert [d['creative_addition'] for d in scene['dialogue']]==[False,True,True]
    assert DRAFT==before
    assert validate_episode(result,OUTLINE,60,EVENTS,TEXT)==result


@pytest.mark.parametrize('speaker',['旁白','陌生人','林舟（心声）'])
def test_unknown_or_ambiguous_speaker_is_not_assigned_to_someone(speaker):
    draft=copy.deepcopy(DRAFT);draft['scenes'][0]['dialogue'][0]['speaker']=speaker
    with pytest.raises(ModelOutputError) as caught:parse(draft)
    assert caught.value.diagnostic=='unknown_speaker'
    assert '已确认大纲' in caught.value.message


def test_shared_alias_or_another_canonical_name_does_not_change_identity():
    for person in [{'name':'外人','aliases':['小林'],'description':'同名昵称'},
                   {'name':'小林','aliases':[],'description':'独立人物'}]:
        bible={'characters':[*BIBLE['characters'],person]}
        with pytest.raises(ModelOutputError):parse(DRAFT,bible)


@pytest.mark.parametrize('patch,diagnostic',[
    ({'duration':59},'duration_sum_mismatch'),
    ({'event_ids':['second']},'unknown_scene_event'),
    ({'event_ids':[],'creative_addition':True},'missing_planned_events'),
    ({'event_ids':[],'creative_addition':False},'unattributed_scene'),
    ({'characters':['未知人']},'unknown_scene_character'),
])
def test_metadata_reconciliation_does_not_relax_other_contracts(patch,diagnostic):
    draft=copy.deepcopy(DRAFT);draft['scenes'][0].update(patch)
    with pytest.raises(ModelOutputError) as caught:parse(draft)
    assert caught.value.diagnostic==diagnostic


def test_unverified_original_quote_still_rejected_by_stored_contract():
    value=parse(DRAFT);value['scenes'][0]['dialogue'][-1]['creative_addition']=False
    with pytest.raises(ModelOutputError) as caught:validate_episode(value,OUTLINE,60,EVENTS,TEXT)
    assert caught.value.diagnostic=='unmarked_new_dialogue'


def test_script_schema_lists_cast_and_event_ids_and_does_not_ask_model_to_label_quotes():
    data={'selected_episode':OUTLINE,'target_duration':60}
    prompt=NovelAdaptation._prompt('script',data,{'planning_policy':'本集写作规则'},10000)
    spec=json.loads(prompt.split('\nSCHEMA\n')[1].split('\nDATA\n')[0])
    assert spec['$defs']['WrittenDialogue']['properties']['speaker']['enum']==OUTLINE['characters']
    assert 'creative_addition' not in spec['$defs']['WrittenDialogue']['properties']
    assert spec['$defs']['WrittenScene']['properties']['event_ids']['items']['enum']==['first']


async def test_real_worker_reconciles_once_persists_and_publishes_adapted_labels(tmp_path):
    service,pid,fake,_=make_service(tmp_path)
    try:
        await run(service,pid);await run(service,pid,'direction');confirm(service,pid,'direction')
        await run(service,pid,'outline');confirm(service,pid,'outline')
        before=copy.deepcopy(service.detail(pid)['variant'])
        fake.invalid=lambda v:{**v,'scenes':[{**v['scenes'][0],'dialogue':[{'speaker':'小林','text':'新编的台词。','creative_addition':False}]}]}
        job=await run(service,pid,'script',episode=1)
        assert job['status']=='completed' and len(job['calls'])==1
        d=service.detail(pid);v=d['variant']
        assert all(v[k]==before[k] for k in ('chunks','bible','direction','direction_confirmed','outline','outline_confirmed'))
        assert v['scripts']['1']['value']['scenes'][0]['dialogue']==[{'speaker':'林舟','text':'新编的台词。','creative_addition':True}]
        assert fake.calls[-1]['allowed_speakers']==['林舟']
        assert [p['name'] for p in fake.calls[-1]['bible']['characters']]==['林舟']
        restored=NovelAdaptation(service.engine,service.library,query=fake,runtime=service.runtime)
        assert restored.detail(pid)==d
        # Publishing is exercised only in the isolated synthetic database.
        saved=restored.publish(pid,1,d['revision'])
        artifact=service.engine.get_state(saved['session_id']).artifacts['script_generation']
        assert '【改编对白】' in json.dumps(artifact,ensure_ascii=False)
        assert not service.engine.get_state(saved['session_id']).confirmed_stages
    finally:service.engine.store.close()

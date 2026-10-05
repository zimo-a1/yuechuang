"""Interaction contracts: one-shot edits, project library and explicit reference use."""
from copy import deepcopy
from uuid import uuid4
import pytest
from core.agents.reference_agent import ReferenceGeneratorAgent
from core.agents.video_agent import VideoDirectorAgent
from tests.test_projects import api, draft
from tests.test_project_studio import episode
from tests.test_output_validation import SCRIPT


def seed(engine, sid):
    state = engine.get_state(sid)
    state.artifacts = {
        'script_generation': deepcopy(SCRIPT),
        'character_design': {'characters': [{'id': 'c1', 'name': '修表匠', 'description': '旧工作服', 'selected': 'code/result/person.png', 'versions': ['code/result/person.png']}],
                             'settings': [], 'props': [{'id': 'p1', 'name': '怀表', 'description': '黄铜怀表', 'selected': 'code/result/watch.png', 'versions': ['code/result/watch.png']}]},
        'storyboard': {'episodes': [{'episode_number': 1, 'segments': [
            {'segment_id': 'seg_01', 'segment_number': 1, 'location': '修表店', 'characters': ['修表匠'], 'total_duration': 4,
             'shots': [{'shot_number': 1, 'shot_type': '中景', 'content': '修表匠举起怀表', 'duration': 4}]}]}]},
        'video_generation': {'clips': [{'id': 'seg_01', 'name': '镜头1', 'description': '旧提示词', 'selected': 'old.mp4', 'versions': ['old.mp4']}]},
        'reference_generation': {'scenes': [{'id': 'seg_01', 'selected': 'old.png', 'versions': ['old.png']}]},
    }
    state.confirmed_stages = ['script_generation', 'character_design', 'storyboard', 'reference_generation', 'video_generation']
    engine.save_session_to_disk(sid)
    return deepcopy(state.artifacts['storyboard']['episodes'][0]['segments'][0])


async def test_segment_atomic_save_keeps_versions_and_detects_stale_tab(api):
    client, engine, _ = api
    sid = (await draft(client))['session_id']; original = seed(engine, sid)
    modified = {**original, 'reference_asset_ids': ['p1'], 'video_prompt': '缓慢举起怀表，凝视表盘', 'visual_prompt': '手持怀表特写'}
    response = await client.patch(f'/api/projects/{sid}/segments/seg_01', json={'expected_segment': original, 'segment': modified})
    assert response.status_code == 200, response.text
    state = engine.get_state(sid)
    assert state.confirmed_stages == ['script_generation', 'character_design']
    assert state.artifacts['video_generation']['clips'][0]['description'] == modified['video_prompt']
    assert state.artifacts['video_generation']['clips'][0]['versions'] == ['old.mp4']
    assert state.artifacts['reference_generation']['scenes'][0]['visual_prompt'] == modified['visual_prompt']
    before = deepcopy(state.artifacts)
    conflict = await client.patch(f'/api/projects/{sid}/segments/seg_01', json={'expected_segment': original, 'segment': original})
    assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'SEGMENT_CHANGED'
    assert state.artifacts == before
    engine.sessions.pop(sid)
    engine._load_sessions_from_disk()
    assert engine.get_state(sid).artifacts == before


@pytest.mark.parametrize('patch', [{'reference_asset_ids':['missing']}, {'reference_asset_ids':['p1','p1']}, {'total_duration': 5}, {'shots':[]}, {'total_duration':True}, {'video_prompt':42}])
async def test_segment_invalid_edit_is_not_written(api, patch):
    client, engine, _ = api
    sid = (await draft(client))['session_id']; original = seed(engine, sid)
    before = deepcopy(engine.get_state(sid).__dict__)
    response = await client.patch(f'/api/projects/{sid}/segments/seg_01', json={'expected_segment':original,'segment':{**original,**patch}})
    assert response.status_code == 409, response.text
    assert engine.get_state(sid).__dict__ == before


async def test_import_only_same_project_and_repeat_is_idempotent(api):
    client, engine, _ = api
    parent = (await draft(client, project_container=True))['session_id']
    source = await episode(client,parent); target = await episode(client,parent)
    seed(engine,source); seed(engine,target)
    request={'source_session_id':source,'kind':'props','asset_id':'p1'}
    response=await client.post(f'/api/projects/{target}/assets/import',json=request)
    assert response.status_code==200,response.text
    props=response.json()['artifact']['props'];assert len(props)==2
    assert props[-1]['selected']=='code/result/watch.png'
    assert engine.get_state(target).confirmed_stages==['script_generation']
    assert (await client.post(f'/api/projects/{target}/assets/import',json=request)).json()['artifact']['props']==props
    assert len(engine.get_state(source).artifacts['character_design']['props'])==1
    other=(await draft(client))['session_id'];seed(engine,other)
    request['source_session_id']=other
    assert (await client.post(f'/api/projects/{target}/assets/import',json=request)).status_code==409


def test_explicit_bindings_and_prompt_used_by_both_agents(tmp_path):
    person=tmp_path/'person.png';person.write_bytes(b'image')
    prop=tmp_path/'prop.png';prop.write_bytes(b'image')
    art={'characters':[{'id':'c1','name':'修表匠','selected':str(person)}], 'settings':[], 'props':[{'id':'p1','name':'怀表','selected':str(prop)}]}
    segment={'segment_id':'s','characters':['修表匠'],'shots':[], 'reference_asset_ids':['p1'], 'video_prompt':'指定动作'}
    reference=ReferenceGeneratorAgent();video=VideoDirectorAgent()
    asset_map=reference._build_asset_map(art)
    assert reference._collect_refs(segment,asset_map,{'修表匠':'c1'}, {})==[str(prop)]
    assert video._get_segment_reference_assets(segment,art)==[str(prop)]
    assert '指定动作' in video._assemble_prompt(segment,'写实',art,{'description':'过时提示词'})
    segment['reference_asset_ids']=[]
    assert reference._collect_refs(segment,asset_map,{}, {})==[]
    assert video._get_segment_reference_assets(segment,art)==[]


async def test_segment_storage_failure_rolls_back_every_artifact(api, monkeypatch):
    client, engine, _ = api
    sid = (await draft(client))['session_id']; original = seed(engine, sid)
    before = deepcopy(engine.get_state(sid).__dict__)
    def fail(_):
        raise OSError('simulated disk failure')
    monkeypatch.setattr(engine, 'save_session_to_disk', fail)
    response = await client.patch(f'/api/projects/{sid}/segments/seg_01', json={
        'expected_segment': original, 'segment': {**original, 'video_prompt': 'new prompt'}})
    assert response.status_code == 500
    assert response.json()['error']['code'] == 'SAVE_FAILED'
    assert engine.get_state(sid).__dict__ == before

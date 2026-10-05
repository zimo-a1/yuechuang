"""Project/episode and five-step studio contract; no provider calls."""
from copy import deepcopy
from uuid import uuid4
import pytest
from core.orchestrator import WorkflowEngine, WorkflowStage
from core.workflow_rules import WorkflowConflict
from tests.test_projects import api, draft
from tests.test_output_validation import SCRIPT

async def episode(client, parent, **values):
    response=await client.post(f'/api/projects/{parent}/episodes', json=values, headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code==201, response.text
    return response.json()['session_id']

async def test_project_episode_isolation_defaults_idempotence_and_legacy(api):
    client,engine,_=api
    parent=(await draft(client,title='容器',project_container=True,video_ratio='16:9'))['session_id']
    assert (await client.get(f'/api/projects/{parent}')).json()['episodes']==[]
    headers={'Idempotency-Key':str(uuid4())}
    response=await client.post(f'/api/projects/{parent}/episodes',json={},headers=headers)
    first=response.json()['session_id']
    assert response.status_code==201
    assert (await client.post(f'/api/projects/{parent}/episodes',json={},headers=headers)).json()['session_id']==first
    assert (await client.post(f'/api/projects/{parent}/episodes',json={'title':'改请求'},headers=headers)).status_code==409
    second=await episode(client,parent,title='第二集')
    items=(await client.get(f'/api/projects/{parent}')).json()['episodes']
    assert [e['meta']['episode_number'] for e in items]==[1,2]
    assert engine.get_state(first).meta['video_ratio']=='16:9'
    assert engine.get_state(first).meta['llm_model']
    assert (await client.patch(f'/api/projects/{first}/source',json={'content':'独立原文','expected_revision':1})).status_code==200
    assert engine.get_state(second).meta['idea']==''
    assert engine.get_state(parent).meta['idea']==''
    assert (await client.get(f'/api/projects/{first}')).status_code==404
    blocked=await client.post(f'/api/project/{parent}/execute/script_generation',json={})
    assert blocked.status_code==409 and blocked.json()['error']['code']=='EPISODE_REQUIRED'
    old=(await draft(client,title='原有单项目',idea='旧内容'))['session_id']
    assert [e['id'] for e in (await client.get(f'/api/projects/{old}')).json()['episodes']]==[old]

async def test_skip_edit_source_invalidates_confirmation_without_destroying_artifacts(api):
    client,engine,_=api
    sid=(await draft(client,idea='日 内 修表店\n修表匠打开怀表。'))['session_id']
    response=await client.patch(f'/api/projects/{sid}/script-content',json={'content':'client ignored','expected_revision':1,'use_original':True})
    assert response.status_code==200
    assert response.json()['artifact']['episodes'][0]['content']==engine.get_state(sid).meta['idea']
    await engine.continue_workflow(sid,'script_generation')
    before=deepcopy(engine.get_state(sid).artifacts)
    assert (await client.patch(f'/api/projects/{sid}/source',json={'content':'改变原文','expected_revision':1})).status_code==409
    assert (await client.patch(f'/api/projects/{sid}/source',json={'content':'改变原文','expected_revision':2})).status_code==200
    state=engine.get_state(sid)
    assert state.artifacts==before and not state.confirmed_stages
    with pytest.raises(WorkflowConflict,match='原始内容已变更'):
        await engine.continue_workflow(sid,'script_generation')
    assert (await client.patch(f'/api/projects/{sid}/script-content',json={'content':'重新确认后的剧本','expected_revision':3})).status_code==200
    await engine.continue_workflow(sid,'script_generation')

async def test_multi_episode_legacy_cannot_be_overwritten_by_plain_editor(api):
    client,engine,_=api
    sid=(await draft(client))['session_id']
    script=deepcopy(SCRIPT);script['episodes']=[{'episode_number':1,'content':'one'},{'episode_number':2,'content':'two'}]
    engine.update_artifact(sid,'script_generation',script)
    revision=engine.get_state(sid).meta['project_revision']
    before=deepcopy(engine.get_state(sid).artifacts['script_generation'])
    response=await client.patch(f'/api/projects/{sid}/script-content',json={'content':'overwrite','expected_revision':revision})
    assert response.status_code==409 and engine.get_state(sid).artifacts['script_generation']==before

async def test_assets_prepare_crud_version_validation_and_aggregation(api):
    client,engine,_=api
    parent=(await draft(client,project_container=True))['session_id'];sid=await episode(client,parent)
    engine.update_artifact(sid,'script_generation',deepcopy(SCRIPT))
    assert (await client.post(f'/api/projects/{sid}/assets',json={})).status_code==409
    await engine.continue_workflow(sid,'script_generation')
    prepared=await client.post(f'/api/projects/{sid}/assets',json={})
    assert prepared.status_code==200
    assert prepared.json()['artifact']['characters']
    before=deepcopy(prepared.json()['artifact'])
    assert (await client.post(f'/api/projects/{sid}/assets',json={})).json()['artifact']==before
    created=await client.post(f'/api/projects/{sid}/assets',json={'kind':'props','name':'怀表','description':'黄铜怀表'})
    prop=created.json()['artifact']['props'][0]
    assert (await client.post(f'/api/projects/{sid}/assets',json={'kind':'props','id':prop['id'],'selected':'/invalid/path'})).status_code==409
    assert engine.get_state(sid).artifacts['character_design']['props'][0]['selected']==''
    assert (await client.post(f'/api/projects/{sid}/assets',json={'kind':'props','id':prop['id'],'description':'带划痕的黄铜怀表'})).status_code==200
    assert engine.get_state(sid).artifacts['script_generation']['props'][0]['description']=='带划痕的黄铜怀表'
    assert any(item['kind']=='props' and item['session_id']==sid for item in (await client.get(f'/api/projects/{parent}')).json()['assets'])
    assert (await client.post(f'/api/projects/{sid}/assets',json={'kind':'props','id':prop['id'],'delete':True})).status_code==200
    assert not engine.get_state(sid).artifacts['script_generation']['props']
    assert not (await client.post(f'/api/projects/{sid}/assets',json={})).json()['artifact']['props']

async def test_container_delete_preflights_busy_children_and_cascades(api):
    client,engine,_=api
    parent=(await draft(client,project_container=True))['session_id'];first=await episode(client,parent);second=await episode(client,parent)
    engine.store.save_job({'task_id':'busy','session_id':second,'stage':'script_generation','status':'running','created_at':1,'updated_at':1,'input':{},'intervention':None,'events':[],'sequence':0})
    assert engine.delete_session(parent) is False
    assert all(engine.get_state(sid) for sid in [parent,first,second])
    job=engine.store.get_job('busy');job['status']='stopped';engine.store.save_job(job)
    engine.delete_session(parent)
    assert all(not engine.get_state(sid) for sid in [parent,first,second])

async def test_episode_and_assets_persist_on_actual_engine_restart(tmp_path):
    db=tmp_path/'db.sqlite3';engine=WorkflowEngine(session_dir=str(tmp_path/'legacy'),database_path=db)
    parent,_=engine.create_draft({'title':'父项目','project_container':True},str(uuid4()),'root')
    child,_=engine.create_episode(parent['session_id'],{},str(uuid4()),'child');sid=child['session_id']
    engine.update_source(sid,'本地原文',1);engine.save_script_content(sid,'剧本',2)
    await engine.continue_workflow(sid,'script_generation')
    engine.manage_assets(sid,{'kind':'props','name':'道具','description':'蓝色怀表'})
    before=engine.get_status_snapshot(sid);engine.store.close()
    restored=WorkflowEngine(session_dir=str(tmp_path/'legacy'),database_path=db)
    try:
        after=restored.get_status_snapshot(sid)
        assert after['updated_at']==before['updated_at'].timestamp()
        for key in ('status','meta','artifacts','confirmed_stages'):
            assert after[key]==before[key]
        assert restored.project_detail(parent['session_id'])['assets'][0]['name']=='道具'
    finally:restored.store.close()

async def test_asset_extraction_preserves_body_and_identity(monkeypatch):
    from core.agents.script_agent import ScriptWriterAgent
    original=deepcopy(SCRIPT);original['props']=[{'prop_id':'old','name':'怀表','description':'旧描述'}]
    agent=ScriptWriterAgent()
    async def query(*args,**kwargs):
        assert kwargs['attempts']==1
        return [{'name':'怀表','description':'不可覆盖'},{'name':'雨伞','description':'黑色长柄伞'}]
    monkeypatch.setattr(agent,'_query_structured',query)
    result=await agent.process({'session_id':'offline','llm_model':'offline','_session_artifacts':{'script_generation':original}},{'action':'extract_assets','kind':'props'})
    assert result['payload']['episodes']==original['episodes']
    assert result['payload']['props'][0]==original['props'][0]
    assert len(result['payload']['props'])==2 and result['requires_intervention']

def test_prop_image_is_referenced_only_when_present_in_the_shot(tmp_path):
    from core.agents.reference_agent import ReferenceGeneratorAgent
    agent=ReferenceGeneratorAgent()
    data={'props':[{'name':'怀表','selected':str(tmp_path/'watch.png')},{'name':'雨伞','selected':str(tmp_path/'umbrella.png')}]}
    refs=agent._collect_refs({'shots':[{'content':'人物打开怀表'}]},agent._build_asset_map(data),{}, {})
    assert refs==[str(tmp_path/'watch.png')]

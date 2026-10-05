import pytest
from core.shot_control import compile_shot_control,validate_shot_control,ShotPlanError


def shot():
    return {'total_duration':15,'characters':['杨飞'],'shot_control':{'version':1,'beats':[
        {'unit_id':'a','kind':'action','start':0.0,'end':2.0,'text':'仓库空镜'},
        {'unit_id':'b','kind':'dialogue','start':3.0,'end':10.0,'text':'厂里的所有库存，我来承销！','speaker':'杨飞','listeners':[],'offscreen':True}]}}


def test_voiceover_does_not_instruct_an_actor_to_appear_or_make_eye_contact():
    s=shot();validate_shot_control(s);p=compile_shot_control(s)
    assert '画外音' in p and '说话者不出镜' in p
    assert '继续看着对话对象' not in p and '所有人闭嘴' not in p
    assert p.count('厂里的所有库存，我来承销！')==1


def test_voiceover_still_enforces_cast_and_speech_time():
    s=shot();s['characters']=[]
    with pytest.raises(ShotPlanError):validate_shot_control(s)
    s=shot();s['shot_control']['beats'][1]['end']=4.0
    with pytest.raises(ShotPlanError):validate_shot_control(s)


def test_action_cannot_claim_to_be_voiceover():
    s=shot();s['shot_control']['beats'][0]['offscreen']=True
    with pytest.raises(ShotPlanError):validate_shot_control(s)

"""Isolated synthetic browser acceptance server; cannot call a real provider."""
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
STATE=Path(os.environ.get('YC_PHASE9_QA_DIR','/tmp/yuechuang-stage9-preview'))
STATE.mkdir(parents=True,exist_ok=True)
os.environ.update(YC_STATE_DIR=str(STATE),YC_LEGACY_SESSION_DIR=str(STATE/'legacy'),YC_IP_DIR=str(STATE/'ip'),
                 YC_SETTINGS_DIR=str(STATE/'settings'),YC_CONFIG_PATH=str(STATE/'config.yaml'),YC_ENV_PATH=str(STATE/'.env'))
for provider in ('OPENAI','GEMINI','DEEPSEEK','DASHSCOPE','ARK','KLING','AGNES','TUDOU'):
    for suffix in ('API_KEY','ACCESS_KEY','SECRET_KEY'):os.environ[f'{provider}_{suffix}']=''
if not (STATE/'config.yaml').exists():(STATE/'config.yaml').write_text('api_providers: {}\nmodels: {}\n')
from api.app import app
from api.dependencies import workflow_engine,task_manager
from core.tasks import TaskConflict
def deny(*args,**kwargs):raise TaskConflict('隔离验收环境仅使用文本替身，禁止实际生成')
task_manager.submit=deny
from tests.test_novel_adaptation import make_service,FakeModel,RUNTIME
from core.novel_adaptation import NovelAdaptation
from core.ip_library import IPLibrary
import api.routers.novel as router
if not (STATE/'fixture.json').exists():
    import json
    fixture,pid,_,_=make_service(STATE,engine=workflow_engine)
    (STATE/'fixture.json').write_text(json.dumps({'project_id':pid}))
novel=NovelAdaptation(workflow_engine,IPLibrary(STATE/'ip'),query=FakeModel(),runtime=lambda:RUNTIME)
router.get_novel=lambda:novel
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8050)

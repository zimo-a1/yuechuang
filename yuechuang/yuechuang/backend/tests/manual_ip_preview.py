"""Isolated IP browser preview. Contains only local synthetic test text."""
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
STATE=Path(os.environ.get('YC_PHASE8_QA_DIR','/tmp/yuechuang-stage8-preview'))
STATE.mkdir(parents=True,exist_ok=True)
os.environ.update(YC_STATE_DIR=str(STATE),YC_LEGACY_SESSION_DIR=str(STATE/'legacy'),YC_IP_DIR=str(STATE/'ip_library'),YC_SETTINGS_DIR=str(STATE/'settings'),YC_CONFIG_PATH=str(STATE/'config.yaml'),YC_ENV_PATH=str(STATE/'.env'))
for provider in ('OPENAI','GEMINI','DEEPSEEK','DASHSCOPE','ARK','KLING','AGNES','TUDOU'):
    for suffix in ('API_KEY','ACCESS_KEY','SECRET_KEY'):os.environ[f'{provider}_{suffix}']=''
if not (STATE/'config.yaml').exists():(STATE/'config.yaml').write_text('api_providers: {}\nmodels: {}\n')
from api.app import app
from api.dependencies import workflow_engine
from core.tasks import TaskConflict
# This isolated preview cannot dispatch model calls, including through old UI.
def deny_generation(*args,**kwargs):raise TaskConflict('隔离验收环境不执行模型请求')
from api.dependencies import task_manager
task_manager.submit=deny_generation
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8040)

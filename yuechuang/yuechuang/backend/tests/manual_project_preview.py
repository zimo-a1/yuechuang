"""Isolated, explicitly offline phase 6 browser preview. Never use provider APIs."""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATE = Path(os.environ.get('YC_PHASE6_QA_DIR', '/tmp/yuechuang-phase6-preview'))
os.environ['YC_STATE_DIR'] = str(STATE)
os.environ['YC_LEGACY_SESSION_DIR'] = str(STATE / 'legacy')

from api.app import app
from api.dependencies import workflow_engine
from core.agents.base_agent import AgentInterface
from core.orchestrator import WorkflowStage
from core.agents.editor_agent import VideoEditorAgent

SCRIPT = {
    'title': '[离线测试] 逆行怀表', 'logline': '修表匠用一分钟帮助陌生人。',
    'characters': [{'name': '修表匠', 'description': '穿旧工作服的中年人', 'role': '主角', 'personality': ['谨慎']}],
    'settings': [{'name': '修表店', 'description': '午后安静的旧店铺'}],
    'episodes': [{'episode_number': 1, 'act_title': '一分钟', 'content': '【离线固定样例，不是模型生成】\n日 内 修表店\n修表匠打开怀表。\n修表匠：你还有一分钟。'}],
}


class OfflineScript(AgentInterface):
    async def process(self, input_data, intervention=None):
        for percent in (15, 45, 75):
            self._check_cancel()
            self._report_progress('离线测试', '固定样例演示，不调用模型', percent)
            await asyncio.sleep(1)
        return {'payload': SCRIPT, 'requires_intervention': True}


class DisabledAgent(AgentInterface):
    async def process(self, input_data, intervention=None):
        self._report_progress('离线任务验收', '模拟媒体失败，不调用供应商', 25)
        await asyncio.sleep(2)
        self._check_cancel()
        raise ValueError('隔离验收：媒体请求已禁用，用于测试失败和重试')


for stage in workflow_engine.agent_factories:
    workflow_engine.agent_factories[stage] = DisabledAgent
workflow_engine.agent_factories[WorkflowStage.SCRIPT_GENERATION] = OfflineScript
workflow_engine.agent_factories[WorkflowStage.POST_PRODUCTION] = VideoEditorAgent
if not workflow_engine.get_state('phase6-legacy-preview'):
    workflow_engine.create_session('phase6-legacy-preview', {'idea': '[离线测试] 历史项目兼容', 'episodes': 4, 'llm_model': 'offline'})
    workflow_engine.update_artifact('phase6-legacy-preview', 'script_generation', SCRIPT)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8020)

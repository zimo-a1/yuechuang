"""Isolated phase-2 browser fixture. Uses real validation logic and fake LLM responses."""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if not os.environ.get('YC_STATE_DIR'):
    raise RuntimeError('Set YC_STATE_DIR to an isolated temporary directory')
os.environ['YC_LEGACY_SESSION_DIR'] = str(Path(os.environ['YC_STATE_DIR']) / 'legacy')

from api.app import app  # noqa: E402
from api.dependencies import workflow_engine  # noqa: E402
from core.agents.base_agent import AgentInterface  # noqa: E402
from core.agents.storyboard_agent import StoryboardAgent  # noqa: E402
from core.orchestrator import WorkflowStage  # noqa: E402
from core.output_validation import ModelOutputError  # noqa: E402
from models import llm_client  # noqa: E402

SCRIPT = {
    'title': '结构校验验收（模拟）',
    'characters': [{'name': '阿青', 'description': '穿蓝色外套'}],
    'settings': [{'name': '客厅', 'description': '明亮的客厅'}],
    'episodes': [{'episode_number': 1, 'act_title': '出发', 'content': '1-1 日 内 客厅\n阿青：我们走。'}],
}
_, UNITS = StoryboardAgent._annotate_episode_script(
    SCRIPT['episodes'][0]['content'], SCRIPT['characters'], SCRIPT['settings'],
)


class FixtureLLM:
    def query(self, prompt, *args):
        time.sleep(0.05)
        if '前置规划助手' in prompt:
            return json.dumps([{'unit_ids': [unit['unit_id'] for unit in UNITS]}])
        if '连续性检查员' in prompt:
            return '{"issues": [], "patches": []}'
        return '{}'  # Repeated invalid design triggers a labelled deterministic fallback.


class DisabledAgent(AgentInterface):
    async def process(self, input_data, intervention=None):
        raise ModelOutputError()


llm_client.LLM = FixtureLLM
for stage in workflow_engine.agent_factories:
    workflow_engine.agent_factories[stage] = DisabledAgent
workflow_engine.agent_factories[WorkflowStage.STORYBOARD] = StoryboardAgent
if not workflow_engine.get_state('phase2-demo'):
    workflow_engine.create_session('phase2-demo', {
        'idea': '第二阶段结构校验验收（模拟，不调用模型）', 'llm_model': 'deepseek-chat',
        'vlm_model': 'agnes-2.5-flash', 'image_t2i_model': 'agnes-image-2.5-flash',
        'image_it2i_model': 'agnes-image-2.5-flash', 'video_model': 'agnes-video-2.5-flash',
        'episodes': 1,
    })
    workflow_engine.update_artifact('phase2-demo', 'script_generation', SCRIPT)

# This isolated fixture intentionally begins at stage 3; seed its prior reviews.
state = workflow_engine.get_state('phase2-demo')
if not state.confirmed_stages:
    state.confirmed_stages = ['script_generation', 'character_design']
    workflow_engine.save_session_to_disk('phase2-demo')

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8012)

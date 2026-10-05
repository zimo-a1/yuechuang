"""Isolated browser acceptance fixture. MOCK ONLY; never calls a model provider.

Run with YC_STATE_DIR pointing to a temporary directory. Serves on 127.0.0.1:8011.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if not os.environ.get('YC_STATE_DIR'):
    raise RuntimeError('Set YC_STATE_DIR to an isolated temporary directory')

os.environ["YC_LEGACY_SESSION_DIR"] = str(Path(os.environ["YC_STATE_DIR"]) / "legacy")

from api.app import app  # noqa: E402
from api.dependencies import workflow_engine  # noqa: E402
from core.agents.base_agent import AgentInterface  # noqa: E402


class DemoAgent(AgentInterface):
    async def process(self, input_data, intervention=None):
        for step in range(1, 21):
            self._check_cancel()
            self._report_progress('后台任务验证', f'受控测试进度 {step}/20', step * 5)
            await asyncio.sleep(1)
        return {'payload': {
            'title': '第一阶段后台任务验收（模拟数据）',
            'logline': '刷新页面后，后台任务仍继续执行。此内容仅用于工程验证。',
            'characters': [], 'settings': [], 'scenes': [], 'episodes': [],
        }, 'requires_intervention': True}


for stage in workflow_engine.agent_factories:
    workflow_engine.agent_factories[stage] = DemoAgent
if not workflow_engine.get_state('phase1-demo'):
    workflow_engine.create_session('phase1-demo', {
        'idea': '后台任务持久化验收（模拟，不调用模型）',
        'llm_model': 'deepseek-chat', 'vlm_model': 'agnes-2.5-flash',
        'image_t2i_model': 'agnes-image-2.5-flash', 'image_it2i_model': 'agnes-image-2.5-flash',
        'video_model': 'agnes-video-2.5-flash', 'episodes': 1,
    })

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8011)

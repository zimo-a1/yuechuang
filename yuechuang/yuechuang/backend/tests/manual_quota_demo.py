"""Phase-3 offline API fixture. Real guards/storage, fake provider; never use real sessions."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if not os.environ.get('YC_STATE_DIR'):
    raise RuntimeError('Set YC_STATE_DIR to a new isolated temporary directory')
fixture_root = Path(os.environ['YC_STATE_DIR']).resolve()
fixture_root.mkdir(parents=True, exist_ok=True)
os.environ['YC_LEGACY_SESSION_DIR'] = str(fixture_root / 'legacy')

from api.app import app  # noqa: E402
from api.dependencies import workflow_engine  # noqa: E402
from config import settings  # noqa: E402
from core.agents.base_agent import AgentInterface  # noqa: E402
from core.agents.video_agent import VideoDirectorAgent  # noqa: E402
from core.orchestrator import WorkflowStage  # noqa: E402
from models import video_client  # noqa: E402


class OfflineVideoClient:
    def generate_video(self, **kwargs):
        # Deliberately not a playable video; this fixture only verifies admission.
        Path(kwargs['save_path']).write_bytes(b'OFFLINE_QUOTA_FIXTURE')


class DisabledAgent(AgentInterface):
    async def process(self, input_data, intervention=None):
        raise RuntimeError('Only offline video admission is enabled in this fixture')


video_client.VideoClient = OfflineVideoClient
os.chdir(fixture_root)
settings.TEMP_DIR = str(fixture_root / 'uploads')
for stage in workflow_engine.agent_factories:
    workflow_engine.agent_factories[stage] = DisabledAgent
workflow_engine.agent_factories[WorkflowStage.VIDEO_GENERATION] = VideoDirectorAgent
if not workflow_engine.get_state('phase3-demo'):
    workflow_engine.create_session('phase3-demo', {
        'idea': '第三阶段离线额度验收（不调用供应商）',
        'llm_model': 'offline', 'vlm_model': 'offline',
        'image_t2i_model': 'offline', 'image_it2i_model': 'offline',
        'video_model': 'agnes-video-2.5-flash', 'enable_concurrency': False,
    })
    reference = fixture_root / 'reference.png'
    from PIL import Image
    Image.new('RGB', (8, 8), 'blue').save(reference)
    state = workflow_engine.get_state('phase3-demo')
    state.confirmed_stages = ['script_generation', 'character_design', 'storyboard', 'reference_generation']
    state.artifacts = {
        'storyboard': {'episodes': [{'episode_number': 1, 'segments': [
            {'segment_id': f'seg{i}', 'total_duration': 5, 'shots': [{'duration': 5, 'content': '离线测试'}]}
            for i in (1, 2)]}]},
        'reference_generation': {'scenes': [{'id': f'seg{i}', 'selected': str(reference)} for i in (1, 2)]},
    }
    workflow_engine.save_session_to_disk('phase3-demo')

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8013, log_config=None)

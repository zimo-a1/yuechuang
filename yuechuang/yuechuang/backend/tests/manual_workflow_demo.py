"""Isolated six-stage UI fixture: synthetic artifacts and local FFmpeg, zero provider calls."""
import os
import subprocess
import sys
from pathlib import Path

backend = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend))
sys.path.insert(0, str(backend / 'tests'))
if not os.environ.get('YC_STATE_DIR'):
    raise RuntimeError('Set YC_STATE_DIR to an isolated temporary directory')
root = Path(os.environ['YC_STATE_DIR']).resolve()
root.mkdir(parents=True, exist_ok=True)
os.environ['YC_LEGACY_SESSION_DIR'] = str(root / 'legacy')
os.chdir(root)

from config import settings  # noqa: E402

settings.CODE_DIR = str(root / 'code')
settings.TEMP_DIR = str(root / 'uploads')
from PIL import Image  # noqa: E402
from test_workflow_acceptance import META, artifacts, install_offline_agents  # noqa: E402

from api.app import app  # noqa: E402
from api.dependencies import workflow_engine  # noqa: E402
from upload_validation import ffmpeg_executable  # noqa: E402

assets = root / 'code/result/fixture'
assets.mkdir(parents=True, exist_ok=True)
image = assets / 'offline.png'
Image.new('RGB', (180, 320), '#3974a8').save(image)
clip = assets / 'offline.mp4'
if not clip.exists():
    subprocess.run([ffmpeg_executable(), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=180x320:r=10:d=1',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip)], check=True, timeout=20)
data = artifacts(str(clip.relative_to(root)))
for stage, key in [('character_design', 'characters'), ('reference_generation', 'scenes'), ('video_generation', 'clips')]:
    for item in data[stage][key]:
        path = str(clip.relative_to(root)) if key == 'clips' else str(image.relative_to(root))
        item.update(name='离线验收素材', description='本地合成素材，不代表模型生成效果', selected=path,
                    versions=[path], status='done', episode=1, index=1)
data['script_generation']['characters'][0].update(description='离线测试角色', personality=[], role='主角')
data['storyboard']['episodes'][0].update(episode_title='离线测试')
segment = data['storyboard']['episodes'][0]['segments'][0]
segment.update(segment_number=1, episode_number=1, location='窗前', characters=['人物'], total_duration=1)
segment['shots'][0].update(shot_number=1, shot_type='中景')
install_offline_agents(workflow_engine, data)
if not workflow_engine.get_state('phase4-demo'):
    workflow_engine.create_session('phase4-demo', META)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8014, log_config=None)

"""Repair the existing sample: local trim + one bounded first-frame continuation.

Default/--prepare: local files only. --execute: one paid create, never resubmits.
The anchor must be published and mapped before execute; no automatic uploads.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.agents.video_agent import VideoDirectorAgent
from models.video_client import VideoClient
from models.video_tudou import TudouVideoClient
from tests.manual_dialogue_preview import build_preview
from upload_validation import ffmpeg_executable

SOURCE = ROOT / 'temp/tudou-dialogue-smoke-20260930/dialogue_01.mp4'
OUT = ROOT / 'temp/tudou-dialogue-continuity-20260930'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    os.replace(temporary, path)


def ffmpeg(args):
    subprocess.run([ffmpeg_executable(), '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                    *args], check=True, capture_output=True, timeout=120)


def prepare():
    """Cut reviewed 24fps sample before the turn; anchor is decoded from kept clip."""
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = OUT / 'plan.json'
    if manifest.exists():
        plan = json.loads(manifest.read_text())
        verify(plan)
        return plan
    kept, anchor = OUT / 'dialogue_01_trimmed.mp4', OUT / 'continuation_first_frame.png'
    ffmpeg(['-protocol_whitelist', 'file,pipe', '-i', str(SOURCE), '-t', '4.25',
            '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'libx264', '-crf', '18', '-preset', 'fast',
            '-c:a', 'aac', '-movflags', '+faststart', str(kept)])
    # The 102nd frame is the last retained video frame at 24fps (4.20833s).
    ffmpeg(['-protocol_whitelist', 'file,pipe', '-i', str(kept),
            '-vf', 'select=eq(n\\,101)', '-frames:v', '1', str(anchor)])
    if not anchor.is_file():
        raise ValueError('Retained clip is too short; no continuation anchor')
    segment = build_preview()['episode']['segments'][1]
    segment['shots'][0]['content'] = '从输入首帧继续同一个固定双人中景，同伴答话，青年安静倾听。'
    characters = {'characters': [
        {'name': '青年', 'description': '首帧左侧黑色夹克青年，保持首帧外观。'},
        {'name': '同伴', 'description': '首帧右侧戴眼镜、黑西装白衬衫的中年男子，保持首帧外观。'},
    ]}
    prompt = (ROOT / 'prompts/video/continuation_zh.txt').read_text().strip() + '\n' + VideoDirectorAgent()._assemble_prompt(
        segment, '写实电影；完全继承输入首帧的光线、构图、色温和景别', characters)
    plan = {'version': 1, 'status': 'prepared_not_generated',
            'model': 'grok-imagine-video-1.5', 'duration': 6, 'video_ratio': '9:16',
            'mode': 'first_frame', 'video_calls': 1, 'new_images': 0, 'llm_calls': 0,
            'source': str(SOURCE), 'source_sha256': digest(SOURCE),
            'keep_seconds': 4.25, 'keep_frames': 102, 'source_fps': 24,
            'kept': str(kept), 'kept_sha256': digest(kept),
            'anchor': str(anchor), 'anchor_sha256': digest(anchor), 'prompt': prompt}
    save(manifest, plan)
    return plan


def verify(plan):
    for key in ('source', 'kept', 'anchor'):
        if digest(plan[key]) != plan[f'{key}_sha256']:
            raise ValueError('Continuity source changed; refusing to reuse old plan')


class RecordedClient(TudouVideoClient):
    def __init__(self, state, path):
        super().__init__()
        self.state, self.path = state, path

    def _poll(self, task_id, **kwargs):
        self.state.update(status='polling', task_id=task_id)
        save(self.path, self.state)
        return super()._poll(task_id, **kwargs)

    def _download(self, url, save_path):
        self.state.update(status='downloading', result_url=url)
        save(self.path, self.state)
        return super()._download(url, save_path)


def execute(plan):
    verify(plan)
    state_path, video = OUT / 'state.json', OUT / 'dialogue_02_continued.mp4'
    fingerprint = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    state = json.loads(state_path.read_text()) if state_path.exists() else {'version': 1, 'status': 'pending', 'plan_sha256': fingerprint}
    if state.get('plan_sha256') != fingerprint:
        raise ValueError('Existing run belongs to a different plan; no new submission')
    if state['status'] == 'completed' and video.is_file():
        return
    client = RecordedClient(state, state_path)
    if state.get('result_url'):
        client._download(state['result_url'], str(video))
    elif state.get('task_id'):
        client._download(client._poll(state['task_id']), str(video))
    elif state['status'] == 'pending':
        # Real first-frame input, not another independent character-reference call.
        args = dict(prompt=plan['prompt'], image_path=plan['anchor'], model=plan['model'],
                    duration=plan['duration'], video_ratio=plan['video_ratio'], resolution='720P')
        TudouVideoClient.build_request(**args)
        state['status'] = 'submission_unknown'
        save(state_path, state)
        facade = VideoClient()
        facade._tudou_client = client
        facade.generate_video(**args, save_path=str(video))
    else:
        raise ValueError('Submission outcome unknown; refusing duplicate create')
    state['status'] = 'completed'
    save(state_path, state)


def run(execute_paid=False):
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = prepare()
        if execute_paid:
            execute(plan)
        print(json.dumps({'status': 'executed' if execute_paid else 'prepared_not_generated',
                          'proposed_video_calls': 1, 'new_images': 0, 'llm_calls': 0,
                          'kept_seconds': 4.25, 'new_video_seconds': 6}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true', help='Local preparation only (default)')
    parser.add_argument('--execute', action='store_true', help='One paid call; requires mapped anchor and authorization')
    try:
        run(parser.parse_args().execute)
    except Exception as exc:
        print(f'Continuity test stopped ({type(exc).__name__}); no automatic create retry', file=sys.stderr)
        raise SystemExit(1) from None

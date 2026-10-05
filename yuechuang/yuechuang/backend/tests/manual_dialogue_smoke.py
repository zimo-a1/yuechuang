"""Bounded two-clip real smoke test; default dry-run, resume never recreates tasks."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manual_dialogue_preview import build_preview
from core.agents.video_agent import VideoDirectorAgent
from models.video_client import VideoClient
from models.video_tudou import TudouVideoClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'temp/tudou-dialogue-smoke-20260930'
STATE = OUT / 'state.json'


def save(state):
    OUT.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    os.replace(temporary, STATE)


class RecordedClient(TudouVideoClient):
    def __init__(self, state, row):
        super().__init__()
        self.state, self.row = state, row

    def _poll(self, task_id, **kwargs):
        self.row.update(task_id=task_id, status='polling')
        save(self.state)
        return super()._poll(task_id, **kwargs)

    def _download(self, url, save_path):
        self.row.update(result_url=url, status='downloading')
        save(self.state)
        return super()._download(url, save_path)


def run(execute=False):
    os.chdir(ROOT)
    preview = build_preview()
    if not execute:
        print(json.dumps(preview['proposed_generation'], ensure_ascii=False))
        return
    refs = [str(ROOT / 'code/result/image/f64a7555a2c54ddfb94b9250957507a4/Assets/characters' / name)
            for name in ['char_16e376.png', 'char_06812c.png']]
    characters = {'characters': [
        {'name': '青年', 'description': '参考图1中的青年男子，黑色夹克、黑色衬衫，短黑发，无眼镜。'},
        {'name': '同伴', 'description': '参考图2中的中年男子，眼镜、黑色西装、白色衬衫。'},
    ]}
    prompts = [VideoDirectorAgent()._assemble_prompt(segment, '写实电影，自然光', characters)
               + '\n参考图1是青年，参考图2是同伴。每张图是同一个人的多视图设定，仅提取人物外观。成片是正常单画面，仅有这两个人，禁止拼图、分屏、重复人物。背景为空白墙面，不出现纸张、屏幕、标牌。'
               for segment in preview['episode']['segments']]
    # Validate the entire batch before the first paid call.
    for prompt in prompts:
        TudouVideoClient.build_request(prompt, reference_image_paths=refs, duration=6, video_ratio='9:16')
    state = json.loads(STATE.read_text()) if STATE.exists() else {'model': 'grok-imagine-video-1.5', 'clips': [
        {'index': i + 1, 'status': 'pending', 'prompt': prompt} for i, prompt in enumerate(prompts)]}
    if len(state['clips']) != 2 or [row['prompt'] for row in state['clips']] != prompts:
        raise RuntimeError('Existing run differs; refusing additional creates')
    for row in state['clips']:
        target = OUT / f"dialogue_{row['index']:02d}.mp4"
        if row['status'] == 'completed' and target.exists():
            continue
        client = RecordedClient(state, row)
        try:
            if row.get('result_url'):
                client._download(row['result_url'], str(target))
            elif row.get('task_id'):
                client._download(client._poll(row['task_id']), str(target))
            elif row['status'] == 'pending':
                row['status'] = 'submission_unknown'
                save(state)
                facade = VideoClient()
                facade._tudou_client = client
                facade.generate_video(prompt=row['prompt'], image_path=None, save_path=str(target),
                                      model=state['model'], duration=6, video_ratio='9:16',
                                      resolution='720P', reference_image_paths=refs)
            else:
                raise RuntimeError('Submission outcome unknown; refusing duplicate create')
            row.update(status='completed', path=str(target))
            save(state)
            print(f"clip {row['index']}: completed", flush=True)
        except Exception as exc:
            row['last_error_type'] = type(exc).__name__
            save(state)
            print(f"clip {row['index']}: stopped ({type(exc).__name__}); no automatic create retry", flush=True)
            raise SystemExit(1) from None


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    run(parser.parse_args().execute)

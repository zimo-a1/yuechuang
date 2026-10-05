"""Reviewable, opt-in provider contract smoke; default invocation makes zero API calls.

This is a small contract check, not a real six-stage film acceptance test.
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Only after explicit approval of the printed call plan')
    args = parser.parse_args()
    plan = {'text_creates': 1, 'image_creates': 1, 'video_creates': 1, 'video_seconds': 4,
            'creation_retries': 0, 'input': 'synthetic blue cube; no user documents',
            'pricing': 'supplier account pricing; not a currency budget guarantee'}
    print(json.dumps(plan, ensure_ascii=False))
    if not args.execute:
        return 0
    from api.logging_config import setup_concurrent_logging
    from config import Config
    setup_concurrent_logging()
    expected = {'LLM_MODEL': 'deepseek-flash', 'IMAGE_T2I_MODEL': 'agnes-image-2.5-flash',
                'VIDEO_FIRST_FRAME_MODEL': 'agnes-video-2.5-flash'}
    if any(getattr(Config, key) != value for key, value in expected.items()):
        print('Model configuration changed; review the call plan again.')
        return 2
    if not Config.DEEPSEEK_API_KEY or not Config.AGNES_API_KEY:
        print('Missing provider credentials.')
        return 2
    from core.output_validation import decode_json, validate_episodes
    from models.image_agnes import AgnesImageClient
    from models.llm_deepseek import DeepSeek
    from models.video_agnes import AgnesVideoClient
    from upload_validation import validate_upload
    sid = 'provider-smoke-' + uuid.uuid4().hex
    root = Path(Config.CODE_DIR) / 'result' / sid
    root.mkdir(parents=True)
    phase = 'text'
    try:
        text = DeepSeek(timeout=60, max_attempts=1)
        text.client = text.client.with_options(max_retries=0)
        text.max_tokens = 1024
        result = text.query('Return JSON only: {"episodes":[{"episode_number":1,"content":"A blue cube on a table."}]}',
                            model=Config.LLM_MODEL)
        validate_episodes(decode_json(result))
        phase = 'image'
        image = AgnesImageClient(timeout=120)
        image.max_attempts = 1
        paths = image.generate_image('A single blue cube on a plain white table, no text.',
                                     session_id=sid, model=Config.IMAGE_T2I_MODEL, size='720*1280')
        if not paths:
            raise ValueError('missing image')
        validate_upload(Path(paths[0]), Path(paths[0]).suffix)
        phase = 'video'
        video = AgnesVideoClient(timeout=60)
        video.max_attempts = 1
        output = root / 'sample.mp4'
        video.generate_video('A gentle camera move around the blue cube. No text.', paths[0], str(output),
                             model=Config.VIDEO_FIRST_FRAME_MODEL, duration=4, video_ratio='9:16', resolution='720P')
        validate_upload(output, '.mp4')
        print(json.dumps({'status': 'passed', 'scope': 'provider contracts only', 'output': str(output)}))
        return 0
    except Exception as exc:
        # Provider bodies/URLs may contain credentials. Never print the raw exception.
        print(json.dumps({'status': 'failed', 'phase': phase, 'error_type': type(exc).__name__, 'retries': 0}))
        return 1


if __name__ == '__main__':
    sys.exit(main())

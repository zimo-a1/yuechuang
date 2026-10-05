"""One opt-in Tudou Veo smoke. Dry run by default; never retry creation.

Run from backend: .venv/bin/python tests/manual_tudou_veo_smoke.py --execute
Resume polling: add --resume. State and video are saved under ignored temp/.
An ambiguous submission must be checked in the supplier console before any new call.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import time
from urllib.parse import quote, urlsplit

BACKEND = Path(__file__).resolve().parents[1]
PLAN_FILE = Path(__file__).parent / 'fixtures' / 'tudou_veo_smoke.json'
OUTPUT = BACKEND / 'temp' / 'tudou-veo31-smoke'


def read_key():
    key = os.environ.get('TUDOU_API_KEY', '').strip()
    env = BACKEND / '.env'
    if env.exists():
        for line in env.read_text().splitlines():
            name, separator, value = line.strip().partition('=')
            if separator and name.strip() == 'TUDOU_API_KEY':
                key = value.strip().strip('\"\'') or key
    return key


def save_state(state):
    temp = OUTPUT / 'state.json.tmp'
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    temp.replace(OUTPUT / 'state.json')


def task_data(response):
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict) or body.get('code', 200) != 200:
        raise ValueError('Invalid task response')
    data = body.get('data', body)
    if not isinstance(data, dict):
        raise ValueError('Missing task data')
    return data


def run(plan, resume, key):
    import httpx
    path = OUTPUT / 'state.json'
    if path.exists() and not resume:
        print('已有提交记录；禁止重复创建。使用 --execute --resume 仅查询已有任务。')
        return 2
    if resume:
        if not path.exists():
            print('没有可恢复的任务。')
            return 2
        state = json.loads(path.read_text())
        if not state.get('task_id'):
            print('提交结果未知或未取得任务 ID；先查供应商控制台，禁止自动重提。')
            return 2
        if state.get('status') in ('downloaded', 'failed'):
            print(json.dumps(state, ensure_ascii=False))
            return 0 if state['status'] == 'downloaded' else 1
    else:
        state = {'schema_version': 1, 'status': 'submission_unknown',
                 'started_at': time.time(), 'request': plan['request'],
                 'create_attempts': 1, 'actual_cost': None}
        save_state(state)  # Persist before sending; timeout/crash must not cause a duplicate.

    phase = 'query' if resume else 'submit'
    try:
        with httpx.Client(base_url=plan['base_url'],
                          headers={'Authorization': 'Bearer ' + key},
                          timeout=plan['request_timeout_seconds'],
                          follow_redirects=False) as client:
            if not resume:
                data = task_data(client.post('/v1/videos/generations', json=plan['request']))
                task_id = data.get('id') or data.get('task_id')
                if not isinstance(task_id, str) or not task_id:
                    raise ValueError('Missing task id')
                state.update(task_id=task_id, status='submitted')
                save_state(state)
            deadline = time.monotonic() + plan['poll_timeout_seconds']
            phase = 'query'
            while time.monotonic() < deadline:
                time.sleep(plan['poll_interval_seconds'])
                response = client.get('/v1/tasks/' + quote(state['task_id'], safe=''))
                if response.status_code == 429 or response.status_code >= 500:
                    print(json.dumps({'phase': phase, 'http_status': response.status_code}), flush=True)
                    continue  # Bounded by polling deadline; never creates another task.
                data = task_data(response)
                status = data.get('status')
                if status not in ('submitted', 'queued', 'processing', 'completed', 'failed'):
                    raise ValueError('Unknown task status')
                state.update(status=status, elapsed_seconds=round(time.time() - state['started_at'], 1))
                save_state(state)
                print(json.dumps({'status': status, 'elapsed_seconds': state['elapsed_seconds']}), flush=True)
                if status == 'failed':
                    return 1  # Do not print raw provider errors or signed URLs.
                if status == 'completed':
                    result = data.get('result', {})
                    url = result.get('video_url') if isinstance(result, dict) else None
                    if not url and isinstance(result, dict):
                        # Tudou Wan's live response stores MP4 URLs under images.
                        # The downloaded file is still checked for an MP4 header.
                        videos = result.get('videos') or result.get('images')
                        if isinstance(videos, list) and videos and isinstance(videos[0], dict):
                            urls = videos[0].get('url')
                            url = urls[0] if isinstance(urls, list) and urls else urls
                    if not isinstance(url, str) or urlsplit(url).scheme != 'https':
                        raise ValueError('Missing HTTPS video URL')
                    break
            else:
                print('本轮查询超时，任务记录已保存；可恢复查询，不会重新创建。')
                return 2

        phase = 'download'
        target = OUTPUT / 'sample.mp4'
        partial = OUTPUT / 'sample.mp4.part'
        # Separate client: never forward the supplier API key to the media host.
        with httpx.Client(timeout=60, follow_redirects=True) as media:
            with media.stream('GET', url) as response:
                response.raise_for_status()
                size = 0
                download_deadline = time.monotonic() + 180
                with partial.open('wb') as out:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 100 * 1024 * 1024 or time.monotonic() > download_deadline:
                            raise ValueError('Download limit reached')
                        out.write(chunk)
        with partial.open('rb') as video:
            if b'ftyp' not in video.read(256):
                raise ValueError('Response is not an MP4')
        partial.replace(target)
        previous_error = {k: state.pop(k) for k in ('error_phase', 'error_type', 'http_status') if k in state}
        if previous_error:
            state['recovered_error'] = previous_error
        state.update(status='downloaded', output=str(target), bytes=size)
        save_state(state)
        print(json.dumps(state, ensure_ascii=False))
        return 0
    except Exception as exc:
        state.update(error_phase=phase, error_type=type(exc).__name__)
        if isinstance(exc, httpx.HTTPStatusError):
            state['http_status'] = exc.response.status_code
        save_state(state)
        print(json.dumps({k: state[k] for k in ('error_phase', 'error_type', 'http_status') if k in state}))
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    plan = json.loads(PLAN_FILE.read_text())
    print(json.dumps({'plan': plan, 'max_new_tasks': 0 if args.resume else 1,
                      'creation_retries': 0, 'execute': args.execute}, ensure_ascii=False))
    if not args.execute:
        return 0
    key = read_key()
    if not key:
        print('缺少 TUDOU_API_KEY；未发送任何请求。')
        return 2
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / 'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('已有测试进程在运行；未创建任务。')
            return 2
        return run(plan, args.resume, key)


if __name__ == '__main__':
    raise SystemExit(main())

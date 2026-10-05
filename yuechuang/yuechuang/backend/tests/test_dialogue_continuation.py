import json
from pathlib import Path
import pytest
from tests import manual_dialogue_continuation as repair
from tests.manual_dialogue_preview import build_preview
from core.shot_control import compile_shot_control


@pytest.fixture
def plan(monkeypatch, tmp_path):
    source = tmp_path / 'source.mp4'
    out = tmp_path / 'repair'
    monkeypatch.setattr(repair, 'SOURCE', source)
    monkeypatch.setattr(repair, 'OUT', out)
    # Changing test frames catch wrong extraction better than a static solid color.
    repair.ffmpeg(['-f', 'lavfi', '-i', 'testsrc2=s=64x96:r=24:d=5',
                   '-f', 'lavfi', '-i', 'sine=frequency=440:duration=5',
                   '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)])
    return repair.prepare()


def test_local_prepare_is_repeatable_and_anchor_matches_kept_last_frame(plan):
    assert repair.prepare() == plan
    assert plan['keep_frames'] == 102
    raw = repair.OUT / 'last.rgb'
    repair.ffmpeg(['-i', plan['kept'], '-vf', 'select=eq(n\\,101)', '-frames:v', '1',
                   '-pix_fmt', 'rgb24', '-f', 'rawvideo', str(raw)])
    from PIL import Image
    with Image.open(plan['anchor']) as img:
        assert img.convert('RGB').tobytes() == raw.read_bytes()
    assert '房间' in plan['prompt'] and '输入首帧' in plan['prompt']
    assert plan['prompt'].count('好，我陪你去车站。') == 1
    assert not (repair.OUT / 'state.json').exists()


def test_trimmed_asset_change_stops_before_any_client(plan, monkeypatch):
    Path(plan['kept']).write_bytes(b'changed')
    monkeypatch.setattr(repair, 'RecordedClient', lambda *a: pytest.fail('must not construct client'))
    with pytest.raises(ValueError, match='source changed'):
        repair.execute(plan)


def test_no_mapping_blocks_before_submission_state(plan, monkeypatch):
    monkeypatch.delenv('TUDOU_REFERENCE_URL_MAP_FILE', raising=False)
    monkeypatch.setattr(repair.VideoClient, 'generate_video', lambda *a, **k: pytest.fail('must not submit'))
    with pytest.raises(ValueError):
        repair.execute(plan)
    assert not (repair.OUT / 'state.json').exists()


def test_single_first_frame_create_and_no_duplicate_after_timeout(plan, monkeypatch):
    calls = []
    monkeypatch.setattr(repair.TudouVideoClient, 'build_request', staticmethod(lambda **kw: {}))
    def generate(self, **kw):
        calls.append(kw)
        raise TimeoutError()
    monkeypatch.setattr(repair.VideoClient, 'generate_video', generate)
    with pytest.raises(TimeoutError):
        repair.execute(plan)
    with pytest.raises(ValueError, match='duplicate create'):
        repair.execute(plan)
    assert len(calls) == 1
    assert calls[0]['image_path'] == plan['anchor']
    assert 'reference_image_paths' not in calls[0]


def test_known_task_resume_does_not_require_unexpired_anchor_url(plan, monkeypatch):
    fingerprint = repair.hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    repair.save(repair.OUT / 'state.json', {'version': 1, 'status': 'polling',
                'task_id': 'existing-task', 'plan_sha256': fingerprint})
    monkeypatch.delenv('TUDOU_REFERENCE_URL_MAP_FILE', raising=False)
    monkeypatch.setattr(repair.VideoClient, 'generate_video', lambda *a, **k: pytest.fail('no new create'))
    monkeypatch.setattr(repair.RecordedClient, '_poll', lambda self, task: 'https://example.com/video.mp4')
    monkeypatch.setattr(repair.RecordedClient, '_download', lambda self, url, path: Path(path).write_bytes(b'video'))
    repair.execute(plan)
    repair.execute(plan)
    assert json.loads((repair.OUT / 'state.json').read_text())['status'] == 'completed'


def test_dialogue_end_stays_facing_partner_until_clip_end():
    segment = build_preview()['episode']['segments'][0]
    prompt = compile_shot_control(segment)
    assert '3.5–6秒：对白结束' in prompt
    assert '直到最后一帧' in prompt and '不转头看镜头' in prompt
    assert prompt.count('我决定明天出发。') == 1

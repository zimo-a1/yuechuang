import json
import pytest
import manual_dialogue_smoke as smoke


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(smoke, 'OUT', tmp_path)
    monkeypatch.setattr(smoke, 'STATE', tmp_path / 'state.json')
    monkeypatch.setattr(smoke.TudouVideoClient, 'build_request', staticmethod(lambda *a, **k: {}))


def test_dry_run_never_constructs_client(monkeypatch):
    monkeypatch.setattr(smoke, 'VideoClient', lambda: pytest.fail('network client constructed'))
    smoke.run()


def test_submission_failure_stops_batch_and_cannot_recreate(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    calls = []
    def fail(self, **kwargs):
        calls.append(kwargs)
        raise TimeoutError()
    monkeypatch.setattr(smoke.VideoClient, 'generate_video', fail)
    for _ in range(2):
        with pytest.raises(SystemExit):
            smoke.run(True)
    assert len(calls) == 1
    state = json.loads(smoke.STATE.read_text())
    assert [r['status'] for r in state['clips']] == ['submission_unknown', 'pending']


def test_completed_run_does_not_create_again(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    calls = []
    def generate(self, **kwargs):
        calls.append(kwargs)
        from pathlib import Path
        Path(kwargs['save_path']).write_bytes(b'video')
    monkeypatch.setattr(smoke.VideoClient, 'generate_video', generate)
    smoke.run(True)
    smoke.run(True)
    assert len(calls) == 2
    assert all(row['status'] == 'completed' for row in json.loads(smoke.STATE.read_text())['clips'])

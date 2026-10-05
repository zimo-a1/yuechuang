import json
import sys

import httpx
import pytest
import manual_tudou_veo_smoke as smoke


def setup_plan(monkeypatch, tmp_path, handler):
    plan = json.loads(smoke.PLAN_FILE.read_text())
    plan['poll_interval_seconds'] = 0
    monkeypatch.setattr(smoke, 'OUTPUT', tmp_path)
    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, 'Client', lambda **kw: real_client(transport=transport, **kw))
    return plan


def test_dry_run_never_reads_key_or_calls_network(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['manual_tudou_veo_smoke.py'])
    def forbidden():
        raise AssertionError('Dry run accessed credentials')
    monkeypatch.setattr(smoke, 'read_key', forbidden)
    assert smoke.main() == 0


def test_ambiguous_submit_is_not_repeated(monkeypatch, tmp_path):
    calls = []
    def handler(request):
        calls.append(request.method)
        raise httpx.ReadTimeout('Sensitive supplier error must not be printed')
    plan = setup_plan(monkeypatch, tmp_path, handler)
    assert smoke.run(plan, False, 'unit-test-only') == 1
    assert smoke.run(plan, False, 'unit-test-only') == 2
    assert smoke.run(plan, True, 'unit-test-only') == 2
    assert calls == ['POST']
    assert json.loads((tmp_path / 'state.json').read_text())['status'] == 'submission_unknown'


def test_resume_download_has_no_api_key(monkeypatch, tmp_path):
    calls = []
    def handler(request):
        calls.append((request.method, request.url.host))
        assert request.method == 'GET'
        if request.url.host == 'api.ai-tudou.net':
            assert request.headers['Authorization'] == 'Bearer unit-test-only'
            return httpx.Response(200, json={'code': 200, 'data': {
                'id': 'saved-task', 'status': 'completed',
                'result': {'video_url': 'https://media.example.test/sample.mp4'}}})
        assert 'Authorization' not in request.headers
        return httpx.Response(200, content=b'\x00\x00\x00\x18ftypisom' + b'\x00' * 32)
    plan = setup_plan(monkeypatch, tmp_path, handler)
    smoke.save_state({'schema_version': 1, 'status': 'processing', 'task_id': 'saved-task',
                      'started_at': smoke.time.time(), 'actual_cost': None})
    assert smoke.run(plan, True, 'unit-test-only') == 0
    state = json.loads((tmp_path / 'state.json').read_text())
    assert state['status'] == 'downloaded'
    assert state['actual_cost'] is None
    assert (tmp_path / 'sample.mp4').exists()
    assert calls == [('GET', 'api.ai-tudou.net'), ('GET', 'media.example.test')]


def test_missing_key_blocks_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'argv', ['manual_tudou_veo_smoke.py', '--execute'])
    monkeypatch.setattr(smoke, 'read_key', lambda: '')
    monkeypatch.setattr(smoke, 'OUTPUT', tmp_path / 'unused')
    assert smoke.main() == 2
    assert not smoke.OUTPUT.exists()


def test_submit_503_is_saved_without_retry_or_error_body(monkeypatch, tmp_path, capsys):
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(503, json={'error': {'message': 'private-provider-details'}})
    plan = setup_plan(monkeypatch, tmp_path, handler)
    assert smoke.run(plan, False, 'unit-test-only') == 1
    assert calls == ['POST']
    state_text = (tmp_path / 'state.json').read_text()
    assert json.loads(state_text)['http_status'] == 503
    assert 'private-provider-details' not in state_text + capsys.readouterr().out


@pytest.mark.parametrize('media_field', ['videos', 'images'])
def test_wan_task_alias_queued_and_video_array(monkeypatch, tmp_path, media_field):
    calls = []
    def handler(request):
        calls.append(request.method)
        if request.method == 'POST':
            return httpx.Response(200, json={'code': 200, 'data': {'task_id': 'wan-task'}})
        if request.url.host == 'api.ai-tudou.net':
            if len(calls) == 2:
                return httpx.Response(200, json={'status': 'queued'})
            return httpx.Response(200, json={'status': 'completed', 'result': {
                media_field: [{'url': ['https://media.example.test/wan.mp4']}]}})
        assert 'Authorization' not in request.headers
        return httpx.Response(200, content=b'\x00\x00\x00\x18ftypisom' + b'\x00' * 32)
    plan = setup_plan(monkeypatch, tmp_path, handler)
    plan['request']['model'] = 'wan3.0'
    assert smoke.run(plan, False, 'unit-test-only') == 0
    assert calls == ['POST', 'GET', 'GET', 'GET']
    assert json.loads((tmp_path / 'state.json').read_text())['task_id'] == 'wan-task'

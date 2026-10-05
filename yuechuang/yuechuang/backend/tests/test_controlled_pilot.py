import json
import sys

import pytest

from tests import manual_controlled_pilot as pilot


def test_controlled_pilot_defaults_to_no_network(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["manual_controlled_pilot.py"])
    monkeypatch.setattr(pilot, "execute", lambda _url: (_ for _ in ()).throw(AssertionError("must not execute")))

    assert pilot.main() == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["execute"] is False
    assert plan["plan"]["gemini_images"] == 8
    assert plan["plan"]["grok_videos"] == 4


def test_request_accepts_completed_ndjson_response(monkeypatch):
    class Response:
        def read(self):
            return (
                b'{"type":"progress","percent":100}\n'
                b'{"type":"stage_complete","stage":"script_generation"}\n'
                b'{"type":"done"}\n'
            )

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(pilot, "urlopen", lambda *_args, **_kwargs: Response())

    assert pilot._request("http://example.test", "POST", "/execute") == {
        "status": "completed",
        "_stream_completed": True,
    }


def test_completed_stages_reuses_waiting_artifact_after_restart():
    snapshot = {
        "status": {"script_generation": "waiting", "character_design": "error"},
        "artifacts": {"script_generation": {"episodes": []}},
    }

    assert pilot._completed_stages(snapshot) == ["script_generation"]


def test_controlled_configuration_rejects_stale_backend_response():
    with pytest.raises(RuntimeError, match="未加载受控整集配置"):
        pilot._assert_controlled_configuration({"controlled_episode": False})

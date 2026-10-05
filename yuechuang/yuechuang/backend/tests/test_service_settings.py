"""Stage 7 contracts. Only local fixtures; no provider generation or real keys."""
import asyncio
import copy
import json
from uuid import uuid4

import httpx
import pytest

from config import Config, DEFAULT_CONFIG
from core.service_settings import (ServiceSettings, service_settings, model_alias, project_defaults,
                                   validate_video_options, SettingsError, probe_connection)
from core.configuration_runtime import capture_runtime, runtime_scope, ContextThreadPoolExecutor
from core.orchestrator import WorkflowEngine, WorkflowStage
from core.tasks import TaskManager
from core.agents.base_agent import AgentInterface
from tests.test_output_validation import SCRIPT


@pytest.fixture
def settings_store(tmp_path, monkeypatch):
    monkeypatch.setattr(service_settings, "directory", tmp_path / "settings")
    monkeypatch.setattr(service_settings, "env_path", tmp_path / ".env")
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["models"].update(llm="deepseek-flash", vlm="agnes-2.5-flash", image_t2i="gemini-3-pro-image-preview", image_it2i="gemini-3-pro-image-preview",
                         video_first_frame="grok-imagine-video-1.5", video_start_end="", video_reference="", video_text="grok-imagine-video-1.5")
    cfg["generation"]["video_generation_mode"] = "text"
    monkeypatch.setattr(Config, "CONFIG", cfg)
    for provider in cfg["api_providers"]:
        for field in ("API_KEY", "ACCESS_KEY", "SECRET_KEY"):
            attr = f"{provider.upper()}_{field}"
            if hasattr(Config, attr):
                monkeypatch.setattr(Config, attr, "")
    return service_settings


def add(store, **overrides):
    values = {"name": "离线文本", "kind": "text", "provider": "deepseek", "base_url": "https://one.example.test/v1",
              "models": ["deepseek-flash"], "credentials": {"api_key": "offline-test-only"}, **overrides}
    doc = store.save_connection(store.read()["revision"], values)
    return doc["connections"][-1], values


def test_credentials_write_only_blank_keep_clear_and_restart(settings_store):
    store = settings_store
    conn, values = add(store)
    assert conn["credentials"] == {"api_key": True}
    assert "offline-test-only" not in json.dumps(store.public())
    assert "offline-test-only" not in (store.directory / "settings.sqlite3").read_bytes().decode(errors="ignore")
    assert store.env_path.stat().st_mode & 0o777 == 0o600
    original = store.runtime()["connections"][-1]["credentials"]["api_key"]
    store.save_connection(store.read()["revision"], {**values, "credentials": {"api_key": ""}}, conn["id"])
    restarted = ServiceSettings(store.directory, store.env_path)
    assert restarted.runtime()["connections"][-1]["credentials"]["api_key"] == original
    store.save_connection(store.read()["revision"], {**values, "credentials": {}, "clear_credentials": ["api_key"]}, conn["id"])
    assert ServiceSettings(store.directory, store.env_path).public()["connections"][-1]["credentials"]["api_key"] is False


def test_two_connections_same_model_use_distinct_clients_and_freeze(settings_store, monkeypatch):
    from models.llm_client import LLM
    from models.llm_deepseek import DeepSeek
    first, values = add(settings_store)
    second, _ = add(settings_store, name="备用", base_url="https://two.example.test/v1", credentials={"api_key": "offline-two"})
    observed = []
    monkeypatch.setattr(DeepSeek, "query", lambda self, *args, **kwargs: observed.append((self.base_url, self.api_key, kwargs["model"])) or "离线响应")
    # Constructors do not call providers. No remote calls inside patched query.
    old_client = LLM()
    settings_store.save_connection(settings_store.read()["revision"], {**values, "base_url": "https://changed.example.test/v1", "credentials": {"api_key": "offline-new"}}, first["id"])
    old_client.query("离线", model=model_alias(first["id"], "deepseek-flash"))
    old_client.query("离线", model=model_alias(second["id"], "deepseek-flash"))
    LLM().query("离线", model=model_alias(first["id"], "deepseek-flash"))
    assert observed == [("https://one.example.test/v1", "offline-test-only", "deepseek-flash"), ("https://two.example.test/v1", "offline-two", "deepseek-flash"), ("https://changed.example.test/v1", "offline-new", "deepseek-flash")]


def test_parallel_legacy_provider_context_isolation(settings_store, monkeypatch):
    monkeypatch.setattr(Config, "DEEPSEEK_BASE_URL", "https://before.example.test/v1")
    snapshot = capture_runtime()
    monkeypatch.setattr(Config, "DEEPSEEK_BASE_URL", "https://after.example.test/v1")
    with runtime_scope(snapshot), ContextThreadPoolExecutor(2) as pool:
        assert pool.submit(lambda: Config.DEEPSEEK_BASE_URL).result() == "https://before.example.test/v1"
    assert Config.DEEPSEEK_BASE_URL == "https://after.example.test/v1"


def test_named_video_connection_routes_native_model_and_credentials(settings_store, monkeypatch, tmp_path):
    from models.video_client import VideoClient
    from models.video_tudou import TudouVideoClient
    conn, _ = add(settings_store, kind="video", provider="tudou", models=["grok-imagine-video-1.5"], base_url="https://video.example.test", credentials={"api_key": "offline-video"})
    captured = []
    monkeypatch.setattr(TudouVideoClient, "generate_video", lambda self, **kwargs: captured.append((self.base_url, self.api_key, kwargs)) or "https://media.example.test/offline.mp4")
    result = VideoClient().generate_video("offline", None, str(tmp_path / "out.mp4"), model=model_alias(conn["id"], "grok-imagine-video-1.5"), duration=6, resolution="720P")
    assert result.endswith("offline.mp4")
    assert captured[0][0:2] == ("https://video.example.test", "offline-video")
    assert captured[0][2]["model"] == "grok-imagine-video-1.5"
    with pytest.raises(SettingsError):
        VideoClient().generate_video("offline", None, str(tmp_path / "out.mp4"), model=model_alias(conn["id"], "grok-imagine-video-1.5"), duration=16)
    assert len(captured) == 1


def test_revisions_reject_stale_save_and_keep_data(settings_store):
    revision = settings_store.read()["revision"]
    conn, values = add(settings_store)
    with pytest.raises(SettingsError, match="其他页面"):
        settings_store.save_connection(revision, {**values, "name": "过期写入"}, conn["id"])
    assert settings_store.read()["connections"][-1]["name"] == values["name"]


@pytest.mark.parametrize("url", ["https://user:secret@example.test", "https://example.test?key=secret", "http://example.test", "file:///tmp/a", "https://example.test#secret"])
def test_reject_credential_urls(settings_store, url):
    with pytest.raises(SettingsError):
        add(settings_store, base_url=url)


def test_unknown_media_model_cannot_masquerade_as_supported(settings_store):
    with pytest.raises(SettingsError, match="适配"):
        add(settings_store, kind="video", provider="tudou", models=["veo-unknown"])


def test_default_connection_and_registry_persist(settings_store):
    from models.config_model import get_models_by_type
    conn, values = add(settings_store)
    settings_store.set_default(settings_store.read()["revision"], conn["id"], "deepseek-flash")
    assert project_defaults()["llm_model"] == model_alias(conn["id"], "deepseek-flash")
    assert any(m["id"] == project_defaults()["llm_model"] for m in get_models_by_type("llm"))
    settings_store.save_connection(settings_store.read()["revision"], {**values, "enabled": False}, conn["id"])
    assert not any(m["id"] == model_alias(conn["id"], "deepseek-flash") for m in get_models_by_type("llm"))
    assert project_defaults()["llm_model"] == model_alias(conn["id"], "deepseek-flash")  # no silent fallback


@pytest.mark.parametrize("options", [{"duration": 16}, {"resolution": "4K"}, {"ratio": "123:9"}, {"duration": 6.5}])
def test_invalid_video_inputs_rejected_before_transport(options):
    with pytest.raises(SettingsError):
        validate_video_options("grok-imagine-video-1.5", **options)


def test_readonly_probe_never_posts_or_claims_generation(settings_store, monkeypatch):
    import requests
    conn, _ = add(settings_store)
    observed = []
    class Response:
        status_code = 200
        def json(self): return {"data": [{"id": "deepseek-flash"}]}
    monkeypatch.setattr(requests.Session, "get", lambda self, url, **kwargs: observed.append((url, kwargs)) or Response())
    monkeypatch.setattr(requests.Session, "post", lambda *a, **kw: pytest.fail("must not generate"))
    saved = settings_store.probe(settings_store.read()["revision"], conn["id"])
    assert saved["connections"][-1]["test"]["status"] == "available"
    assert "未调用付费" in saved["connections"][-1]["test"]["message"]
    assert observed[0][0].endswith("/v1/models") and observed[0][1]["allow_redirects"] is False
    assert ServiceSettings(settings_store.directory, settings_store.env_path).public()["connections"][-1]["test"]["status"] == "available"


def test_probe_redacts_provider_failure(settings_store, monkeypatch):
    import requests
    conn, _ = add(settings_store)
    def fail(*a, **kw): raise requests.ConnectionError("must-not-echo-secret-or-url")
    monkeypatch.setattr(requests.Session, "get", fail)
    result = settings_store.probe(settings_store.read()["revision"], conn["id"])
    assert "must-not-echo" not in json.dumps(result)
    assert result["connections"][-1]["test"]["status"] == "error"


def test_no_probe_without_credentials_or_supported_endpoint():
    assert probe_connection({"credentials": {"api_key": ""}})["status"] == "missing"
    assert probe_connection({"credentials": {"api_key": "offline"}, "provider": "ark"})["status"] == "unsupported"


def test_dashscope_vlm_passes_connection_endpoint_per_request(monkeypatch):
    from types import SimpleNamespace as NS
    from models.vlm_dashscope import QwenVLClient, MultiModalConversation
    observed = []
    response = NS(status_code=200, output=NS(choices=[NS(message=NS(content=[{"text": "offline"}]))]))
    monkeypatch.setattr(MultiModalConversation, "call", lambda **kwargs: observed.append(kwargs) or response)
    one = QwenVLClient(api_key="offline-one", base_url="https://one.example.test/api/v1")
    two = QwenVLClient(api_key="offline-two", base_url="https://two.example.test/api/v1")
    assert one.chat("offline", [], "qwen3.6-plus") == "offline"
    assert two.chat("offline", [], "qwen3.6-plus") == "offline"
    assert [(r["api_key"], r["base_address"]) for r in observed] == [("offline-one", one.base_url), ("offline-two", two.base_url)]


@pytest.fixture
async def api(settings_store, tmp_path, monkeypatch):
    from api.app import app
    from api import dependencies
    from api.routers import workflow, sessions
    engine = WorkflowEngine(session_dir=str(tmp_path / "legacy"), database_path=tmp_path / "workflow.sqlite3")
    manager = TaskManager(engine)
    for mod in (dependencies, workflow):
        monkeypatch.setattr(mod, "workflow_engine", engine)
        monkeypatch.setattr(mod, "task_manager", manager)
    monkeypatch.setattr(sessions, "workflow_engine", engine)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
        yield client, engine, manager
    await manager.shutdown()
    engine.store.close()


async def test_defaults_new_project_existing_unchanged_and_explicit_apply(api, settings_store):
    client, engine, _ = api
    async def create():
        return (await client.post("/api/projects", json={"title": "离线项目", "project_container": True}, headers={"Idempotency-Key": str(uuid4())})).json()
    first = await create()
    defaults = settings_store.read()["defaults"]
    defaults.update(style="anime", video_ratio="9:16", target_duration=90)
    response = await client.put("/api/settings/defaults", json={"revision": settings_store.read()["revision"], "defaults": defaults})
    assert response.status_code == 200, response.text
    second = await create()
    assert second["meta"]["target_duration"] == 90 and second["meta"]["style"] == "anime"
    assert engine.get_state(first["session_id"]).meta["target_duration"] == 60
    child = await client.post(f'/api/projects/{second["session_id"]}/episodes', json={}, headers={"Idempotency-Key": str(uuid4())})
    assert child.json()["meta"]["target_duration"] == 90
    applied = await client.post(f'/api/settings/apply/{first["session_id"]}', json={"revision": settings_store.read()["revision"], "expected_project_revision": 1})
    assert applied.status_code == 200, applied.text
    assert applied.json()["meta"]["settings_application"]["revision"] == settings_store.read()["revision"]
    assert (await client.post(f'/api/settings/apply/{first["session_id"]}', json={"revision": settings_store.read()["revision"], "expected_project_revision": 1})).status_code == 409
    assert (await client.get("/api/settings/storage")).status_code == 200


async def test_api_errors_never_echo_submitted_secret(api):
    client, _, _ = api
    result = await client.post("/api/settings/connections", json={"credentials": {"api_key": "secret-must-not-echo"}})
    assert result.status_code == 422
    assert set(result.json()) == {"error"} and "secret-must-not-echo" not in result.text


async def test_task_admission_freezes_config_until_completion_and_version_is_public(api, settings_store, monkeypatch):
    _, engine, manager = api
    monkeypatch.setattr(Config, "DEEPSEEK_BASE_URL", "https://original.example.test/v1")
    sid = "offline-freeze"
    engine.create_session(sid, {"llm_model": "offline"})
    observed = []
    class OfflineAgent(AgentInterface):
        async def process(self, data, intervention=None):
            # Both the default executor and nested pool must receive the fixed snapshot.
            loop = asyncio.get_running_loop()
            observed.append(await loop.run_in_executor(None, lambda: Config.DEEPSEEK_BASE_URL))
            return {"payload": SCRIPT, "requires_intervention": True}
    engine.agent_factories[WorkflowStage.SCRIPT_GENERATION] = OfflineAgent
    job = manager.submit(sid, "script_generation", {})
    monkeypatch.setattr(Config, "DEEPSEEK_BASE_URL", "https://changed.example.test/v1")
    for _ in range(100):
        if manager.get(sid, job["task_id"])["status"] in {"completed", "error"}: break
        await asyncio.sleep(.01)
    assert manager.get(sid, job["task_id"])["status"] == "completed"
    assert observed == ["https://original.example.test/v1"]
    assert job["config_version"].startswith("settings-")
    assert "attributes" not in json.dumps(engine.store.get_job(job["task_id"]))
    assert not manager._runtime

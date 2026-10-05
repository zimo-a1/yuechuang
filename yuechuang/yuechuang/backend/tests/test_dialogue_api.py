"""API-level acceptance for dialogue edits and pre-dispatch failures (offline)."""

import asyncio
from copy import deepcopy

import httpx
import pytest

from core.orchestrator import WorkflowEngine
from core.tasks import TaskManager
from tests.manual_dialogue_preview import build_preview
from tests.test_workflow_acceptance import META, artifacts


@pytest.fixture
async def api(tmp_path, monkeypatch):
    from api.app import app
    from api.routers import workflow
    engine = WorkflowEngine(session_dir=str(tmp_path / "legacy"), database_path=tmp_path / "state.sqlite3")
    engine.create_session("dialogue-api", META)
    manager = TaskManager(engine)
    monkeypatch.setattr(workflow, "workflow_engine", engine)
    monkeypatch.setattr(workflow, "task_manager", manager)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        try:
            yield client, engine
        finally:
            await manager.shutdown()
            engine.store.close()


async def test_dialogue_edit_api_rejects_overlap_without_overwriting(api):
    client, _ = api
    path = "/api/project/dialogue-api/artifact/storyboard"
    body = {"episodes": [build_preview()["episode"]]}
    saved = await client.patch(path, json=body)
    assert saved.status_code == 200, saved.text
    before = (await client.get(path)).json()
    invalid = deepcopy(body)
    beats = invalid["episodes"][0]["segments"][0]["shot_control"]["beats"]
    beats.append({**beats[0], "unit_id": "U999"})
    rejected = await client.patch(path, json=invalid)
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "SHOT_PLAN_INVALID"
    assert (await client.get(path)).json() == before


async def test_real_video_agent_rejects_local_reference_before_paid_create(api, tmp_path, monkeypatch):
    import requests
    client, engine = api
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TUDOU_REFERENCE_URL_MAP_FILE", raising=False)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: pytest.fail("no provider creation allowed"))
    image = tmp_path / "reference.png"
    from tests.test_tudou_adapters import PNG_1X1
    image.write_bytes(PNG_1X1)
    state = engine.get_state("dialogue-api")
    state.meta.update(video_generation_mode="first_frame", video_first_frame_model="grok-imagine-video-1.5")
    state.artifacts = artifacts()
    state.artifacts["storyboard"] = {"episodes": [build_preview()["episode"]]}
    state.artifacts["reference_generation"] = {"scenes": [
        {"id": seg["segment_id"], "selected": str(image)}
        for seg in state.artifacts["storyboard"]["episodes"][0]["segments"]
    ]}
    state.confirmed_stages = ["script_generation", "character_design", "storyboard", "reference_generation"]
    for stage in state.confirmed_stages:
        state.status[stage] = "completed"
    engine.save_session_to_disk(state.session_id)
    accepted = await client.post("/api/project/dialogue-api/execute/video_generation", json={}, headers={"Prefer": "respond-async"})
    assert accepted.status_code == 202, accepted.text
    for _ in range(200):
        job = (await client.get(accepted.json()["status_url"])).json()
        if job["status"] in {"error", "completed", "stopped"}:
            break
        await asyncio.sleep(0.01)
    assert job["status"] == "error", job
    assert job["error"]["code"] == "VIDEO_INPUT_INVALID"
    assert "公网" in job["error"]["message"]
    assert str(image) not in job["error"]["message"]
    assert engine.store.get_job(job["task_id"])["error"] == job["error"]

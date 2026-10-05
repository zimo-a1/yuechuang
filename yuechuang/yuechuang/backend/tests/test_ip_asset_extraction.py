"""IP episode extraction admission and durable execution, with no provider calls."""

import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest

from core.agents.script_agent import ScriptWriterAgent
from core.output_validation import ModelOutputError
from core.workflow_rules import WorkflowConflict
from tests.test_output_validation import SCRIPT
from tests.test_projects import api  # noqa: F401


STAGE = "script_generation"
KINDS = {"characters": "character_id", "settings": "setting_id", "props": "prop_id"}


async def published_episode(engine, sid="published-episode"):
    engine.create_session(sid, {
        "source_type": "ip", "parent_project_id": "ip-parent", "llm_model": "offline",
        "ip_binding": {"ip_id": "original-ip", "version_id": "original-version"},
        "novel_origin": {"project_id": "ip-parent", "episode": 1,
                         "signature": "published-snapshot", "outline_hash": "confirmed-outline"},
    })
    script = deepcopy(SCRIPT)
    script["props"] = [{"name": "怀表", "description": "保留的黄铜怀表"}]
    script["source_map"] = {"ip_id": "original-ip", "version_id": "original-version",
                            "adaptation_episode": 1, "scenes": [{"event_ids": ["original-event"]}],
                            "manually_edited": False}
    for kind, id_key in KINDS.items():
        script[kind][0][id_key] = f"existing-{kind}"
    engine.update_artifact(sid, STAGE, script)
    await engine.continue_workflow(sid, STAGE)
    engine.manage_assets(sid, {})
    state = engine.get_state(sid)
    for kind in KINDS:
        item = state.artifacts["character_design"][kind][0]
        item.update(selected=f"/offline/{kind}.png", versions=[f"/offline/{kind}.png"])
    engine.save_session_to_disk(sid)
    return state


async def terminal(manager, sid, task_id):
    for _ in range(300):
        task = manager.get(sid, task_id)
        if task["status"] in {"completed", "error", "stopped", "interrupted"}:
            return task
        await asyncio.sleep(0.01)
    pytest.fail("offline extraction did not finish")


def offline_query(monkeypatch, kind, original):
    calls = []

    async def query(self, prompt, validator, **kwargs):
        calls.append(kwargs["label"])
        assert kwargs["attempts"] == 1
        return validator([
            {"name": original[kind][0]["name"], "description": "must not replace the existing asset"},
            {"name": f"新增{kind}", "description": "仅供离线测试的外观描述"},
        ])

    monkeypatch.setattr(ScriptWriterAgent, "_query_structured", query)
    # Even constructing a configured transport is unnecessary in this test.
    monkeypatch.setattr("models.llm_client.LLM", lambda: object())
    return calls


@pytest.mark.parametrize("kind", list(KINDS))
async def test_published_ip_extraction_runs_async_and_preserves_content_identity_images(api, monkeypatch, kind):
    client, engine, manager = api
    state = await published_episode(engine)
    sid = state.session_id
    original = deepcopy(state.artifacts[STAGE])
    existing_images = deepcopy(state.artifacts["character_design"])
    calls = offline_query(monkeypatch, kind, original)
    response = await client.post(f"/api/project/{sid}/intervene", json={
        "stage": STAGE, "modifications": {"action": "extract_assets", "kind": kind,
            "session_id": sid, "style": "realistic", "llm_model": "offline",
            "video_sound": "on", "video_shot_type": "multi"},
    }, headers={"Prefer": "respond-async", "Idempotency-Key": str(uuid4())})
    assert response.status_code == 202, response.text
    task = await terminal(manager, sid, response.json()["task_id"])
    assert task["status"] == "completed", task.get("error")
    assert len(calls) == 1
    result = engine.get_state(sid).artifacts[STAGE]
    assert result["episodes"] == original["episodes"]
    assert result["source_map"] == original["source_map"]
    assert result[kind][0] == original[kind][0]
    assert len(result[kind]) == 2 and result[kind][1][KINDS[kind]]
    for other in KINDS.keys() - {kind}:
        assert result[other] == original[other]
    assert state.artifacts["character_design"] == existing_images
    assert STAGE not in state.confirmed_stages
    # Reconfirming and materializing the new metadata still preserves selected media.
    await engine.continue_workflow(sid, STAGE)
    loaded = engine.manage_assets(sid, {})["artifact"]
    for other in KINDS:
        assert loaded[other][0] == existing_images[other][0]
    assert len(loaded[kind]) == 2 and loaded[kind][1]["versions"] == []


@pytest.mark.parametrize("modifications", [
    {"modified_script": SCRIPT},
    {"action": "rewrite"},
    {"action": "extract_assets", "kind": "unknown"},
    {"action": "extract_assets", "kind": []},
    {"action": "extract_assets", "kind": "characters", "modified_script": SCRIPT},
    {"action": "extract_assets", "kind": "characters", "selected_mode": "rewrite"},
    {"action": "extract_assets", "kind": "characters", "ip_binding": None},
    {"action": "extract_assets", "kind": "characters", "source_map": {"published": True}},
    {"action": "extract_assets", "kind": "characters", "_session_artifacts": {STAGE: SCRIPT}},
])
async def test_ip_script_rewrites_and_mixed_actions_are_denied_before_a_task_exists(api, modifications):
    client, engine, manager = api
    state = await published_episode(engine)
    before = deepcopy(state.to_dict())
    response = await client.post(f"/api/project/{state.session_id}/intervene",
                                 json={"stage": STAGE, "modifications": modifications})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "IP_ANALYSIS_PENDING"
    with pytest.raises(WorkflowConflict):
        manager.submit(state.session_id, STAGE, {"ip_binding": None}, modifications)
    assert engine.store.jobs(state.session_id) == []
    assert state.to_dict() == before


@pytest.mark.parametrize("condition,code", [
    ("unpublished", "IP_ANALYSIS_PENDING"),
    ("wrong_parent", "IP_ANALYSIS_PENDING"),
    ("missing_body", "STAGE_INCOMPLETE"),
    ("blank_body", "STAGE_INCOMPLETE"),
    ("source_changed", "SOURCE_CHANGED"),
    ("container", "EPISODE_REQUIRED"),
])
async def test_unpublished_empty_changed_and_container_ip_sessions_cannot_extract(api, condition, code):
    client, engine, manager = api
    state = await published_episode(engine)
    if condition == "unpublished":
        state.meta.pop("novel_origin")
    elif condition == "wrong_parent":
        state.meta["novel_origin"]["project_id"] = "another-parent"
    elif condition == "missing_body":
        state.artifacts[STAGE]["episodes"] = []
    elif condition == "blank_body":
        state.artifacts[STAGE]["episodes"][0]["content"] = " \n\t"
    elif condition == "source_changed":
        state.meta["source_changed"] = True
    else:
        state.meta["project_container"] = True
    before = deepcopy(state.to_dict())
    modifications = {"action": "extract_assets", "kind": "characters"}
    response = await client.post(f"/api/project/{state.session_id}/intervene",
                                 json={"stage": STAGE, "modifications": modifications})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == code
    # Task admission rechecks server provenance even if a caller bypasses the router.
    with pytest.raises(WorkflowConflict) as caught:
        manager.submit(state.session_id, STAGE, {"ip_binding": None, "novel_origin": {
            "project_id": "ip-parent", "signature": "forged"}, "source_map": {"published": True}}, modifications)
    assert caught.value.code == code
    assert state.to_dict() == before and engine.store.jobs(state.session_id) == []


async def test_ip_direct_generation_stays_blocked_and_extraction_idempotence_survives_confirmation_reset(api, monkeypatch):
    client, engine, manager = api
    state = await published_episode(engine)
    sid = state.session_id
    response = await client.post(f"/api/project/{sid}/execute/{STAGE}", json={
        "action": "extract_assets", "kind": "characters", "ip_binding": None})
    assert response.status_code == 409
    with pytest.raises(WorkflowConflict):
        manager.submit(sid, STAGE, {"action": "extract_assets", "kind": "characters", "ip_binding": None})
    assert engine.store.jobs(sid) == []
    calls = offline_query(monkeypatch, "characters", deepcopy(state.artifacts[STAGE]))
    action = {"action": "extract_assets", "kind": "characters"}
    _, input_data = engine.prepare_intervention_execution(sid, STAGE, action)
    first = manager.submit(sid, STAGE, input_data, action, "same-extraction")
    assert STAGE not in state.confirmed_stages
    replay = manager.submit(sid, STAGE, input_data, action, "same-extraction")
    assert replay["task_id"] == first["task_id"]
    assert (await terminal(manager, sid, first["task_id"]))["status"] == "completed"
    assert len(calls) == 1 and len(engine.store.jobs(sid)) == 1


async def test_failed_ip_extraction_can_retry_after_submission_clears_confirmation(api, monkeypatch):
    _, engine, manager = api
    state = await published_episode(engine)
    original = deepcopy(state.artifacts)
    calls = []

    async def query(self, prompt, validator, **kwargs):
        calls.append(kwargs["label"])
        if len(calls) == 1:
            raise ModelOutputError("offline_failure")
        return validator([{"name": "新增道具", "description": "离线测试外观"}])

    monkeypatch.setattr(ScriptWriterAgent, "_query_structured", query)
    monkeypatch.setattr("models.llm_client.LLM", lambda: object())
    action = {"action": "extract_assets", "kind": "props"}
    _, values = engine.prepare_intervention_execution(state.session_id, STAGE, action)
    first = manager.submit(state.session_id, STAGE, values, action)
    assert (await terminal(manager, state.session_id, first["task_id"]))["status"] == "error"
    assert state.artifacts == original and STAGE not in state.confirmed_stages
    retried = manager.retry(state.session_id, first["task_id"], "retry-extraction")
    assert (await terminal(manager, state.session_id, retried["task_id"]))["status"] == "completed"
    assert len(calls) == 2
    assert state.artifacts[STAGE]["source_map"] == original[STAGE]["source_map"]
    assert state.artifacts[STAGE]["episodes"] == original[STAGE]["episodes"]
    assert state.artifacts["character_design"] == original["character_design"]


@pytest.mark.parametrize("exception,status,code", [
    (WorkflowConflict("TEST_CONFLICT", "请检查当前项目状态"), 409, "TEST_CONFLICT"),
    (ValueError("private input must not appear in HTTP responses"), 400, "INVALID_INPUT"),
])
async def test_intervention_preparation_errors_are_structured_not_internal_errors(api, monkeypatch, exception, status, code):
    client, engine, _ = api

    def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr(engine, "prepare_intervention_execution", fail)
    response = await client.post("/api/project/any-session/intervene", json={
        "stage": STAGE, "modifications": {"action": "extract_assets", "kind": "characters"}})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "private input" not in response.text
    assert not engine.store.jobs()

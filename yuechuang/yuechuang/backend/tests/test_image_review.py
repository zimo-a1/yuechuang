"""VLM 图片审核：失败或解析不了时记为“审核未完成”，不默认合格、不自动重画。离线假客户端。"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import image_review
from core.agents.character_agent import CharacterDesignerAgent
from core.agents.reference_agent import ReferenceGeneratorAgent

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
)


class FakeImageClient:
    def __init__(self, **_):
        self.calls = []

    def generate_image(self, **kwargs):
        self.calls.append(kwargs)
        output = Path(kwargs["save_dir"]) / f"generated-{len(self.calls)}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(PNG)
        return [str(output)]


def fake_vlm(monkeypatch, reply):
    from models import vlm_client
    queries = []

    class FakeVLM:
        def query(self, *args, **kwargs):
            queries.append(kwargs)
            if isinstance(reply, Exception):
                raise reply
            return reply

    monkeypatch.setattr(vlm_client, "VLM", FakeVLM)
    return queries


def test_parse_review_never_defaults_to_pass():
    for reply in ("看起来不错", "", None, ["{bad json"], '{"issues": ["无分数"]}', '{"score": "很好"}', '{"score": 42}'):
        result = image_review.parse_review(reply)
        assert result["review_unavailable"] is True
        assert result["is_acceptable"] is None
        assert image_review.summarize(result, "a.png")["status"] == "unavailable"
    parsed = image_review.parse_review('说明：\n```json\n{"score": "8", "hard_failures": [], "detail": {"face": "ok"}}\n```')
    assert parsed["score"] == 8 and parsed["detail"] == {"face": "ok"}
    assert image_review.summarize(parsed, "a.png")["status"] == "passed"
    failed = image_review.summarize({"score": 9, "hard_failures": ["多了一只手"]}, "a.png")
    assert failed["status"] == "failed" and failed["verified"] and "多了一只手" in failed["reason"]


def test_character_vlm_error_is_unavailable_not_pass(monkeypatch):
    fake_vlm(monkeypatch, RuntimeError("provider said key=sk-should-not-leak"))
    result = CharacterDesignerAgent()._evaluate_with_vlm("a.png", "青年", "characters", "offline")
    assert result["review_unavailable"] is True
    assert result.get("is_acceptable") is not True and result.get("score") != 5
    assert "sk-should-not-leak" not in str(result)
    summary = image_review.summarize(result, "a.png")
    assert summary == {"status": "unavailable", "verified": False, "score": None,
                       "reason": image_review.UNAVAILABLE_MESSAGE, "issues": [], "image": "a.png"}


@pytest.mark.parametrize("kind", ["character", "reference"])
def test_unreviewed_image_is_kept_without_paid_retry(kind, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    queries = fake_vlm(monkeypatch, "抱歉，无法评估这张图")
    client = FakeImageClient()
    if kind == "character":
        _, path, evaluation = CharacterDesignerAgent()._generate_one(
            client, "c1", "主角", "青年", "characters", "realistic", "人类", "offline-t2i", "offline-vlm", "s", 3)
    else:
        _, path, evaluation = ReferenceGeneratorAgent()._generate_one(
            client, "s", {"segment_id": "seg_01_01", "shots": [{"content": "站台"}]}, "站台首帧", [],
            "realistic", "offline-it2i", "offline-t2i", max_versions=3)
        assert evaluation["final_visual_prompt"] == "站台首帧"
    assert len(client.calls) == 1, "审核失败不应触发额外的付费重画"
    assert len(queries) == 1
    assert path and Path(path).is_file()
    assert evaluation["review_unavailable"] is True


def test_real_failed_review_still_regenerates(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake_vlm(monkeypatch, '{"score": 3, "hard_failures": ["脸部变形"], "issues": ["脸部变形"]}')
    client = FakeImageClient()
    _, path, evaluation = CharacterDesignerAgent()._generate_one(
        client, "c1", "主角", "青年", "characters", "realistic", "人类", "offline-t2i", "offline-vlm", "s", 2)
    assert len(client.calls) == 2
    assert path and image_review.summarize(evaluation, path)["status"] == "failed"


def test_character_stage_persists_review_and_warning(monkeypatch, tmp_path):
    from models import image_client
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(image_client, "ImageClient", FakeImageClient)
    fake_vlm(monkeypatch, "not json")
    events = []
    agent = CharacterDesignerAgent()
    agent.set_progress_callback(lambda phase, step, percent, data=None: events.append(data or {}))
    result = asyncio.run(agent.process({
        "session_id": "review", "enable_concurrency": False, "style": "realistic",
        "image_t2i_model": "gemini-3-pro-image-preview", "vlm_model": "offline",
        "characters": {"c1": {"name": "主角", "description": "青年", "species": "人类"}},
        "settings": {"s1": {"name": "车站", "description": "站台"}},
    }))
    payload = result["payload"]
    character = payload["characters"][0]
    assert character["status"] == "done" and character["selected"]
    assert character["review"]["status"] == "unavailable"
    assert character["review"]["image"] == character["selected"]
    assert {(w["code"], w["item_id"]) for w in payload["validation_warnings"]} == {
        (image_review.REVIEW_UNAVAILABLE, "c1"), (image_review.REVIEW_UNAVAILABLE, "s1")}
    done = [e for e in events if (e.get("asset_complete") or {}).get("status") == "done"]
    assert len(done) == 2 and all(e["asset_complete"]["review"]["status"] == "unavailable" for e in done)
    assert all(e["validation_warning"]["code"] == image_review.REVIEW_UNAVAILABLE for e in done)

    # 只改选择、不重画：同一张图的审核记录保留；换了图就不再挂旧记录。
    keep = asyncio.run(CharacterDesignerAgent().process({
        "session_id": "review", "image_t2i_model": "gemini-3-pro-image-preview", "vlm_model": "offline",
        "characters": payload["characters"], "settings": payload["settings"],
        "_session_artifacts": {"character_design": payload},
    }, intervention={"select_characters": {"c1": character["selected"]}}))["payload"]
    assert keep["characters"][0]["review"] == character["review"]
    other = tmp_path / "other.png"
    other.write_bytes(PNG)
    switched = asyncio.run(CharacterDesignerAgent().process({
        "session_id": "review", "image_t2i_model": "gemini-3-pro-image-preview", "vlm_model": "offline",
        "characters": payload["characters"], "settings": payload["settings"],
        "_session_artifacts": {"character_design": payload},
    }, intervention={"select_characters": {"c1": str(other)}}))["payload"]
    assert "review" not in switched["characters"][0]
    assert [w["item_id"] for w in switched["validation_warnings"]] == ["s1"]


def test_reference_stage_persists_review_and_warning(monkeypatch, tmp_path):
    from models import image_client, llm_client

    class FakeLLM:
        def __init__(self):
            self.deepseek_client = SimpleNamespace(max_attempts=5, max_tokens=20000)

        def query(self, *args, **kwargs):
            return "站台首帧"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(image_client, "ImageClient", FakeImageClient)
    monkeypatch.setattr(llm_client, "LLM", FakeLLM)
    fake_vlm(monkeypatch, RuntimeError("timeout"))
    events = []
    agent = ReferenceGeneratorAgent()
    agent.set_progress_callback(lambda phase, step, percent, data=None: events.append(data or {}))
    result = asyncio.run(agent.process({
        "session_id": "ref-review", "enable_concurrency": False, "style": "realistic",
        "llm_model": "deepseek-flash", "vlm_model": "offline",
        "image_t2i_model": "gemini-3-pro-image-preview", "image_it2i_model": "gemini-3-pro-image-preview",
        "_session_artifacts": {
            "script_generation": {"title": "审核", "original_text": ""},
            "character_design": {"characters": [], "settings": []},
            "storyboard": {"episodes": [{"episode_number": 1, "segments": [
                {"segment_id": "seg_01_01", "episode_number": 1, "segment_number": 1,
                 "location": "车站", "characters": [], "shots": [{"content": "主角走向站台"}]},
            ]}]},
        },
    }))
    scene = result["payload"]["scenes"][0]
    assert scene["status"] == "done" and scene["selected"]
    assert scene["review"]["status"] == "unavailable" and scene["review"]["image"] == scene["selected"]
    assert result["payload"]["validation_warnings"][0]["code"] == image_review.REVIEW_UNAVAILABLE
    done = [e for e in events if (e.get("asset_complete") or {}).get("status") == "done"]
    assert done[0]["asset_complete"]["review"]["status"] == "unavailable"
    assert done[0]["validation_warning"]["item_id"] == "seg_01_01"

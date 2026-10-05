import asyncio
from pathlib import Path

import pytest

from core.agents.character_agent import CharacterDesignerAgent
from core.agents.reference_agent import ReferenceGeneratorAgent
from core.agents.storyboard_agent import StoryboardAgent
from core.agents.video_agent import VideoDirectorAgent


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
)


def test_pilot_storyboard_is_one_deterministic_segment_without_llm_calls():
    result = asyncio.run(StoryboardAgent().process({
        "session_id": "pilot-storyboard",
        "pilot_mode": True,
        "llm_model": "deepseek-flash",
        "_session_artifacts": {"script_generation": {
            "characters": [{"name": "苏平川", "character_id": "c1", "description": "青年"}],
            "settings": [{"name": "车站", "setting_id": "s1", "description": "站台"}],
            "episodes": [{
                "episode_number": 1,
                "act_title": "南下",
                "content": "【车站·清晨】\n苏平川：我该出发了。",
            }],
        }},
    }))

    episodes = result["payload"]["episodes"]
    assert len(episodes) == 1
    assert len(episodes[0]["segments"]) == 1


def test_controlled_episode_storyboard_is_four_fifteen_second_shots():
    result = asyncio.run(StoryboardAgent().process({
        "session_id": "controlled-episode-storyboard",
        "controlled_episode": True,
        "llm_model": "deepseek-flash",
        "_session_artifacts": {"script_generation": {
            "characters": [{"name": "苏平川", "character_id": "c1", "description": "青年"}],
            "settings": [{"name": "车站", "setting_id": "s1", "description": "站台"}],
            "episodes": [{
                "episode_number": 1,
                "act_title": "南下",
                "content": "【车站·清晨】\n<action>苏平川拖着行李走向站台。</action>\n苏平川：我该出发了。\n<action>列车驶入，风吹动他的衣角。</action>\n苏平川：南方会有新的机会。",
            }],
        }},
    }))

    segments = result["payload"]["episodes"][0]["segments"]
    assert len(segments) == 4
    assert [segment["total_duration"] for segment in segments] == [15, 15, 15, 15]


def test_pilot_character_stage_creates_one_character_and_one_setting(monkeypatch, tmp_path):
    from models import image_client

    calls = []

    class FakeImageClient:
        def __init__(self, **_):
            pass

        def generate_image(self, **kwargs):
            calls.append(kwargs)
            output = Path(kwargs["save_dir"]) / f"image-{len(calls)}.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(PNG)
            return [str(output)]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(image_client, "ImageClient", FakeImageClient)
    result = asyncio.run(CharacterDesignerAgent().process({
        "session_id": "pilot-assets",
        "pilot_mode": True,
        "enable_concurrency": True,
        "style": "realistic",
        "image_t2i_model": "gemini-3-pro-image-preview",
        "vlm_model": "offline",
        "characters": {
            "c1": {"name": "主角", "description": "青年", "species": "人类"},
            "c2": {"name": "配角", "description": "同事", "species": "人类"},
        },
        "settings": {
            "s1": {"name": "车站", "description": "清晨站台"},
            "s2": {"name": "办公室", "description": "明亮空间"},
        },
    }))

    assert len(calls) == 2
    assert {call["resolution"] for call in calls} == {"1K"}
    characters = result["payload"]["characters"]
    settings = result["payload"]["settings"]
    assert sum(bool(item["selected"]) for item in characters) == 1
    assert sum(bool(item["selected"]) for item in settings) == 1


def test_controlled_episode_character_stage_creates_two_characters_and_two_settings(monkeypatch, tmp_path):
    from models import image_client

    calls = []

    class FakeImageClient:
        def __init__(self, **_):
            pass

        def generate_image(self, **kwargs):
            calls.append(kwargs)
            output = Path(kwargs["save_dir"]) / f"image-{len(calls)}.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(PNG)
            return [str(output)]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(image_client, "ImageClient", FakeImageClient)
    result = asyncio.run(CharacterDesignerAgent().process({
        "session_id": "controlled-episode-assets",
        "controlled_episode": True,
        "enable_concurrency": True,
        "style": "realistic",
        "image_t2i_model": "gemini-3-pro-image-preview",
        "vlm_model": "offline",
        "characters": {
            "c1": {"name": "主角", "description": "青年", "species": "人类"},
            "c2": {"name": "同伴", "description": "青年", "species": "人类"},
            "c3": {"name": "路人", "description": "路人", "species": "人类"},
        },
        "settings": {
            "s1": {"name": "车站", "description": "清晨站台"},
            "s2": {"name": "客车", "description": "车厢"},
            "s3": {"name": "街道", "description": "南方街道"},
        },
    }))

    assert len(calls) == 4
    assert {call["resolution"] for call in calls} == {"1K"}
    assert sum(bool(item["selected"]) for item in result["payload"]["characters"]) == 2
    assert sum(bool(item["selected"]) for item in result["payload"]["settings"]) == 2


def test_pilot_video_stage_creates_one_six_second_text_clip(monkeypatch, tmp_path):
    from models import video_client

    calls = []

    class FakeVideoClient:
        def generate_video(self, **kwargs):
            calls.append(kwargs)
            output = Path(kwargs["save_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"0000ftypoffline")
            return "https://media.example.test/clip.mp4"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(video_client, "VideoClient", FakeVideoClient)
    result = asyncio.run(VideoDirectorAgent().process({
        "session_id": "pilot-video",
        "pilot_mode": True,
        "enable_concurrency": True,
        "video_generation_mode": "text",
        "video_text_model": "grok-imagine-video-1.5",
        "video_ratio": "9:16",
        "video_resolution": "720P",
        "style": "realistic",
        "_session_artifacts": {
            "storyboard": {"episodes": [{"episode_number": 1, "segments": [
                {"segment_id": "seg_01_01", "episode_number": 1, "segment_number": 1,
                 "location": "车站", "characters": ["主角"], "total_duration": 18,
                 "shots": [{"duration": 8, "content": "主角走向站台"}]},
                {"segment_id": "seg_01_02", "episode_number": 1, "segment_number": 2,
                 "location": "车站", "characters": ["主角"], "total_duration": 18,
                 "shots": [{"duration": 8, "content": "列车驶入"}]},
            ]}]},
            "reference_generation": {"scenes": []},
            "character_design": {"characters": [], "settings": []},
        },
    }))

    assert len(calls) == 1
    assert calls[0]["image_path"] is None
    assert calls[0]["duration"] == 6
    clips = result["payload"]["clips"]
    assert len(clips) == 1
    assert clips[0]["selected"].endswith(".mp4")


def test_controlled_episode_video_stage_creates_four_fifteen_second_text_clips(monkeypatch, tmp_path):
    from models import video_client

    calls = []

    class FakeVideoClient:
        def generate_video(self, **kwargs):
            calls.append(kwargs)
            output = Path(kwargs["save_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"0000ftypoffline")
            return "https://media.example.test/clip.mp4"

    segments = [
        {"segment_id": f"seg_01_0{index}", "episode_number": 1, "segment_number": index,
         "location": "车站", "characters": ["主角"], "total_duration": 15,
         "shot_control": {"version": 1, "beats": [
             {"unit_id": f"U{index:03d}", "kind": "action", "start": 0.5, "end": 2.5, "text": "主角抬头"}]},
         "shots": [{"duration": 15, "content": f"第{index}个镜头"}]}
        for index in range(1, 5)
    ]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(video_client, "VideoClient", FakeVideoClient)
    result = asyncio.run(VideoDirectorAgent().process({
        "session_id": "controlled-episode-video",
        "controlled_episode": True,
        "enable_concurrency": True,
        "video_generation_mode": "text",
        "video_text_model": "grok-imagine-video-1.5",
        "video_ratio": "9:16",
        "video_resolution": "720P",
        "style": "realistic",
        "_session_artifacts": {
            "storyboard": {"episodes": [{"episode_number": 1, "segments": segments}]},
            "reference_generation": {"scenes": []},
            "character_design": {"characters": [], "settings": []},
        },
    }))

    assert len(calls) == 4
    assert {call["image_path"] for call in calls} == {None}
    assert [call["duration"] for call in calls] == [15, 15, 15, 15]
    assert len(result["payload"]["clips"]) == 4


def test_pilot_reference_stage_creates_one_prompt_and_one_unreviewed_image(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from models import image_client, llm_client

    image_calls = []
    llm_calls = []

    class FakeImageClient:
        def __init__(self, **_):
            pass

        def generate_image(self, **kwargs):
            image_calls.append(kwargs)
            output = Path(kwargs["save_dir"]) / "reference.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(PNG)
            return [str(output)]

    class FakeLLM:
        def __init__(self):
            self.deepseek_client = SimpleNamespace(max_attempts=5, max_tokens=20000)

        def query(self, *args, **kwargs):
            llm_calls.append((args, kwargs))
            return "电影感首帧，主角站在车站站台，清晨自然光"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(image_client, "ImageClient", FakeImageClient)
    monkeypatch.setattr(llm_client, "LLM", FakeLLM)
    result = asyncio.run(ReferenceGeneratorAgent().process({
        "session_id": "pilot-reference",
        "pilot_mode": True,
        "enable_concurrency": True,
        "style": "realistic",
        "video_ratio": "9:16",
        "llm_model": "deepseek-flash",
        "vlm_model": "offline",
        "image_t2i_model": "gemini-3-pro-image-preview",
        "image_it2i_model": "gemini-3-pro-image-preview",
        "_session_artifacts": {
            "script_generation": {"title": "试跑", "original_text": "少量公开开篇梗概"},
            "character_design": {"characters": [], "settings": []},
            "storyboard": {"episodes": [{"episode_number": 1, "segments": [
                {"segment_id": "seg_01_01", "episode_number": 1, "segment_number": 1,
                 "location": "车站", "characters": [], "shots": [{"content": "主角走向站台"}]},
                {"segment_id": "seg_01_02", "episode_number": 1, "segment_number": 2,
                 "location": "车站", "characters": [], "shots": [{"content": "列车驶入"}]},
            ]}]},
        },
    }))

    assert len(llm_calls) == 1
    assert len(image_calls) == 1
    assert image_calls[0]["resolution"] == "1K"
    scenes = result["payload"]["scenes"]
    assert len(scenes) == 1
    assert scenes[0]["selected"].endswith(".jpg")


def test_controlled_episode_reference_stage_creates_four_prompts_and_images(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from models import image_client, llm_client

    image_calls = []
    llm_calls = []

    class FakeImageClient:
        def __init__(self, **_):
            pass

        def generate_image(self, **kwargs):
            image_calls.append(kwargs)
            output = Path(kwargs["save_dir"]) / f"reference-{len(image_calls)}.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(PNG)
            return [str(output)]

    class FakeLLM:
        def __init__(self):
            self.deepseek_client = SimpleNamespace(max_attempts=5, max_tokens=20000)

        def query(self, *args, **kwargs):
            llm_calls.append((args, kwargs))
            return "电影感首帧，人物处在车站，清晨自然光"

    segments = [
        {"segment_id": f"seg_01_0{index}", "episode_number": 1, "segment_number": index,
         "location": "车站", "characters": [], "shots": [{"content": f"第{index}个镜头"}]}
        for index in range(1, 5)
    ]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(image_client, "ImageClient", FakeImageClient)
    monkeypatch.setattr(llm_client, "LLM", FakeLLM)
    result = asyncio.run(ReferenceGeneratorAgent().process({
        "session_id": "controlled-episode-reference",
        "controlled_episode": True,
        "enable_concurrency": True,
        "style": "realistic",
        "video_ratio": "9:16",
        "llm_model": "deepseek-flash",
        "vlm_model": "offline",
        "image_t2i_model": "gemini-3-pro-image-preview",
        "image_it2i_model": "gemini-3-pro-image-preview",
        "_session_artifacts": {
            "script_generation": {"title": "试跑", "original_text": "少量公开开篇梗概"},
            "character_design": {"characters": [], "settings": []},
            "storyboard": {"episodes": [{"episode_number": 1, "segments": segments}]},
        },
    }))

    assert len(llm_calls) == 4
    assert len(image_calls) == 4
    assert {call["resolution"] for call in image_calls} == {"1K"}
    assert len(result["payload"]["scenes"]) == 4


def test_controlled_reference_allows_one_manual_retry_for_only_failed_scene():
    artifacts = {
        "reference_generation": {
            "scenes": [
                {"id": "seg_01_01", "status": "done", "selected": "one.jpg"},
                {"id": "seg_01_02", "status": "failed", "selected": ""},
            ]
        }
    }

    ReferenceGeneratorAgent._validate_controlled_regeneration(artifacts, ["seg_01_02"])

    with pytest.raises(ValueError, match="仅允许人工确认后重试一个"):
        ReferenceGeneratorAgent._validate_controlled_regeneration(artifacts, ["seg_01_01"])
    with pytest.raises(ValueError, match="仅允许人工确认后重试一个"):
        ReferenceGeneratorAgent._validate_controlled_regeneration(artifacts, ["seg_01_02", "seg_01_01"])

import asyncio
from copy import deepcopy

import pytest

from core.agents.storyboard_agent import StoryboardAgent
from core.agents.video_agent import VideoDirectorAgent
from core.output_validation import validate_storyboard
from core.shot_control import ShotPlanError, compile_shot_control, validate_shot_control


def plan(script):
    _, units = StoryboardAgent._annotate_episode_script(
        script, [{"name": "甲"}, {"name": "乙"}], [{"name": "车站"}, {"name": "房间"}])
    return StoryboardAgent._controlled_episode_segments(1, units), units


SCRIPT = "【房间·清晨】\n人物：甲、乙\n甲：我决定去车站。\n乙：我陪你一起去。\n【车站·清晨】\n人物：甲、乙\n甲：就在这里等吧。\n乙：好，我去买票。"


def test_plan_preserves_scene_order_every_unit_and_one_speaker_per_clip():
    segments, units = plan(SCRIPT)
    assert [segment["location"] for segment in segments] == ["房间", "房间", "车站", "车站"]
    assert [beat["unit_id"] for segment in segments for beat in segment["shot_control"]["beats"]] == [unit["unit_id"] for unit in units]
    for segment in segments:
        control = validate_shot_control(segment)
        assert len({beat.speaker for beat in control.beats if beat.kind == "dialogue"}) == 1
        assert control.beats[-1].end <= 14.5
        assert segment["total_duration"] == 15
    saved = validate_storyboard([{"episode_number": 1, "segments": segments}])
    assert saved[0]["segments"][0]["shot_control"] == segments[0]["shot_control"]


@pytest.mark.parametrize("script", [
    "【车站·清晨】\n" + "\n".join("甲：" + "很长的台词" * 30 for _ in range(4)),
    "【车站·清晨】\n" + "\n".join(f"{'甲' if i % 2 else '乙'}：走吧。" for i in range(5)),
    "\n".join(f"【场景{i}·清晨】\n甲：走吧。" for i in range(5)),
])
def test_unfilmable_scripts_reject_instead_of_dropping_or_squeezing(script):
    with pytest.raises(ShotPlanError, match="不会截断"):
        plan(script)


def test_prompt_names_speaker_listener_gaze_and_says_line_only_once():
    segment = plan(SCRIPT)[0][0]
    prompt = VideoDirectorAgent()._assemble_prompt(segment, "写实")
    assert prompt.count("我决定去车站。") == 1
    assert "仅甲说一次" in prompt and "乙闭嘴倾听" in prompt
    assert "看向对方的眼睛" in prompt and "无文字纹理" in prompt


@pytest.mark.parametrize("mutation", ["overlap", "overrun", "unknown_speaker", "too_fast", "nan"])
def test_bad_edited_timeline_rejected(mutation):
    segment = plan(SCRIPT)[0][0]
    beat = segment["shot_control"]["beats"][0]
    if mutation == "overlap":
        segment["shot_control"]["beats"].append({**beat, "unit_id": "U999"})
    elif mutation == "overrun":
        beat["end"] = 20.0
    elif mutation == "unknown_speaker":
        beat["speaker"] = "陌生人"
    elif mutation == "too_fast":
        beat["end"] = 0.6
    else:
        beat["start"] = float("nan")
    with pytest.raises(ShotPlanError):
        compile_shot_control(segment)


@pytest.mark.parametrize("failure", ["last_invalid", "extra_segment", "legacy_plan"])
def test_whole_video_batch_preflight_prevents_any_create(monkeypatch, tmp_path, failure):
    from models import video_client
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(video_client, "VideoClient", lambda: pytest.fail("must not construct model client"))
    segments = plan(SCRIPT)[0]
    if failure == "last_invalid":
        segments[-1]["shot_control"]["beats"][0]["end"] = 20.0
    elif failure == "extra_segment":
        segments.append(deepcopy(segments[-1]))
    else:
        segments[-1].pop("shot_control")
    with pytest.raises(ShotPlanError):
        asyncio.run(VideoDirectorAgent().process({
            "session_id": "preflight", "controlled_episode": True,
            "video_generation_mode": "text", "video_text_model": "grok-imagine-video-1.5",
            "_session_artifacts": {"storyboard": {"episodes": [{"segments": segments}]}}
        }))


def test_edit_validates_before_mutation_and_timeline_survives_restart(tmp_path):
    from core.orchestrator import WorkflowEngine
    database = tmp_path / "state.sqlite3"
    session_dir = str(tmp_path / "sessions")
    engine = WorkflowEngine(session_dir=session_dir, database_path=database)
    try:
        engine.create_session("timeline", {})
        segments = plan(SCRIPT)[0]
        episode = {"episode_number": 1, "segments": segments}
        engine.update_artifact("timeline", "storyboard", {"episodes": [episode]})
        before = deepcopy(engine.get_state("timeline").artifacts["storyboard"])
        bad = deepcopy(episode)
        bad["segments"][0]["shot_control"]["beats"][0]["end"] = 25.0
        with pytest.raises(ShotPlanError):
            engine.update_artifact("timeline", "storyboard", {"episodes": [bad]})
        assert engine.get_state("timeline").artifacts["storyboard"] == before
    finally:
        engine.store.close()
    restored = WorkflowEngine(session_dir=session_dir, database_path=database)
    try:
        assert restored.get_state("timeline").artifacts["storyboard"] == before
    finally:
        restored.store.close()


def test_twelve_second_preview_has_no_network_and_two_distinct_speakers(monkeypatch):
    import requests
    from tests.manual_dialogue_preview import build_preview
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: pytest.fail("offline preview"))
    preview = build_preview()
    assert preview["network_requests"] == 0
    assert preview["proposed_generation"]["video_calls"] == 2
    assert sum(segment["total_duration"] for segment in preview["episode"]["segments"]) == 12
    assert [segment["shot_control"]["beats"][0]["speaker"] for segment in preview["episode"]["segments"]] == ["青年", "同伴"]

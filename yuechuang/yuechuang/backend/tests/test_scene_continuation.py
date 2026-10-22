"""Same-scene MiniMax continuation. Local files and a fake video client only."""
import asyncio
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from core.agents.video_agent import VideoDirectorAgent
from upload_validation import ffmpeg_executable


def png(path, color):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path)


def pixel(path):
    with Image.open(path) as image:
        return image.convert("RGB").getpixel((0, 0))


def two_color_clip(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg_executable(), "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=red:s=16x16:r=1:d=1",
         "-f", "lavfi", "-i", "color=c=lime:s=16x16:r=1:d=1",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
         "-frames:v", "2", str(path)],
        check=True, capture_output=True, timeout=30,
    )


def segment(segment_id, location, episode=1, scene_key=""):
    item = {
        "segment_id": segment_id,
        "episode_number": episode,
        "segment_number": 1,
        "location": location,
        "characters": ["主角"],
        "total_duration": 6,
        "shots": [{"duration": 6, "content": location}],
    }
    if scene_key:
        item["scene_key"] = scene_key
    return item


def test_continuation_rules_do_not_change_grok_or_cross_scene():
    agent = VideoDirectorAgent()
    warehouse = segment("seg_01_01", "仓库")
    same = segment("seg_01_02", "仓库")
    gate = segment("seg_01_03", "厂门口")
    next_episode = segment("seg_02_01", "仓库", episode=2)
    blank = segment("seg_01_04", "  ")
    other_key = segment("seg_01_05", "仓库", scene_key="night")
    keyed = segment("seg_01_06", "仓库", scene_key="day")
    assert agent._same_scene(warehouse, same)
    assert not agent._same_scene(warehouse, gate)
    assert not agent._same_scene(warehouse, next_episode)
    assert not agent._same_scene(warehouse, blank)
    assert not agent._same_scene(keyed, other_key)
    assert agent._output_resolution("MiniMax-H3-Max-Turbo", "start_end_frame", "720P") == "768P"
    assert agent._output_resolution("MiniMax-H3-Max-Turbo", "start_end_frame", "480P") == "480P"
    assert agent._output_resolution("MiniMax-H3-Max-Turbo", "text", "720P") == "720P"
    assert agent._output_resolution("grok-video-1.5", "start_end_frame", "720P") == "720P"
    assert not agent._scene_continuation("grok-video-1.5", "start_end_frame")


def test_grok_start_end_still_uses_the_next_reference(tmp_path):
    agent = VideoDirectorAgent()
    first, second = tmp_path / "a.png", tmp_path / "b.png"
    png(first, "red")
    png(second, "blue")
    segments = [segment("seg_01_01", "仓库"), segment("seg_01_02", "仓库")]
    scene_map = {"seg_01_01": {"id": "seg_01_01", "selected": str(first)},
                 "seg_01_02": {"id": "seg_01_02", "selected": str(second)}}
    request = agent._clip_request("s", segments, 0, segments[0], scene_map, {}, "start_end_frame", "grok-video-1.5", "720P")
    assert request["img_path"] == str(first)
    assert request["last_img_path"] == str(second)
    assert request["previous"] is None
    assert request["resolution"] == "720P"


def test_extract_keeps_the_last_frame_and_rejects_garbage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = VideoDirectorAgent()
    clip = tmp_path / "clip.mp4"
    two_color_clip(clip)
    frame = agent._extract_last_frame("session", "seg_01_01", str(clip))
    red, green, blue = pixel(frame)
    assert green > red + 40 and green > blue
    assert agent._extract_last_frame("session", "seg_01_01", str(clip)) == frame
    garbage = tmp_path / "bad.mp4"
    garbage.write_bytes(b"not a video")
    assert agent._extract_last_frame("session", "seg_01_02", str(garbage)) is None


def test_minimax_same_scene_uses_previous_last_frame_and_768p(monkeypatch, tmp_path):
    from models import video_client

    calls = []

    class FakeVideoClient:
        def generate_video(self, **kwargs):
            calls.append(kwargs)
            output = Path(kwargs["save_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            if len(calls) == 1:
                two_color_clip(output)
            else:
                output.write_bytes(b"0000ftypoffline")
            return "offline"

    refs = [tmp_path / name for name in ("warehouse-a.png", "warehouse-b.png", "gate.png")]
    for path, color in zip(refs, ("red", "blue", "white")):
        png(path, color)
    segments = [
        segment("seg_01_01", "仓库"),
        segment("seg_01_02", "仓库"),
        segment("seg_01_03", "厂门口"),
    ]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(video_client, "VideoClient", FakeVideoClient)
    asyncio.run(VideoDirectorAgent().process({
        "session_id": "continuation",
        "enable_concurrency": True,
        "video_generation_mode": "start_end_frame",
        "video_start_end_model": "MiniMax-H3-Max-Turbo",
        "video_ratio": "16:9",
        "video_resolution": "720P",
        "style": "realistic",
        "_session_artifacts": {
            "storyboard": {"episodes": [{"episode_number": 1, "segments": segments}]},
            "reference_generation": {"scenes": [
                {"id": item["segment_id"], "selected": str(path)} for item, path in zip(segments, refs)
            ]},
            "character_design": {"characters": [], "settings": []},
            "video_generation": {"clips": []},
        },
    }))

    assert [call["model"] for call in calls] == ["MiniMax-H3-Max-Turbo"] * 3
    assert {call["resolution"] for call in calls} == {"768P"}
    assert {call["video_ratio"] for call in calls} == {"16:9"}
    assert [call["duration"] for call in calls] == [6, 6, 6]
    assert calls[0]["image_path"] == str(refs[0]) and calls[0]["last_image_path"] == str(refs[0])
    # 续接段：首帧是上一段末帧，不传尾帧（2026-10-12）
    assert calls[1]["image_path"] != str(refs[1]) and calls[1]["last_image_path"] is None
    red, green, blue = pixel(calls[1]["image_path"])
    assert green > red + 40 and green > blue
    # 场景第一段、换场景的段：首尾帧照旧都用本段参考图
    assert calls[0]["image_path"] == str(refs[0]) and calls[0]["last_image_path"] == str(refs[0])
    assert calls[2]["image_path"] == str(refs[2]) and calls[2]["last_image_path"] == str(refs[2])


def run_minimax(segments, refs, fake_client, monkeypatch, tmp_path, intervention=None, clips=None, events=None):
    from models import video_client
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(video_client, "VideoClient", fake_client)
    agent = VideoDirectorAgent()
    if events is not None:
        agent.set_progress_callback(lambda phase, step, percent, data=None: events.append((step, data or {})))
    return asyncio.run(agent.process({
        "session_id": "continuation",
        "video_generation_mode": "start_end_frame",
        "video_start_end_model": "MiniMax-H3-Max-Turbo",
        "video_ratio": "16:9",
        "video_resolution": "720P",
        "style": "realistic",
        "_session_artifacts": {
            "storyboard": {"episodes": [{"episode_number": 1, "segments": segments}]},
            "reference_generation": {"scenes": [
                {"id": item["segment_id"], "selected": str(path)} for item, path in zip(segments, refs)
            ]},
            "character_design": {"characters": [], "settings": []},
            "video_generation": {"clips": clips or []},
        },
    }, intervention=intervention))


def reference_pngs(tmp_path, count):
    refs = [tmp_path / f"ref-{index}.png" for index in range(count)]
    for path in refs:
        png(path, "blue")
    return refs


def test_previous_same_scene_failure_blocks_dependent_submission(monkeypatch, tmp_path):
    calls = []

    class FirstFails:
        def generate_video(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("provider failed")
            output = Path(kwargs["save_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"0000ftypoffline")
            return "offline"

    segments = [segment("seg_01_01", "仓库"), segment("seg_01_02", "仓库"),
                segment("seg_01_03", "仓库"), segment("seg_01_04", "厂门口")]
    refs = reference_pngs(tmp_path, 4)
    events = []
    result = run_minimax(segments, refs, FirstFails, monkeypatch, tmp_path, events=events)

    # 第 1 段失败；第 2、3 段同场景续接链断了，不提交；第 4 段换场景，照常提交。
    assert len(calls) == 2
    assert calls[1]["image_path"] == str(refs[3])
    clips = {clip["id"]: clip for clip in result["payload"]["clips"]}
    assert clips["seg_01_01"]["status"] == "failed" and "generation_error" not in clips["seg_01_01"]
    for blocked, previous in (("seg_01_02", "seg_01_01"), ("seg_01_03", "seg_01_02")):
        assert clips[blocked]["status"] == "failed" and clips[blocked]["selected"] == ""
        assert clips[blocked]["generation_error"] == {
            "code": "CONTINUATION_PREVIOUS_FAILED",
            "message": "上一段生成失败，本段未提交，避免续接失效后白白扣费；可稍后重新生成",
            "previous_segment_id": previous,
        }
    assert clips["seg_01_04"]["status"] == "done"
    skipped = [data["asset_complete"] for _, data in events
               if (data.get("asset_complete") or {}).get("skipped")]
    assert [item["id"] for item in skipped] == ["seg_01_02", "seg_01_03"]
    assert all(item["status"] == "failed" and item["error"]["code"] == "CONTINUATION_PREVIOUS_FAILED" for item in skipped)
    assert ("未提交: seg_01_02", {"asset_complete": skipped[0]}) in events

    # 之后重新生成第 2 段：它不在本轮失败名单里，按原规则提交（上一段没视频时用本段参考图）。
    regen = run_minimax(segments, refs, FirstFails, monkeypatch, tmp_path,
                        intervention={"regenerate_clips": ["seg_01_02"]}, clips=result["payload"]["clips"])
    assert len(calls) == 3 and calls[2]["image_path"] == str(refs[1])
    regen_clips = {clip["id"]: clip for clip in regen["payload"]["clips"]}
    assert regen_clips["seg_01_02"]["status"] == "done" and "generation_error" not in regen_clips["seg_01_02"]
    # 没重跑的第 3 段保留上次“未提交”的原因
    assert regen_clips["seg_01_03"]["generation_error"]["previous_segment_id"] == "seg_01_02"


def test_blocked_segment_is_rejected_by_export_like_a_failed_one(monkeypatch, tmp_path):
    from core.agents.editor_agent import VideoEditorAgent
    from core.workflow_rules import WorkflowConflict

    class AlwaysFails:
        def generate_video(self, **kwargs):
            raise RuntimeError("provider failed")

    segments = [segment("seg_01_01", "仓库"), segment("seg_01_02", "仓库")]
    result = run_minimax(segments, reference_pngs(tmp_path, 2), AlwaysFails, monkeypatch, tmp_path)
    clips = result["payload"]["clips"]
    assert clips[1]["generation_error"]["code"] == "CONTINUATION_PREVIOUS_FAILED"
    with pytest.raises(WorkflowConflict) as error:
        asyncio.run(VideoEditorAgent().process({
            "session_id": "continuation",
            "_session_artifacts": {"storyboard": {"episodes": [{"episode_number": 1, "segments": segments}]},
                                   "video_generation": {"clips": clips}},
        }, intervention={"clip_ids": ["seg_01_02"]}))
    assert error.value.code == "EXPORT_INPUT_MISSING"


def test_failed_extract_falls_back_to_this_segment_reference(monkeypatch, tmp_path):
    from models import video_client

    calls = []

    class FakeVideoClient:
        def generate_video(self, **kwargs):
            calls.append(kwargs)
            output = Path(kwargs["save_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"0000ftypoffline")
            return "offline"

    own = tmp_path / "own.png"
    previous = tmp_path / "previous.png"
    png(own, "blue")
    png(previous, "red")
    segments = [segment("seg_01_01", "仓库"), segment("seg_01_02", "仓库")]
    monkeypatch.chdir(tmp_path)
    video_dir = Path("code/result/video/continuation")
    video_dir.mkdir(parents=True)
    (video_dir / "seg_01_01.mp4").write_bytes(b"not a video")
    monkeypatch.setattr(video_client, "VideoClient", FakeVideoClient)
    result = asyncio.run(VideoDirectorAgent().process({
        "session_id": "continuation",
        "video_generation_mode": "start_end_frame",
        "video_start_end_model": "MiniMax-H3-Max-Turbo",
        "video_ratio": "16:9",
        "video_resolution": "768P",
        "style": "realistic",
        "_session_artifacts": {
            "storyboard": {"episodes": [{"episode_number": 1, "segments": segments}]},
            "reference_generation": {"scenes": [
                {"id": "seg_01_01", "selected": str(previous)},
                {"id": "seg_01_02", "selected": str(own)},
            ]},
            "character_design": {},
            "video_generation": {"clips": []},
        },
    }))

    assert len(calls) == 1
    assert calls[0]["image_path"] == str(own)
    assert calls[0]["last_image_path"] == str(own)
    assert calls[0]["resolution"] == "768P"
    frames = result["payload"]["clips"][1]["frames"]
    assert frames["first_frame"] == "own_reference" and frames["end_frame"] == "own_reference"
    assert frames["previous_segment_id"] == "seg_01_01"


def test_continuation_records_frames_and_sends_first_frame_only(monkeypatch, tmp_path):
    from models import media_toapis

    calls = []

    class FakeVideoClient:
        def generate_video(self, **kwargs):
            calls.append(kwargs)
            output = Path(kwargs["save_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            two_color_clip(output)
            return "offline"

    segments = [segment("seg_01_01", "仓库"), segment("seg_01_02", "仓库")]
    refs = reference_pngs(tmp_path, 2)
    events = []
    result = run_minimax(segments, refs, FakeVideoClient, monkeypatch, tmp_path, events=events)
    clips = {clip["id"]: clip for clip in result["payload"]["clips"]}
    assert "frames" not in clips["seg_01_01"]
    frames = clips["seg_01_02"]["frames"]
    assert frames["first_frame"] == "previous_last_frame" and frames["previous_segment_id"] == "seg_01_01"
    assert frames["end_frame"] == "none" and "不传尾帧" in frames["reason"]
    assert frames["clip"] == clips["seg_01_02"]["selected"]
    done = [data["asset_complete"] for _, data in events
            if (data.get("asset_complete") or {}).get("status") == "done"]
    assert done[-1]["id"] == "seg_01_02" and done[-1]["frames"]["end_frame"] == "none"
    assert "frames" not in done[0]

    # 真实请求体（不发送）：只有一张 first_frame，没有 last_frame，校验通过
    second = calls[1]
    body = media_toapis.video_request(
        "续接", second["image_path"], "MiniMax-H3-Max-Turbo", second["duration"],
        second["video_ratio"], second["resolution"], second["last_image_path"], None)
    assert body["image_with_roles"] == [{"url": second["image_path"], "role": "first_frame"}]
    assert body["aspect_ratio"] == "adaptive" and body["resolution"] == "768P"

    # 只重新生成第 2 段：复用第 1 段已有成片的末帧，不重做第 1 段
    regen = run_minimax(segments, refs, FakeVideoClient, monkeypatch, tmp_path,
                        intervention={"regenerate_clips": ["seg_01_02"]}, clips=result["payload"]["clips"])
    assert len(calls) == 3 and calls[2]["last_image_path"] is None
    assert calls[2]["image_path"] == second["image_path"]
    regen_clips = {clip["id"]: clip for clip in regen["payload"]["clips"]}
    assert regen_clips["seg_01_01"]["versions"] == clips["seg_01_01"]["versions"]
    assert regen_clips["seg_01_02"]["selected"].endswith("seg_01_02_v2.mp4")
    assert regen_clips["seg_01_02"]["frames"]["clip"] == regen_clips["seg_01_02"]["selected"]

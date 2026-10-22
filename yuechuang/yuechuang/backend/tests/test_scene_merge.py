"""Stage 23: deterministic same-scene multi-shot segments. Offline; no model or network."""
import asyncio
import json
import math
import re
from copy import deepcopy

import pytest

from core import scene_merge
from core.agents.storyboard_agent import StoryboardAgent
from core.agents.video_agent import VideoDirectorAgent
from core.output_validation import validate_storyboard
from core.service_settings import validate_video_options

CHARACTERS = [{"name": name, "description": name} for name in ("杨飞", "林雪", "王总", "老周")]
SETTINGS = [{"name": name, "description": name} for name in ("杨家客厅", "锐创仓库", "厂门口")]

STANDARD = """**第1集-第1场 夜 内 杨家客厅**
人物：杨飞、林雪
<action>杨飞推门进屋，林雪坐在沙发上。
林雪（冷淡）：你终于回来了，我等了你一整晚。
杨飞：厂里出了事，三百万的货压在仓库里。
**第1集-第2场 日 内 锐创仓库**
人物：杨飞、王总
<action>王总把合同摔在桌上。
王总：这批货你要是吃不下，就别来找我。
杨飞：我全要，但我要先拿货后付款。
**第1集-第3场 日 外 厂门口**
老周：杨飞，你真敢接这批货？
**第1集-第4场 日 内 锐创仓库**
王总：两天，我只给你两天。
杨飞：成交。"""

NOVEL = """场 1 · 杨家客厅 · 20秒
杨飞推门进屋，看见林雪坐在沙发上。
林雪：你终于回来了，我等了你一整晚。
杨飞：厂里出了事，三百万的货压在仓库里。【改编对白】
场 2 · 锐创仓库 · 40秒【创作补充】
王总把合同摔在桌上。
王总：这批货你要是吃不下，就别来找我。"""

LONG_SCENE = "\n".join(["**第1集-第1场 日 内 锐创仓库**"] + [
    f"{'王总' if i % 2 else '杨飞'}：{'这批货今天必须全部清点完毕再装车'}{i}" for i in range(6)])


def annotate(script):
    return StoryboardAgent._annotate_episode_script(script, CHARACTERS, SETTINGS)[1]


def plans_for(script):
    return StoryboardAgent._merged_segment_plans(1, annotate(script))


def segments_for(script):
    return [StoryboardAgent._fallback_multi_design(1, plan) for plan in plans_for(script)]


def chars(unit):
    return scene_merge.spoken_chars(unit.get("dialogue") or "") if unit.get("is_dialogue") else 0


class FakeLLM:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def query(self, prompt, *args):
        self.calls.append(prompt)
        return self.responder(prompt)


def design_reply(prompt, include_dialogue=False):
    shots = json.loads(re.search(r"^shots: (.*)$", prompt, flags=re.M).group(1))
    out = []
    for shot in shots:
        content = "中景，平视侧机位，人物彼此相对。" + "".join(
            item["text"] for item in shot["items"] if item["kind"] == "action")
        if include_dialogue:
            content += "".join(item["text"] for item in shot["items"] if item["kind"] == "dialogue")
        out.append({"shot_id": shot["shot_id"], "shot_type": "中景", "content": content})
    return json.dumps({"shots": out}, ensure_ascii=False)


def responder(prompt):
    if "连续性检查员" in prompt:
        return json.dumps({"issues": [], "patches": []})
    return design_reply(prompt)


def run_storyboard(monkeypatch, script, existing=None, fake=None, target=60):
    fake = fake or FakeLLM(responder)
    monkeypatch.setattr("models.llm_client.LLM", lambda: fake)
    artifacts = {"script_generation": {"characters": CHARACTERS, "settings": SETTINGS,
                                       "episodes": [{"episode_number": 1, "act_title": "开局", "content": script}]}}
    if existing:
        artifacts["storyboard"] = existing
    result = asyncio.run(StoryboardAgent().process({
        "session_id": "merge", "llm_model": "mock", "target_duration": target,
        "_session_artifacts": artifacts}))
    return result, fake


# 1, 3: grouping and hard breaks

def test_one_segment_per_short_scene_and_hard_breaks():
    plans = plans_for(STANDARD)
    assert [(p["location"], p["scene_key"]) for p in plans] == [
        ("杨家客厅", "1-1"), ("锐创仓库", "1-2"), ("厂门口", "1-3"), ("锐创仓库", "1-4")]
    # warehouse -> gate -> warehouse is three scenes, never merged across the gate
    assert plans[1]["items"][0]["unit_id"] != plans[3]["items"][0]["unit_id"]


def test_same_location_different_scene_header_is_split():
    script = "**第1集-第1场 夜 内 锐创仓库**\n王总：今晚先把货点清楚。\n**第1集-第2场 日 内 锐创仓库**\n王总：天亮了，装车。"
    plans = plans_for(script)
    assert len(plans) == 2
    assert plans[0]["scene_key"] != plans[1]["scene_key"]


def test_across_episodes_never_share_a_segment_or_scene_key():
    first = StoryboardAgent._merged_segment_plans(1, annotate(STANDARD))
    second = StoryboardAgent._merged_segment_plans(2, annotate(STANDARD))
    assert not {p["scene_key"] for p in first} & {p["scene_key"] for p in second}


# 2: durations

def test_segment_duration_rules():
    for segment in segments_for(STANDARD) + segments_for(LONG_SCENE):
        total = segment["total_duration"]
        assert 8 <= total <= 15
        assert sum(shot["duration"] for shot in segment["shots"]) == total
        assert all(shot["duration"] >= 2 for shot in segment["shots"])
    for plan in plans_for(STANDARD) + plans_for(LONG_SCENE):
        need = math.ceil(sum(chars(u) for u in plan["items"]) / 4.5) + 2
        assert plan["total_duration"] >= min(15, need)
        body = [sum(scene_merge.unit_seconds(u) for u in group) for group in plan["shot_groups"]]
        assert plan["shot_durations"][:-1] == body[:-1]  # pause/padding only on the last shot
        assert plan["shot_durations"][-1] >= body[-1]


def test_speech_rate_is_four_and_a_half_chars_per_second_without_marks():
    assert scene_merge.dialogue_seconds("你好。") == 2
    assert scene_merge.dialogue_seconds("一二三四五六七八九十") == 3  # 10 / 4.5 -> 3
    assert scene_merge.dialogue_seconds("一二三四五六七八九十【改编对白】") == 3
    assert StoryboardAgent._duration_for_script_unit("甲：一二三四五六七八九十", True) == 3


def test_short_scene_is_padded_to_eight_seconds():
    plan = plans_for("**第1集-第1场 日 外 厂门口**\n老周：走吧。")[0]
    assert plan["total_duration"] == 8 and plan["shot_durations"] == [8]


# 4, 5: splitting long scenes

def test_long_scene_uses_fewest_even_parts_and_never_splits_a_line():
    units = annotate(LONG_SCENE)
    plans = plans_for(LONG_SCENE)
    total = sum(scene_merge.unit_seconds(u) for u in units)
    assert len(plans) == math.ceil((total + 2) / 15) or len(plans) == math.ceil((total + 2) / 15) + 1
    assert [u["unit_id"] for p in plans for u in p["items"]] == [u["unit_id"] for u in units]
    sizes = [p["speech_seconds"] for p in plans]
    assert max(sizes) - min(sizes) <= max(scene_merge.unit_seconds(u) for u in units)
    assert [p["continues_previous"] for p in plans] == [False] + [True] * (len(plans) - 1)
    assert len({p["scene_key"] for p in plans}) == 1


def test_entry_action_is_not_a_forced_break_but_a_preferred_cut():
    short = "**第1集-第1场 日 内 锐创仓库**\n杨飞：货到了。\n<action>王总走进仓库。\n王总：点清楚了吗？"
    assert len(plans_for(short)) == 1
    long = "\n".join(["**第1集-第1场 日 内 锐创仓库**",
                      "杨飞：这批货今天必须全部清点完毕再装车",
                      "林雪：我已经把单据整理好了等你签字",
                      "<action>王总走进仓库。",
                      "王总：谁让你们动我的货的马上停下",
                      "杨飞：合同在这里您自己看清楚"])
    plans = plans_for(long)
    assert len(plans) == 2
    assert plans[1]["items"][0]["is_entry"]


# 6: sub-shots

def test_two_to_four_sub_shots_and_every_unit_once_in_order():
    script = "**第1集-第1场 日 内 锐创仓库**\n" + "\n".join(
        ["<action>王总把合同摔在桌上。", "王总：签。", "王总：快签。", "杨飞：好。", "<action>杨飞签字。", "王总：行。"])
    plan = plans_for(script)[0]
    assert 2 <= len(plan["shot_groups"]) <= 4
    assert [u["unit_id"] for g in plan["shot_groups"] for u in g] == [u["unit_id"] for u in plan["items"]]
    single = plans_for("**第1集-第1场 日 外 厂门口**\n老周：走吧。")[0]
    assert len(single["shot_groups"]) == 1


# 7: too long

def test_too_long_line_is_capped_not_truncated_and_needs_review(monkeypatch):
    line = "这批货" * 25
    script = f"**第1集-第1场 日 内 锐创仓库**\n王总：{line}"
    result, _ = run_storyboard(monkeypatch, script)
    segment = result["payload"]["episodes"][0]["segments"][0]
    assert segment["total_duration"] == 15
    assert line in segment["shots"][0]["content"]
    assert result["requires_intervention"] is True
    assert "DIALOGUE_TOO_LONG" in {w["code"] for w in result["payload"]["validation_warnings"]}


# 8: novel adaptation format

def test_novel_scene_headers_actions_and_markers():
    units = annotate(NOVEL)
    assert [(u["location"], u["scene_key"], u["is_dialogue"]) for u in units] == [
        ("杨家客厅", 1, False), ("杨家客厅", 1, True), ("杨家客厅", 1, True),
        ("锐创仓库", 2, False), ("锐创仓库", 2, True)]
    assert all("【" not in u["text"] for u in units)
    prompts = [VideoDirectorAgent()._assemble_prompt(seg, "写实") for seg in segments_for(NOVEL)]
    assert all("改编对白" not in p and "创作补充" not in p for p in prompts)
    assert "杨飞推门进屋" in prompts[0] and "王总把合同摔在桌上" in prompts[1]


# 9: stage 22 continuation uses scene_key

def test_stage22_continuation_follows_scene_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    segments = segments_for(LONG_SCENE + "\n**第1集-第2场 夜 内 锐创仓库**\n王总：今晚先把货点清楚。")
    assert segments[-2]["location"] == segments[-1]["location"]
    for number, segment in enumerate(segments, 1):
        segment["segment_id"] = f"seg_01_{number:02d}"
    agent = VideoDirectorAgent()
    agent._episode_by_segment = {s["segment_id"]: 1 for s in segments}
    chained = agent._clip_request("s", segments, 1, segments[1], {}, {}, "start_end_frame", "MiniMax-H3-Max-Turbo", "720P")
    assert chained["previous"] is segments[0]
    other_scene = agent._clip_request("s", segments, len(segments) - 1, segments[-1], {}, {},
                                      "start_end_frame", "MiniMax-H3-Max-Turbo", "720P")
    assert other_scene["previous"] is None  # same location text, different scene header
    grok = agent._clip_request("s", segments, 1, segments[1], {}, {}, "first_frame", "grok-video-1.5", "720P")
    assert grok["previous"] is None and grok["resolution"] == "720P"


# 10: prompt timeline

def test_timeline_prompt_is_contiguous_and_cuts_between_shots():
    for segment in segments_for(STANDARD) + segments_for(LONG_SCENE):
        prompt = VideoDirectorAgent()._assemble_prompt(segment, "写实")
        ranges = [(float(a), float(b)) for a, b in re.findall(r"镜头\d+（([\d.]+)–([\d.]+)秒）", prompt)]
        assert ranges[0][0] == 0 and ranges[-1][1] == segment["total_duration"]
        assert all(left[1] == right[0] for left, right in zip(ranges, ranges[1:]))
        lines = [line for line in prompt.splitlines() if line.startswith("镜头")]
        assert all("：切到" in line and "：切到 " not in line for line in lines[1:])
        assert "硬切" in prompt or len(lines) == 1
        assert f"场景：{segment['location']}" in prompt
        for shot in segment["shots"]:
            for line in shot["dialogue"]:
                assert prompt.count(line["text"]) == 1


def test_cut_label_has_no_space_before_framing():
    segment = {"plan_rule": scene_merge.PLAN_RULE, "location": "仓库", "characters": ["杨飞"],
               "shots": [{"duration": 3, "content": "中景，杨飞推门。"},
                         {"duration": 3, "content": "近景，杨飞皱眉。"}]}
    section = VideoDirectorAgent()._format_shot_section(segment)
    assert "镜头2（3–6秒）：切到近景，杨飞皱眉。" in section
    assert "切到 " not in section


def test_longest_prompt_fits_minimax_limit():
    segment = segments_for(LONG_SCENE)[0]
    for shot in segment["shots"]:
        shot["content"] = shot["content"] + "镜头缓慢推进，人物表情细腻变化。" * 20
    prompt = VideoDirectorAgent()._assemble_prompt(segment, "写实" * 50, {"characters": [
        {"name": n["name"], "description": "人物外观描述" * 30} for n in CHARACTERS]})
    assert len(prompt) < 7000


def test_legacy_segment_prompt_is_unchanged_and_user_edit_wins():
    legacy = {"segment_id": "seg_01_01", "total_duration": 6, "location": "仓库", "characters": [],
              "shots": [{"shot_number": 1, "shot_type": "中景", "duration": 6, "content": "中景，仓库。"}]}
    agent = VideoDirectorAgent()
    assert agent._format_shot_section(legacy) == "分镜列表：\n分镜1：[6秒] 中景，仓库。"
    merged = segments_for(STANDARD)[0]
    edited = agent._assemble_prompt(merged, "写实", video_data={"description": "分镜列表：\n用户改过的提示词"})
    assert "用户改过的提示词" in edited and "镜头1（" not in edited


# 11: provider duration/mode preflight

@pytest.mark.parametrize("model,mode", [("grok-video-1.5", "first_frame"), ("grok-video-1.5", "reference"),
                                        ("grok-video-1.5", "text"), ("MiniMax-H3-Max-Turbo", "start_end_frame"),
                                        ("MiniMax-H3-Max-Turbo", "first_frame")])
def test_all_new_durations_pass_provider_preflight(model, mode):
    for segment in segments_for(STANDARD) + segments_for(LONG_SCENE) + segments_for(NOVEL):
        validate_video_options(model, duration=segment["total_duration"], mode=mode)


# full agent flow: LLM writes pictures only, code appends dialogue

def test_process_writes_episode_summary_and_appends_dialogue(monkeypatch):
    result, fake = run_storyboard(monkeypatch, STANDARD, target=60)
    episode = result["payload"]["episodes"][0]
    assert episode["plan_rule"] == scene_merge.PLAN_RULE
    assert episode["target_duration"] == 60
    assert episode["estimated_duration"] == sum(s["total_duration"] for s in episode["segments"])
    assert episode["segment_count"] == len(episode["segments"]) == 4
    assert not any("划分成视频生成片段" in prompt for prompt in fake.calls)
    assert len(fake.calls) == 4 + 1  # one design per segment + one continuity review
    assert result["stage_completed"] is True
    for segment in episode["segments"]:
        assert segment["plan_rule"] == scene_merge.PLAN_RULE and segment["scene_key"]
        for shot in segment["shots"]:
            for line in shot["dialogue"]:
                assert shot["content"].count(line["text"]) == 1
    validate_storyboard(result["payload"]["episodes"])


def test_design_that_repeats_dialogue_is_retried(monkeypatch):
    attempts = {"n": 0}

    def reply(prompt):
        if "连续性检查员" in prompt:
            return json.dumps({"issues": [], "patches": []})
        attempts["n"] += 1
        return design_reply(prompt, include_dialogue=attempts["n"] == 1)

    script = "**第1集-第1场 日 内 锐创仓库**\n王总：这批货你要是吃不下就别来找我。"
    result, fake = run_storyboard(monkeypatch, script, fake=FakeLLM(reply))
    assert attempts["n"] == 2
    content = result["payload"]["episodes"][0]["segments"][0]["shots"][0]["content"]
    assert content.count("这批货你要是吃不下就别来找我") == 1


def test_continuity_patch_cannot_drop_dialogue(monkeypatch):
    def reply(prompt):
        if "连续性检查员" in prompt:
            return json.dumps({"issues": ["站位"], "patches": [
                {"segment_number": 1, "shot_number": 1, "content": "中景，两人隔桌相对而立。"}]})
        return design_reply(prompt)

    script = "**第1集-第1场 日 内 锐创仓库**\n王总：这批货你要是吃不下就别来找我。"
    result, _ = run_storyboard(monkeypatch, script, fake=FakeLLM(reply))
    content = result["payload"]["episodes"][0]["segments"][0]["shots"][0]["content"]
    assert content.startswith("中景，两人隔桌相对而立") and "这批货你要是吃不下就别来找我" in content


def test_headerless_script_gets_non_blocking_note(monkeypatch):
    result, _ = run_storyboard(monkeypatch, "杨飞：货到了。\n王总：点清楚。")
    episode = result["payload"]["episodes"][0]
    assert episode["plan_notes"] and not any(s.get("scene_key") for s in episode["segments"])


# 12: old data untouched; new fields persist; estimate refreshes after edits

def test_existing_episode_is_not_resegmented(monkeypatch):
    legacy = {"episodes": [{"episode_number": 1, "segments": [{
        "segment_id": "seg_01_01", "segment_number": 1, "total_duration": 6, "location": "仓库",
        "characters": [], "shots": [{"shot_number": 1, "shot_type": "中景", "duration": 6, "content": "旧"}]}]}]}
    before = deepcopy(legacy)
    result, fake = run_storyboard(monkeypatch, STANDARD, existing=legacy)
    assert fake.calls == []
    assert result["payload"]["episodes"] == before["episodes"]


def test_new_fields_survive_restart_and_estimate_follows_edits(monkeypatch, tmp_path):
    from core.orchestrator import WorkflowEngine
    result, _ = run_storyboard(monkeypatch, STANDARD)
    episode = result["payload"]["episodes"][0]
    database, session_dir = tmp_path / "state.sqlite3", str(tmp_path / "sessions")
    engine = WorkflowEngine(session_dir=session_dir, database_path=database)
    try:
        engine.create_session("merge", {})
        engine.update_artifact("merge", "storyboard", {"episodes": [episode]})
        first = deepcopy(engine.get_state("merge").artifacts["storyboard"]["episodes"][0]["segments"][0])
        edited = deepcopy(first)
        edited["shots"][-1]["duration"] += 1
        edited["total_duration"] += 1
        engine.save_studio_segment("merge", first["segment_id"], {"expected_segment": first, "segment": edited})
        saved = engine.get_state("merge").artifacts["storyboard"]["episodes"][0]
        assert saved["estimated_duration"] == episode["estimated_duration"] + 1
        snapshot = deepcopy(engine.get_state("merge").artifacts["storyboard"])
    finally:
        engine.store.close()
    restored = WorkflowEngine(session_dir=session_dir, database_path=database)
    try:
        again = restored.get_state("merge").artifacts["storyboard"]
        assert again == snapshot
        assert again["episodes"][0]["segments"][0]["scene_key"] == "1-1"
        assert again["episodes"][0]["plan_rule"] == scene_merge.PLAN_RULE
    finally:
        restored.store.close()

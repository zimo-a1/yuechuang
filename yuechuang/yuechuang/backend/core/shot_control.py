"""Offline, versioned dialogue timing and prompt compilation. No model calls."""

import math
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ShotPlanError(ValueError):
    code = "SHOT_PLAN_INVALID"

    def __init__(self, message="分镜对白时间表无效，请检查说话人、重叠时间及片段时长"):
        self.message = message
        super().__init__(message)


def speech_seconds(text: str) -> int:
    # Conservative planning estimate, not a measured/generated audio duration.
    return max(2, math.ceil(len(re.sub(r"\s+", "", text)) / 4))


class Beat(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    unit_id: str
    kind: Literal["action", "dialogue"]
    start: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    end: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    text: Annotated[str, Field(min_length=1)]
    speaker: str = ""
    listeners: list[str] = Field(default_factory=list)
    offscreen: bool = False


class ShotControl(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    beats: Annotated[list[Beat], Field(min_length=1)]

    @model_validator(mode="after")
    def ordered(self):
        previous = 0.0
        ids = set()
        for beat in self.beats:
            if beat.start < previous or beat.end <= beat.start or beat.unit_id in ids:
                raise ValueError("invalid_timeline")
            if not beat.text.strip() or not beat.unit_id:
                raise ValueError("empty_beat")
            if beat.kind == "dialogue":
                if not beat.speaker.strip() or beat.speaker in beat.listeners:
                    raise ValueError("invalid_speaker")
                if beat.end - beat.start < speech_seconds(beat.text):
                    raise ValueError("speech_too_fast")
            elif beat.speaker or beat.offscreen:
                raise ValueError("action_has_speaker")
            previous = beat.end
            ids.add(beat.unit_id)
        return self


def validate_shot_control(segment: dict) -> ShotControl:
    try:
        control = ShotControl.model_validate(segment.get("shot_control"))
        if control.beats[-1].end > segment["total_duration"]:
            raise ValueError("duration_overflow")
        actors = set(segment.get("characters") or [])
        for beat in control.beats:
            if beat.kind == "dialogue" and beat.speaker not in actors:
                raise ValueError("unknown_speaker")
            if not set(beat.listeners) <= actors:
                raise ValueError("unknown_listener")
        return control
    except (ValidationError, ValueError, KeyError, TypeError):
        raise ShotPlanError() from None


def compile_shot_control(segment: dict) -> str:
    control = validate_shot_control(segment)
    instructions = (Path(__file__).resolve().parents[1] / "prompts/video/dialogue_control_zh.txt").read_text(encoding="utf-8").strip()
    actors = segment.get("characters") or []
    dialogues = [beat for beat in control.beats if beat.kind == "dialogue"]
    voiceover_only = bool(dialogues) and all(beat.offscreen for beat in dialogues)
    if voiceover_only:
        instructions = ("本镜头只拍摄场景空镜，没有人物出镜。严格按时间表播放指定角色的画外音，每句只说一次；"
                        "不要把画外音角色生成人像或说话的嘴，不添加其他对白。镜头运动自然平稳。"
                        "没有字幕、文字覆盖、标题、水印或片尾卡；时间表文字仅用于语音。")
    blocking = ""
    if len(actors) == 2 and not voiceover_only:
        blocking = f"{actors[0]}位于画面左侧朝右，{actors[1]}位于右侧朝左，两人看向对方的眼睛；保持这一左右关系。"
    lines = []
    for beat in control.beats:
        timing = f"{beat.start:g}–{beat.end:g}秒"
        if beat.kind == "dialogue":
            if beat.offscreen:
                lines.append(f"{timing}：仅播放{beat.speaker}的画外音一次『{beat.text}』，说话者不出镜。")
                continue
            listener = "、".join(beat.listeners)
            reaction = f"{listener}闭嘴倾听，以轻微点头和呼吸回应。" if listener else "其他人保持安静。"
            lines.append(f"{timing}：仅{beat.speaker}说一次『{beat.text}』。{reaction}")
        else:
            lines.append(f"{timing}：{beat.text}，{'无画外音' if voiceover_only else '无人说话'}。")
    if control.beats[-1].kind == "dialogue" and control.beats[-1].end < segment["total_duration"]:
        if control.beats[-1].offscreen:
            lines.append(f"{control.beats[-1].end:g}–{segment['total_duration']:g}秒：画外音结束，继续同一场景空镜和自然环境声，不出现人物或字幕。")
        else:
            lines.append(f"{control.beats[-1].end:g}–{segment['total_duration']:g}秒：对白结束，所有人闭嘴，继续看着对话对象，保持站位和朝向；仅自然呼吸和眨眼，直到最后一帧。摄影机保持不动，不转头看镜头，不改变场景或光线。")
    return f"{instructions}\n{blocking}\n对白与动作时间表（秒）：\n" + "\n".join(lines)

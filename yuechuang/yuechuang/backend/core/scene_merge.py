"""Stage 23: deterministic same-scene multi-shot segmentation. No model calls.

Rules (see docs/阶段文档/24-同场景多镜头合并.md):
- A segment never crosses episode, scene header (scene_key) or location.
- Speech is estimated at 4.5 visible characters per second; actions take 2 s;
  each segment gets a 2 s pause on its last sub-shot.
- Segments are 8-15 s; a scene that does not fit is split into the fewest,
  most even parts, cutting only between script units.
- Each segment holds 1-4 sub-shots (2-4 unless the scene has a single unit).
- A single line too long to fit is never truncated: the segment is capped at
  15 s and a warning requires human review.
"""

import itertools
import math
import re
from typing import List, Optional, Tuple

PLAN_RULE = "scene_merge_v1"
SPEECH_CHARS_PER_SECOND = 4.5
ACTION_SECONDS = 2
MIN_UNIT_SECONDS = 2
SEGMENT_PAUSE_SECONDS = 2
MIN_SEGMENT_SECONDS = 8
MAX_SEGMENT_SECONDS = 15
MAX_SUB_SHOTS = 4
MAX_PARTITION_CANDIDATES = 200_000

# Metadata labels written by core/novel_adaptation.py; never spoken, never counted.
SCRIPT_MARKERS = ("【改编对白】", "【创作补充】")
VOICEOVER_SPEAKERS = {"旁白", "独白", "画外音"}
_PUNCTUATION = re.compile(r"[\s，。！？、,.!?；;：:…—\-~～“”\"'‘’「」『』《》〈〉（）()【】\[\]·]")


def strip_markers(text: str) -> str:
    for marker in SCRIPT_MARKERS:
        text = text.replace(marker, "")
    return text.strip()


def spoken_chars(text: str) -> int:
    return len(_PUNCTUATION.sub("", strip_markers(text or "")))


def dialogue_seconds(text: str) -> int:
    """Planning estimate for one spoken line (not a measured audio length)."""
    return max(MIN_UNIT_SECONDS, math.ceil(spoken_chars(text) / SPEECH_CHARS_PER_SECOND))


def unit_seconds(unit: dict) -> int:
    if unit.get("is_dialogue"):
        return dialogue_seconds(unit.get("dialogue") or unit.get("text") or "")
    return ACTION_SECONDS


def segment_seconds(units: List[dict]) -> int:
    body = sum(unit_seconds(unit) for unit in units)
    return min(MAX_SEGMENT_SECONDS, max(MIN_SEGMENT_SECONDS, body + SEGMENT_PAUSE_SECONDS))


def overflows(units: List[dict]) -> bool:
    return sum(unit_seconds(unit) for unit in units) + SEGMENT_PAUSE_SECONDS > MAX_SEGMENT_SECONDS


def location_key(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def scene_groups(units: List[dict]) -> List[List[dict]]:
    """Consecutive units of one scene: same scene header and same location text."""
    groups: List[List[dict]] = []
    for unit in units:
        if groups:
            previous = groups[-1][-1]
            if (previous.get("scene_key") == unit.get("scene_key")
                    and location_key(previous.get("location")) == location_key(unit.get("location"))):
                groups[-1].append(unit)
                continue
        groups.append([unit])
    return groups


def _cut_penalty(previous: dict, current: dict) -> float:
    """Lower is a more natural place to start a new segment."""
    if current.get("is_entry"):
        return 0.0
    if previous.get("is_dialogue") and current.get("is_dialogue"):
        return 2.0 if previous.get("speaker") == current.get("speaker") else 0.5
    if previous.get("is_dialogue") != current.get("is_dialogue"):
        return 0.5
    return 1.0


def _part_feasible(part: List[dict]) -> bool:
    return len(part) == 1 or not overflows(part)


def partition_scene(units: List[dict]) -> List[List[dict]]:
    """Fewest segments that fit; among those, most even and best cut points."""
    n = len(units)
    if not overflows(units) or n == 1:
        return [units]
    seconds = [unit_seconds(unit) for unit in units]
    prefix = [0] + list(itertools.accumulate(seconds))
    penalties = [0.0] + [_cut_penalty(units[i - 1], units[i]) for i in range(1, n)]
    lower = math.ceil((prefix[-1] + SEGMENT_PAUSE_SECONDS) / MAX_SEGMENT_SECONDS)
    for k in range(max(2, lower), n + 1):
        if math.comb(n - 1, k - 1) > MAX_PARTITION_CANDIDATES:
            return _greedy_partition(units)
        best: Optional[Tuple[float, Tuple[int, ...]]] = None
        for cuts in itertools.combinations(range(1, n), k - 1):
            bounds = (0,) + cuts + (n,)
            parts = [units[a:b] for a, b in zip(bounds, bounds[1:])]
            if not all(_part_feasible(part) for part in parts):
                continue
            longest = max(prefix[b] - prefix[a] for a, b in zip(bounds, bounds[1:]))
            cost = longest + sum(penalties[c] for c in cuts)
            if best is None or cost < best[0]:
                best = (cost, bounds)
        if best is not None:
            bounds = best[1]
            return [units[a:b] for a, b in zip(bounds, bounds[1:])]
    return [[unit] for unit in units]


def _greedy_partition(units: List[dict]) -> List[List[dict]]:
    parts: List[List[dict]] = [[]]
    for unit in units:
        if parts[-1] and overflows(parts[-1] + [unit]):
            parts.append([])
        parts[-1].append(unit)
    return parts


def group_sub_shots(units: List[dict]) -> List[List[dict]]:
    """Merge adjacent units until at most four sub-shots remain; order is preserved."""
    groups = [[unit] for unit in units]

    def seconds(group):
        return sum(unit_seconds(unit) for unit in group)

    def category(left, right):
        a, b = left[-1], right[0]
        if a.get("is_dialogue") and b.get("is_dialogue") and a.get("speaker") == b.get("speaker"):
            return 0
        if not a.get("is_dialogue") and b.get("is_dialogue"):
            return 1
        return 2

    while len(groups) > MAX_SUB_SHOTS:
        index = min(range(len(groups) - 1),
                    key=lambda i: (category(groups[i], groups[i + 1]), seconds(groups[i]) + seconds(groups[i + 1]), i))
        groups[index:index + 2] = [groups[index] + groups[index + 1]]
    return groups


def plan_episode(units: List[dict]) -> List[dict]:
    """Return ordered segment plans; each holds its units, sub-shot groups and durations."""
    plans: List[dict] = []
    for group in scene_groups(units):
        parts = partition_scene(group)
        for part_index, part in enumerate(parts):
            shots = group_sub_shots(part)
            durations = [sum(unit_seconds(unit) for unit in shot) for shot in shots]
            total = segment_seconds(part)
            if sum(durations) > total:  # one line longer than 15 s: cap time, never text
                durations = [total]
            else:
                durations[-1] += total - sum(durations)
            plans.append({
                "items": part,
                "shot_groups": shots,
                "shot_durations": durations,
                "total_duration": total,
                "continues_previous": part_index > 0,
                "too_long": overflows(part),
                "speech_seconds": sum(unit_seconds(unit) for unit in part),
            })
    return plans


def dialogue_line(unit: dict) -> str:
    speaker = str(unit.get("speaker") or "").strip()
    text = strip_markers(str(unit.get("dialogue") or ""))
    if speaker in VOICEOVER_SPEAKERS:
        return f"画外音：「{text}」"
    return f"{speaker}说：「{text}」"


def episode_duration_summary(episode: dict) -> dict:
    segments = [seg for seg in episode.get("segments") or [] if isinstance(seg, dict)]
    total = 0
    for seg in segments:
        value = seg.get("total_duration")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += value
    return {"estimated_duration": total, "segment_count": len(segments)}


def refresh_duration_summary(storyboard: Optional[dict]) -> None:
    """Keep new-rule episode totals in step with edited segment durations."""
    if not isinstance(storyboard, dict):
        return
    for episode in storyboard.get("episodes") or []:
        if isinstance(episode, dict) and episode.get("plan_rule") == PLAN_RULE:
            episode.update(episode_duration_summary(episode))


def format_timeline(segment: dict) -> str:
    """Video prompt shot section for new-rule segments; keeps the 分镜列表 marker."""
    shots = [shot for shot in segment.get("shots") or [] if isinstance(shot, dict)]
    count = len(shots)
    if count > 1:
        header = (f"本段是同一场景里连续的 {count} 个镜头，镜头之间直接硬切，不加转场、淡入淡出或黑场，"
                  "场景、光线和人物服装保持不变。")
    else:
        header = "本段是同一场景里的一个连续镜头，不加转场、淡入淡出或黑场，场景、光线和人物服装保持不变。"
    detail = "·".join(item for item in (segment.get("scene_time"), segment.get("scene_space")) if item)
    location = str(segment.get("location") or "").strip() or "未注明"
    characters = "、".join(segment.get("characters") or []) or "无"
    lines = ["分镜列表：", header,
             f"场景：{location}{f'（{detail}）' if detail else ''}；出场人物：{characters}"]
    cursor = 0
    for index, shot in enumerate(shots, 1):
        duration = shot.get("duration") or 0
        content = str(shot.get("content") or "").strip()
        if index == 1 and segment.get("continues_previous"):
            content = f"承接上一段结尾的画面和人物站位，{content}"
        elif index > 1:
            content = f"切到{content}"
        lines.append(f"镜头{index}（{cursor:g}–{cursor + duration:g}秒）：{content}")
        cursor += duration
    return "\n".join(lines)

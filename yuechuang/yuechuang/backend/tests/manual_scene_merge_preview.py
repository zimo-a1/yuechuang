"""只读预览：同一份剧本按“旧规则”和第 24 阶段“同场景合并”切段的对比。

不写任何数据、不调用任何模型、不联网：数据库以只读方式打开，网络连接在脚本内被禁用。

用法（在 backend 目录下）：
    python tests/manual_scene_merge_preview.py --list
    python tests/manual_scene_merge_preview.py --session <session_id>
    python tests/manual_scene_merge_preview.py --session <session_id> --episode 1 --json

数据库默认取 $YC_STATE_DIR/workflow.sqlite3，否则 backend/.runtime/workflow.sqlite3；
也可以用 --db 指定。输出三部分：
  A. 当前已保存的分镜（如有）
  B. 旧兜底规则重算（每秒约 5 字、不留停顿、满 15 秒就断）
  C. 新规则（每秒 4.5 字、每段 +2 秒停顿、8–15 秒、同集同地点同场次合并）
"""

import argparse
import json
import os
import socket
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from core import scene_merge  # noqa: E402
from core.agents.storyboard_agent import StoryboardAgent  # noqa: E402
from core.agents.video_agent import VideoDirectorAgent  # noqa: E402


def _no_network(*_args, **_kwargs):
    raise RuntimeError("预览脚本禁止联网")


# Any accidental client construction fails before a request leaves the machine.
socket.socket.connect = _no_network  # type: ignore[method-assign]
socket.socket.connect_ex = _no_network  # type: ignore[method-assign]
socket.create_connection = _no_network  # type: ignore[assignment]
socket.getaddrinfo = _no_network  # type: ignore[assignment]


def default_db() -> Path:
    state_dir = os.getenv("YC_STATE_DIR")
    return Path(state_dir) / "workflow.sqlite3" if state_dir else BACKEND / ".runtime" / "workflow.sqlite3"


def read_sessions(db: Path):
    if not db.is_file():
        raise SystemExit(f"找不到数据库：{db}")
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [json.loads(row[0]) for row in connection.execute("SELECT payload FROM sessions")]
    finally:
        connection.close()


def legacy_unit_seconds(unit: dict) -> int:
    if not unit.get("is_dialogue"):
        return 2
    text = "".join((unit.get("dialogue") or unit.get("text") or "").split())
    return max(2, min(15, (len(text) + 4) // 5))


def legacy_rows(ep_n: int, units: list) -> list:
    old_units = [{**unit, "duration": legacy_unit_seconds(unit)} for unit in units]
    rows = []
    for plan in StoryboardAgent._fallback_segment_plan(ep_n, old_units):
        rows.append({"location": plan["location"], "scene": plan["items"][0].get("scene_key"),
                     "seconds": max(5, plan["total_duration"]), "shots": len(plan["items"]),
                     "units": [item["unit_id"] for item in plan["items"]]})
    return rows


def new_rows(ep_n: int, units: list) -> list:
    rows = []
    video = VideoDirectorAgent()
    for plan in StoryboardAgent._merged_segment_plans(ep_n, units):
        segment = StoryboardAgent._fallback_multi_design(ep_n, plan)
        rows.append({"location": plan["location"], "scene": plan["scene_key"], "seconds": plan["total_duration"],
                     "shots": len(plan["shot_groups"]), "shot_seconds": plan["shot_durations"],
                     "units": [item["unit_id"] for item in plan["items"]], "too_long": plan["too_long"],
                     "continues_previous": plan["continues_previous"],
                     "prompt_chars": len(video._assemble_prompt(segment, "（风格）"))})
    return rows


def saved_rows(storyboard: dict, ep_n: int) -> list:
    for episode in (storyboard or {}).get("episodes") or []:
        if episode.get("episode_number") == ep_n:
            return [{"location": seg.get("location", ""), "scene": seg.get("scene_key", ""),
                     "seconds": seg.get("total_duration"), "shots": len(seg.get("shots") or []),
                     "segment_id": seg.get("segment_id"), "plan_rule": seg.get("plan_rule", "旧规则")}
                    for seg in episode.get("segments") or []]
    return []


def summary(rows: list) -> str:
    total = sum(row["seconds"] or 0 for row in rows)
    return f"{len(rows)} 段，接缝 {max(0, len(rows) - 1)} 个，合计 {total} 秒"


def print_rows(title: str, rows: list) -> None:
    print(f"\n{title}：{summary(rows) if rows else '无'}")
    for number, row in enumerate(rows, 1):
        extra = ""
        if "shot_seconds" in row:
            extra = f" 子镜头秒数={row['shot_seconds']} 提示词约{row['prompt_chars']}字"
            extra += " 【续接上一段】" if row["continues_previous"] else ""
            extra += " 【台词过长，需人工确认】" if row["too_long"] else ""
        if "plan_rule" in row:
            extra = f" {row['segment_id']} {row['plan_rule']}"
        units = f" 单元={','.join(row['units'])}" if row.get("units") else ""
        print(f"  {number:>2}. {row['location'] or '（无地点）'} 场次={row['scene'] or '-'} "
              f"{row['seconds']}秒 镜头{row['shots']}个{units}{extra}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=default_db())
    parser.add_argument("--session")
    parser.add_argument("--episode", type=int)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    sessions = read_sessions(args.db)
    if args.list or not args.session:
        for record in sessions:
            meta = record.get("meta") or {}
            script = (record.get("artifacts") or {}).get("script_generation") or {}
            print(record.get("session_id"), meta.get("title") or script.get("title") or "", 
                  f"目标{meta.get('target_duration', '?')}秒")
        return
    record = next((item for item in sessions if item.get("session_id") == args.session), None)
    if not record:
        raise SystemExit(f"找不到 session：{args.session}")
    artifacts = record.get("artifacts") or {}
    script = artifacts.get("script_generation") or {}
    target = (record.get("meta") or {}).get("target_duration")
    report = []
    for episode in script.get("episodes") or []:
        ep_n = episode.get("episode_number", 1)
        if args.episode and ep_n != args.episode:
            continue
        _, units = StoryboardAgent._annotate_episode_script(
            episode.get("content", ""), script.get("characters", []), script.get("settings", []))
        report.append({"episode": ep_n, "target_duration": target,
                       "saved": saved_rows(artifacts.get("storyboard"), ep_n),
                       "legacy": legacy_rows(ep_n, units) if units else [],
                       "new": new_rows(ep_n, units) if units else []})
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    for item in report:
        print(f"\n===== 第 {item['episode']} 集（目标 {item['target_duration'] or '未设置'} 秒）=====")
        print_rows("A. 已保存分镜", item["saved"])
        print_rows("B. 旧兜底规则重算", item["legacy"])
        print_rows("C. 新规则", item["new"])
    print(f"\n只读完成：未写数据，未调用模型（规则 {scene_merge.PLAN_RULE}）。")


if __name__ == "__main__":
    main()

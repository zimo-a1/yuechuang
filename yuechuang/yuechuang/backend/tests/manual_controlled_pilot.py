"""Run the one-episode, cost-capped YueChuang episode against a local backend.

Dry-run is the default and makes no network calls.  ``--execute`` creates at
most one session and follows the normal confirmation-gated stages.  The source is an original synthetic scenario for testing; it contains no external novel text.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BACKEND = Path(__file__).resolve().parents[1]
STATE_PATH = BACKEND / "temp" / "controlled-pilot" / "state.json"
STAGES = [
    "script_generation",
    "character_design",
    "storyboard",
    "reference_generation",
    "video_generation",
    "post_production",
]
PUBLIC_OPENING_CONTEXT = """原创测试场景：夜班车站。
一位修理工在末班车出发前发现旅客遗失的时钟，决定追上车将物品归还。
请只生成第 1 集，聚焦不超过两位主要人物、两处场景，并按四个 15 秒镜头组织。"""
PLAN = {
    "episodes": 1,
    "target_final_duration_seconds": 60,
    "logical_shots": 4,
    "max_logical_llm_requests": 10,
    "deepseek_transport_retries_per_request": 0,
    "deepseek_output_cap_per_request_tokens": 3000,
    "gemini_images": 8,
    "vlm_evaluations": 0,
    "grok_videos": 4,
    "grok_video_duration_seconds_each": 15,
    "grok_video_duration_seconds_total": 60,
    "concurrency": 1,
    "automatic_regeneration": False,
}


def _write_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATE_PATH)


def _request(base_url: str, method: str, path: str, body: dict | None = None) -> dict:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = Request(
        base_url.rstrip("/") + path,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json", "Prefer": "respond-async"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8").strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                # Older local dev servers return the stage's NDJSON stream even
                # when Prefer: respond-async is set.  The complete response has
                # already been read in that case, so use its terminal event and
                # never submit the same stage a second time.
                events = [json.loads(line) for line in raw.splitlines() if line.strip()]
                terminal = next((event for event in reversed(events)
                                 if event.get("type") in {"stage_complete", "error"}), None)
                if terminal is None:
                    raise RuntimeError("本地后端返回了无法识别的任务响应")
                if terminal.get("type") == "error":
                    error = terminal.get("error") or {}
                    raise RuntimeError(error.get("message") or "阶段任务失败")
                return {"status": "completed", "_stream_completed": True}
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:
            detail = {}
        message = detail.get("detail") or detail.get("error", {}).get("message") or f"HTTP {exc.code}"
        raise RuntimeError(str(message)) from exc
    except URLError as exc:
        raise RuntimeError("无法连接本地后端；未创建任何生成任务") from exc


def _wait_for_task(base_url: str, status_url: str) -> dict:
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        task = _request(base_url, "GET", status_url)
        if task.get("status") in {"completed", "error", "stopped", "interrupted"}:
            return task
        time.sleep(2)
    raise RuntimeError("任务查询超时；请在网页或供应商控制台检查，禁止自动重建任务")


def _completed_stages(snapshot: dict) -> list[str]:
    statuses = snapshot.get("status", {})
    artifacts = snapshot.get("artifacts", {})
    return [stage for stage in STAGES if statuses.get(stage) == "completed" or (
        statuses.get(stage) == "waiting" and stage in artifacts
    )]


def _assert_controlled_configuration(values: dict) -> None:
    required = {
        "controlled_episode": True,
        "video_generation_mode": "text",
        "video_model": "grok-imagine-video-1.5",
        "video_text_model": "grok-imagine-video-1.5",
        "enable_concurrency": False,
    }
    mismatched = [key for key, expected in required.items() if values.get(key) != expected]
    if mismatched:
        raise RuntimeError(
            "本地后端未加载受控整集配置（" + ", ".join(mismatched) + "）；未提交任何生成阶段"
        )


def execute(base_url: str) -> int:
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        snapshot = _request(base_url, "GET", f"/api/project/{state['session_id']}/status")
        _assert_controlled_configuration(snapshot.get("meta", {}))
        completed = _completed_stages(snapshot)
        state["completed_stages"] = completed
        _write_state(state)
        print(json.dumps({"session_id": state["session_id"], "resume": True,
                          "completed_stages": completed, "plan": PLAN}, ensure_ascii=False), flush=True)
    else:
        start = _request(base_url, "POST", "/api/project/start", {
            "idea": PUBLIC_OPENING_CONTEXT,
            "style": "realistic",
            "video_ratio": "9:16",
            "video_resolution": "720P",
            "llm_model": "deepseek-flash",
            "vlm_model": "agnes-2.5-flash",
            "image_t2i_model": "gemini-3-pro-image-preview",
            "image_it2i_model": "gemini-3-pro-image-preview",
            "video_text_model": "grok-imagine-video-1.5",
            "video_model": "grok-imagine-video-1.5",
            "video_generation_mode": "text",
            "enable_concurrency": False,
            "web_search": False,
            "episodes": 1,
            "controlled_episode": True,
        })
        _assert_controlled_configuration(start.get("params", {}))
        state = {"session_id": start["session_id"], "plan": PLAN, "completed_stages": []}
        _write_state(state)
        print(json.dumps({"session_id": state["session_id"], "plan": PLAN}, ensure_ascii=False), flush=True)

    previous = None
    for stage in STAGES:
        if stage in state["completed_stages"]:
            previous = stage
            continue
        if previous:
            _request(base_url, "POST", f"/api/project/{state['session_id']}/continue", {"stage": previous})
        accepted = _request(base_url, "POST", f"/api/project/{state['session_id']}/execute/{stage}")
        task = accepted if accepted.get("_stream_completed") else _wait_for_task(base_url, accepted["status_url"])
        if task.get("status") != "completed":
            state["failed_stage"] = stage
            state["task_status"] = task.get("status")
            _write_state(state)
            raise RuntimeError(f"阶段 {stage} 未完成；停止后续生成，不自动重试")
        state["completed_stages"].append(stage)
        _write_state(state)
        print(json.dumps({"stage": stage, "status": "completed"}, ensure_ascii=False), flush=True)
        previous = stage

    _request(base_url, "POST", f"/api/project/{state['session_id']}/continue", {"stage": previous})
    print(json.dumps({"session_id": state["session_id"], "status": "completed", "plan": PLAN}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Allow the capped paid provider calls.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    args = parser.parse_args()
    print(json.dumps({"execute": args.execute, "plan": PLAN, "source": "original synthetic testing scenario"}, ensure_ascii=False))
    if not args.execute:
        return 0
    return execute(args.base_url)


if __name__ == "__main__":
    raise SystemExit(main())

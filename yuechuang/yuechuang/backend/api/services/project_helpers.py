from typing import Any, Dict

STAGE_NAME_MAP = {
    "script_generation": "剧本生成",
    "character_design": "角色/场景设计",
    "storyboard": "分镜设计",
    "reference_generation": "参考图生成",
    "video_generation": "视频生成",
    "post_production": "后期剪辑",
}


def build_openclaw_message(stage: str, result: Dict[str, Any]) -> str:
    openclaw_msg = result.get("openclaw_hint", "")
    if not openclaw_msg and result.get("requires_intervention", False):
        stage_name = STAGE_NAME_MAP.get(stage, stage)
        openclaw_msg = f"{stage_name}完成，需要用户确认。请展示给用户并等待用户确认后才能调用 /continue。"
    return openclaw_msg

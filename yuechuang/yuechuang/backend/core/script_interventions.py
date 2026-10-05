"""Admission for metadata-only extraction from an already published IP episode."""

from core.workflow_rules import WorkflowConflict, stage_ready


EXTRACTION_KINDS = ("characters", "settings", "props")
# Existing studio transport fields may accompany the action, but script payloads,
# publication provenance and alternate script actions must not cross this boundary.
EXTRACTION_FIELDS = frozenset({
    "action", "kind", "session_id", "style", "video_ratio", "video_resolution",
    "llm_model", "vlm_model", "image_t2i_model", "image_it2i_model",
    "video_generation_mode", "video_first_frame_model", "video_start_end_model",
    "video_reference_model", "video_text_model", "video_model", "video_sound",
    "video_shot_type",
})


def require_ip_script_access(state, stage, intervention=None):
    """Trust persisted publication state, never request-supplied IP/source metadata.

    Confirmation is deliberately not checked here: submitting an extraction clears
    it before the worker starts, and a failed extraction must remain retryable.
    Unpublished or stale source text never qualifies for this narrow exception.
    """
    if stage != "script_generation" or not state.meta.get("ip_binding"):
        return
    if state.meta.get("project_container"):
        raise WorkflowConflict("EPISODE_REQUIRED", "请先从小说改编生成剧集，再进入制作")

    origin = state.meta.get("novel_origin")
    parent = state.meta.get("parent_project_id")
    published = (
        isinstance(parent, str) and bool(parent)
        and isinstance(origin, dict) and origin.get("project_id") == parent
        and type(origin.get("episode")) is int and origin["episode"] >= 1
        and isinstance(origin.get("signature"), str) and bool(origin["signature"])
        and isinstance(origin.get("outline_hash"), str) and bool(origin["outline_hash"])
    )
    extraction = (
        isinstance(intervention, dict)
        and intervention.get("action") == "extract_assets"
        and intervention.get("kind") in EXTRACTION_KINDS
        and set(intervention).issubset(EXTRACTION_FIELDS)
    )
    if not published or not extraction:
        raise WorkflowConflict(
            "IP_ANALYSIS_PENDING",
            "IP 剧本请从小说改编生成；已发布的本集仅支持提取角色、场景和道具，保留正文",
        )
    if state.meta.get("source_changed"):
        raise WorkflowConflict("SOURCE_CHANGED", "原始内容已变更，请先保存并确认本集剧本，再提取素材")
    script = state.artifacts.get("script_generation") or {}
    if not stage_ready(state, "script_generation") or any(
        not isinstance(episode.get("content"), str) or not episode["content"].strip()
        for episode in script.get("episodes", [])
    ):
        raise WorkflowConflict("STAGE_INCOMPLETE", "本集剧本正文尚不完整，请先完成并确认剧本")

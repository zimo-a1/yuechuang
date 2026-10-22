# -*- coding: utf-8 -*-
"""
核心编排器 / 工作流引擎
管理六阶段状态机，协调各智能体执行，支持用户在任意阶段介入
"""

import asyncio
import copy
import uuid
import json
import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from core.agents import (
    CharacterDesignerAgent,
    ReferenceGeneratorAgent,
    ScriptWriterAgent,
    StoryboardAgent,
    VideoDirectorAgent,
    VideoEditorAgent,
)
from core.output_validation import ModelOutputError, validate_episodes, ScriptMetadata
from core.script_interventions import require_ip_script_access
from pydantic import ValidationError
from core.shot_control import ShotPlanError, validate_shot_control
from core import scene_merge
from models.video_tudou import VideoInputError
from core.quota import QuotaExceeded
from core.state_store import StateStore
from core.workflow_rules import (
    WorkflowConflict,
    invalidate_confirmations,
    require_predecessors,
    stage_ready,
)
from upload_validation import validate_upload

logger = logging.getLogger(__name__)
ARTIFACT_IMAGE_LIMIT = 25 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 1024 * 1024
SAFE_ARTIFACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class WorkflowStage(str, Enum):
    """工作流阶段"""
    INIT = "init"
    SCRIPT_GENERATION = "script_generation"
    CHARACTER_DESIGN = "character_design"
    STORYBOARD = "storyboard"
    REFERENCE_GENERATION = "reference_generation"
    VIDEO_GENERATION = "video_generation"
    POST_PRODUCTION = "post_production"
    COMPLETED = "completed"


STAGE_ORDER = [
    WorkflowStage.SCRIPT_GENERATION,
    WorkflowStage.CHARACTER_DESIGN,
    WorkflowStage.STORYBOARD,
    WorkflowStage.REFERENCE_GENERATION,
    WorkflowStage.VIDEO_GENERATION,
    WorkflowStage.POST_PRODUCTION,
]

SESSION_META_KEYS = (
    "language", "settings_revision", "settings_applied_at", "settings_application",
    "title", "source_type", "target_duration", "project_revision", "ip_binding",
    "idea",
    "user_textbox_input",
    "style",
    "video_ratio",
    "video_resolution",
    "expand_idea",
    "llm_model",
    "vlm_model",
    "image_t2i_model",
    "image_it2i_model",
    "video_model",
    "video_first_frame_model",
    "video_start_end_model",
    "video_reference_model",
    "video_text_model",
    "video_generation_mode",
    "video_style",
    "enable_concurrency",
    "web_search",
    "episodes",
    "pilot_mode",
    "controlled_episode",
)


def _normalize_meta_value(value: Any, key: str) -> Any:
    if key in {"expand_idea", "enable_concurrency", "web_search", "pilot_mode", "controlled_episode"} and isinstance(value, str):
        lower = value.lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
    return value


def _extract_session_meta(data: Dict[str, Any]) -> Dict[str, Any]:
    """Restore session-level generation params from nested or legacy flat storage."""
    meta: Dict[str, Any] = {}
    nested_meta = data.get("meta")
    if isinstance(nested_meta, dict):
        meta.update({k: _normalize_meta_value(v, k) for k, v in nested_meta.items() if v is not None})
    # Legacy session compatibility: old session JSON stored these fields at the root instead of under meta.
    for key in SESSION_META_KEYS:
        if key not in meta and key in data and data[key] is not None:
            meta[key] = _normalize_meta_value(data[key], key)
    return meta


class WorkflowState:
    """工作流状态"""

    # 阶段状态说明：
    # - pending: 阶段还没有产物，也没有在运行
    # - running: 阶段正在执行
    # - waiting: 阶段已产出内容，但需要用户介入（如选择角色、选择图片等）
    # - completed: 阶段已完成，可进入下一阶段
    # - stopped: 阶段被用户手动停止
    # - error: 阶段执行中遇到错误

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.current_stage: WorkflowStage = WorkflowStage.INIT
        self.status: Dict[str, str] = {
            stage.value: "pending" for stage in WorkflowStage if stage != WorkflowStage.INIT and stage != WorkflowStage.COMPLETED
        }
        self.artifacts: Dict[str, Any] = {}
        self.error: Optional[str] = None
        self.started_at: Optional[datetime] = None
        self.updated_at: datetime = datetime.now()
        self.meta: Dict[str, Any] = {}
        self.stage_progress: Dict[str, Dict[str, Any]] = {}
        self.confirmed_stages: List[str] = []

    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "current_stage": self.current_stage.value,
            "status": copy.deepcopy(self.status),
            "error": self.error,
            "artifacts": copy.deepcopy(self.artifacts),
            "meta": copy.deepcopy(self.meta),
            "stage_progress": copy.deepcopy(self.stage_progress),
            "confirmed_stages": list(self.confirmed_stages),
            "updated_at": self.updated_at,
        }


class WorkflowEngine:
    """工作流引擎 - 管理六阶段状态机"""

    def __init__(self, session_dir=None, database_path=None):
        self.agent_factories = {
            WorkflowStage.SCRIPT_GENERATION: ScriptWriterAgent,
            WorkflowStage.CHARACTER_DESIGN: CharacterDesignerAgent,
            WorkflowStage.STORYBOARD: StoryboardAgent,
            WorkflowStage.REFERENCE_GENERATION: ReferenceGeneratorAgent,
            WorkflowStage.VIDEO_GENERATION: VideoDirectorAgent,
            WorkflowStage.POST_PRODUCTION: VideoEditorAgent,
        }
        self.sessions: Dict[str, WorkflowState] = {}
        self._stop_events: Dict[str, threading.Event] = {}
        self._active_sessions: Set[str] = set()
        self._background_tasks: Set[Any] = set()
        self._state_lock = threading.RLock()
        self._session_dir = session_dir or os.getenv("YC_LEGACY_SESSION_DIR") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '..', 'code', 'data', 'sessions'
        )
        os.makedirs(self._session_dir, exist_ok=True)
        runtime_dir = os.getenv("YC_STATE_DIR") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.runtime'
        )
        self.store = StateStore(database_path or os.path.join(runtime_dir, 'workflow.sqlite3'))
        self._load_sessions_from_disk()

    def get_or_create_state(self, session_id: str) -> WorkflowState:
        with self._state_lock:
            if session_id not in self.sessions:
                loaded_state = self.get_state(session_id)
                if loaded_state is None:
                    self.sessions[session_id] = WorkflowState(session_id=session_id)
            if session_id not in self._stop_events:
                self._stop_events[session_id] = threading.Event()
            return self.sessions[session_id]

    def get_state(self, session_id: str) -> Optional[WorkflowState]:
        with self._state_lock:
            # 先从内存中获取
            if session_id in self.sessions:
                return self.sessions[session_id]


        return None

    def get_stop_event(self, session_id: str) -> threading.Event:
        with self._state_lock:
            if session_id not in self._stop_events:
                self._stop_events[session_id] = threading.Event()
            return self._stop_events[session_id]

    def create_session(self, session_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Create or initialize a workflow session through the engine-owned state."""
        with self._state_lock:
            if session_id in self.sessions:
                raise ValueError(f"Session already exists: {session_id}")
            state = WorkflowState(session_id=session_id)
            self.sessions[session_id] = state
            self._stop_events[session_id] = threading.Event()
            state.started_at = datetime.now()
            state.status[state.current_stage.value] = "completed"
            state.meta = copy.deepcopy(meta)
            state.updated_at = datetime.now()
            try:
                self.save_session_to_disk(session_id, meta)
            except Exception:
                self.sessions.pop(session_id, None)
                self._stop_events.pop(session_id, None)
                raise
            return {
                "session_id": session_id,
                "status": copy.deepcopy(state.status),
                "meta": copy.deepcopy(state.meta),
            }

    def create_draft(self, meta: Dict[str, Any], key: str, request_hash: str):
        """Replay a creation across requests/restarts without starting a model job."""
        session_id = uuid.uuid5(uuid.NAMESPACE_URL, f"yuechuang:project:{key}").hex
        with self._state_lock:
            state = self.get_state(session_id)
            if state:
                if state.meta.get("creation_request_hash") != request_hash:
                    raise WorkflowConflict("IDEMPOTENCY_CONFLICT", "此创建请求已用于其他内容，请重新创建")
                return {"session_id": session_id, "status": copy.deepcopy(state.status), "meta": copy.deepcopy(state.meta)}, False
            values = {**{k: v for k, v in meta.items() if v is not None}, "source_type": meta.get("source_type", "original"), "created_at": datetime.now().isoformat(),
                      "project_revision": 1, "creation_request_hash": request_hash}
            return self.create_session(session_id, values), True

    def update_draft(self, session_id: str, updates: Dict[str, Any], expected_revision: int):
        with self._state_lock:
            state = self.get_state(session_id)
            if not state:
                raise KeyError(session_id)
            self._require_idle(session_id)
            if state.meta.get("project_revision", 0) != expected_revision:
                raise WorkflowConflict("PROJECT_CHANGED", "项目已在其他页面修改，请刷新后重试")
            if state.artifacts.get("script_generation") and any(key != "title" for key in updates):
                raise WorkflowConflict("SCRIPT_EXISTS", "已有剧本，请在剧本编辑器中修改内容")
            values = copy.deepcopy(updates)
            if "idea" in values:
                values["user_textbox_input"] = values["idea"]
                attachment = state.meta.get("draft_attachment_text", "")
                values["idea"] += attachment
            previous = copy.deepcopy(state.meta)
            previous_updated = state.updated_at
            state.updated_at = datetime.now()
            state.meta.update(values, project_revision=expected_revision + 1)
            try:
                self.save_session_to_disk(session_id)
            except Exception:
                state.meta = previous
                state.updated_at = previous_updated
                raise
            return {"session_id": session_id, "status": copy.deepcopy(state.status), "meta": copy.deepcopy(state.meta)}

    def project_detail(self, project_id: str):
        with self._state_lock:
            parent = self.get_state(project_id)
            if not parent or parent.meta.get("parent_project_id"):
                raise KeyError(project_id)
            records = self.list_saved_sessions()
            episodes = [item for item in records if item["meta"].get("parent_project_id") == project_id]
            if not parent.meta.get("project_container"):
                episodes.append(next(item for item in records if item["id"] == project_id))
            episodes.sort(key=lambda item: item["meta"].get("episode_number", 1))
            assets = []
            for item in episodes:
                artifact = self.sessions[item["id"]].artifacts.get("character_design") or {}
                for kind in ("characters", "settings", "props"):
                    for asset in artifact.get(kind, []):
                        assets.append({**copy.deepcopy(asset), "kind": kind, "session_id": item["id"],
                                       "episode_number": item["meta"].get("episode_number", 1)})
            return {"project": parent.to_dict(), "episodes": episodes, "assets": assets}

    def create_episode(self, project_id: str, values: Dict[str, Any], key: str, request_hash: str):
        with self._state_lock:
            detail = self.project_detail(project_id)
            self._require_idle(project_id)
            sid = uuid.uuid5(uuid.NAMESPACE_URL, f"yuechuang:project:{key}").hex
            existing = self.get_state(sid)
            if existing:
                if existing.meta.get("parent_project_id") != project_id:
                    raise WorkflowConflict("IDEMPOTENCY_CONFLICT", "创建请求已用于其他项目")
                return self.create_draft({}, key, request_hash)
            parent = detail["project"]["meta"]
            number = max([ep["meta"].get("episode_number", 1) for ep in detail["episodes"]] or [0]) + 1
            # Only generation defaults cross the project boundary, never content or prior artifacts.
            meta = {k: v for k, v in parent.items() if k in SESSION_META_KEYS and k not in
                    {"title", "idea", "user_textbox_input", "project_revision", "source_type", "episodes"}}
            meta.update(values, source_type=parent.get("source_type", "original"), parent_project_id=project_id, project_title=parent.get("title", "项目"),
                        episode_number=number, title=values.get("title", "").strip() or f"第{number}集",
                        idea="", user_textbox_input="", episodes=1, expand_idea=True, enable_concurrency=False)
            return self.create_draft(meta, key, request_hash)

    def update_source(self, session_id: str, content: str, expected_revision: int):
        with self._state_lock:
            state = self.get_state(session_id)
            if not state:
                raise KeyError(session_id)
            self._require_idle(session_id)
            if state.meta.get("project_revision", 0) != expected_revision:
                raise WorkflowConflict("PROJECT_CHANGED", "内容已在其他页面修改，请刷新后重试")
            previous = copy.deepcopy(state.__dict__)
            try:
                changed = content != state.meta.get("idea", "")
                state.meta.update(idea=content, user_textbox_input=content, draft_attachment_text="",
                                  project_revision=expected_revision + 1)
                if changed:
                    invalidate_confirmations(state, "script_generation")
                    state.meta["source_changed"] = bool(state.artifacts.get("script_generation"))
                state.updated_at = datetime.now()
                self._recalculate_all_statuses(state)
                self.save_session_to_disk(session_id)
            except Exception:
                state.__dict__.clear(); state.__dict__.update(previous)
                raise
            return {"session_id": session_id, "status": copy.deepcopy(state.status), "meta": copy.deepcopy(state.meta)}

    def save_script_content(self, session_id: str, content: str, expected_revision: int, use_original=False):
        with self._state_lock:
            state = self.get_state(session_id)
            if not state:
                raise KeyError(session_id)
            if state.meta.get("project_revision", 0) != expected_revision:
                raise WorkflowConflict("PROJECT_CHANGED", "内容已在其他页面修改，请刷新后重试")
            if use_original:
                content = state.meta.get("idea", "")
            if not content.strip():
                raise WorkflowConflict("EMPTY_SCRIPT", "请先填写原始内容或剧本")
            current = state.artifacts.get("script_generation") or {}
            episodes = current.get("episodes") or []
            if len(episodes) > 1:
                raise WorkflowConflict("MULTI_EPISODE_SCRIPT", "历史多集剧本请使用结构编辑，避免覆盖其他集")
            episode = {**(episodes[0] if episodes else {}), "episode_number": 1,
                       "act_title": (episodes[0].get("act_title") if episodes else None) or state.meta.get("title", "第一集"), "content": content}
            result = self.update_artifact(session_id, "script_generation", {
                **current, "title": current.get("title") or state.meta.get("title", "剧本"),
                "characters": current.get("characters", []), "settings": current.get("settings", []), "episodes": [episode]})
            return {**result, "meta": copy.deepcopy(state.meta)}

    def save_studio_segment(self, session_id: str, segment_id: str, values: Dict[str, Any]):
        """Atomically save one segment; stale tabs cannot overwrite another edit."""
        with self._state_lock:
            state = self.get_state(session_id)
            if not state:
                raise KeyError(session_id)
            self._require_idle(session_id)
            artifact = copy.deepcopy(state.artifacts.get("storyboard") or {})
            target = next((seg for ep in artifact.get("episodes", []) for seg in ep.get("segments", [])
                           if seg.get("segment_id") == segment_id), None)
            if target is None:
                raise WorkflowConflict("SEGMENT_NOT_FOUND", "分镜不存在，请刷新列表")
            if target != values["expected_segment"]:
                raise WorkflowConflict("SEGMENT_CHANGED", "这个分镜已在其他页面更新。当前输入已保留，请刷新后核对再保存")
            candidate = values["segment"]
            if candidate.get("segment_id") != segment_id:
                raise WorkflowConflict("SEGMENT_INVALID", "不能修改分镜标识")
            editable = {"location", "characters", "shots", "total_duration", "shot_control", "reference_asset_ids", "video_prompt", "visual_prompt"}
            target.update({key: copy.deepcopy(value) for key, value in candidate.items() if key in editable})
            shots = target.get("shots")
            duration = target.get("total_duration")
            if (not isinstance(duration, int) or isinstance(duration, bool) or not 1 <= duration <= 600
                    or not isinstance(shots, list) or not shots
                    or any(not isinstance(shot, dict) or not isinstance(shot.get("duration"), int)
                           or isinstance(shot.get("duration"), bool) or shot["duration"] <= 0
                           or not str(shot.get("content") or shot.get("plot") or "").strip() for shot in shots)
                    or sum(shot["duration"] for shot in shots) != duration):
                raise WorkflowConflict("SEGMENT_INVALID", "请填写镜头内容；镜头时长之和须等于片段总时长")
            if (not isinstance(target.get("characters", []), list)
                    or any(not isinstance(name, str) for name in target.get("characters", []))
                    or not isinstance(target.get("location", ""), str)
                    or any(not isinstance(target.get(key, ""), str) or len(target.get(key, "")) > 20000
                           for key in ("video_prompt", "visual_prompt"))):
                raise WorkflowConflict("SEGMENT_INVALID", "人物、场景或提示词格式无效")
            bindings = target.get("reference_asset_ids")
            if bindings is not None:
                available = {asset.get("id") for kind in ("characters", "settings", "props")
                             for asset in (state.artifacts.get("character_design") or {}).get(kind, []) if asset.get("selected")}
                if (not isinstance(bindings, list) or len(bindings) > 10 or any(not isinstance(key, str) for key in bindings)
                        or len(set(bindings)) != len(bindings) or any(key not in available for key in bindings)):
                    raise WorkflowConflict("REFERENCE_BINDING_INVALID", "最多绑定 10 张本集已有图片；请先制作或导入素材")
            return self.update_artifact(session_id, "storyboard", artifact)

    def import_studio_asset(self, session_id: str, values: Dict[str, Any]):
        """Reuse an existing project asset without a model request or client-supplied path."""
        with self._state_lock:
            state = self.get_state(session_id)
            source = self.get_state(values["source_session_id"])
            if not state or not source:
                raise KeyError(session_id)
            self._require_idle(session_id)
            require_predecessors(state, "character_design")
            root = state.meta.get("parent_project_id") or session_id
            if (source.meta.get("parent_project_id") or source.session_id) != root:
                raise WorkflowConflict("ASSET_PROJECT_MISMATCH", "只能导入同一项目内的素材")
            kind = values["kind"]
            asset = next((item for item in (source.artifacts.get("character_design") or {}).get(kind, [])
                          if item.get("id") == values["asset_id"]), None)
            if not asset or not asset.get("selected"):
                raise WorkflowConflict("ASSET_NOT_READY", "来源素材尚未选择图片")
            imported_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{source.session_id}:{kind}:{asset['id']}").hex
            current = (state.artifacts.get("character_design") or {}).get(kind, [])
            if any(item.get("id") == imported_id for item in current):
                return {"artifact": copy.deepcopy(state.artifacts["character_design"]), "status_map": copy.deepcopy(state.status)}
            previous = copy.deepcopy(state.__dict__)
            try:
                item = {**copy.deepcopy(asset), "id": imported_id,
                        "origin_session_id": source.session_id, "origin_asset_id": asset["id"]}
                state.artifacts.setdefault("character_design", {}).setdefault(kind, []).append(item)
                id_key = {"characters": "character_id", "settings": "setting_id", "props": "prop_id"}[kind]
                state.artifacts.setdefault("script_generation", {}).setdefault(kind, []).append({
                    id_key: imported_id, "name": asset["name"], "description": asset["description"]})
                invalidate_confirmations(state, "character_design")
                state.error = None
                self._recalculate_all_statuses(state)
                state.updated_at = datetime.now()
                self.save_session_to_disk(session_id)
            except Exception:
                state.__dict__.clear(); state.__dict__.update(previous)
                raise
            return {"artifact": copy.deepcopy(state.artifacts["character_design"]), "status_map": copy.deepcopy(state.status)}

    def manage_assets(self, session_id: str, values: Dict[str, Any]):
        """Materialize script metadata or edit one asset without generating media."""
        with self._state_lock:
            state = self.get_state(session_id)
            if not state:
                raise KeyError(session_id)
            self._require_idle(session_id)
            require_predecessors(state, "character_design")
            previous = copy.deepcopy(state.__dict__)
            try:
                script = state.artifacts.get("script_generation") or {}
                artifact = state.artifacts.setdefault("character_design", {})
                for kind, id_key in (("characters", "character_id"), ("settings", "setting_id"), ("props", "prop_id")):
                    items = artifact.setdefault(kind, [])
                    for source in script.get(kind, []):
                        source[id_key] = source.get(id_key) or source.get("id") or uuid.uuid4().hex
                        existing = next((item for item in items if item["id"] == source[id_key]), None)
                        if not existing:
                            items.append({"id": source[id_key], "name": source["name"], "description": source["description"],
                                          "selected": "", "versions": []})
                if values.get("kind"):
                    kind = values["kind"]
                    id_key = {"characters": "character_id", "settings": "setting_id", "props": "prop_id"}[kind]
                    item_id = values.get("id") or uuid.uuid4().hex
                    item = next((it for it in artifact[kind] if it["id"] == item_id), None)
                    if values.get("id") and not item:
                        raise WorkflowConflict("ASSET_NOT_FOUND", "素材不存在，请刷新")
                    if not item:
                        item = {"id": item_id, "selected": "", "versions": []}
                        artifact[kind].append(item)
                    if values.get("delete"):
                        artifact[kind] = [it for it in artifact[kind] if it["id"] != item_id]
                        script[kind] = [it for it in script.get(kind, []) if (it.get(id_key) or it.get("id")) != item_id]
                    else:
                        for key in ("name", "description"):
                            if values.get(key) is not None:
                                item[key] = values[key]
                        if not item.get("name") or not item.get("description"):
                            raise WorkflowConflict("ASSET_INVALID", "请填写素材名称和描述")
                        if values.get("selected") is not None:
                            if values["selected"] not in item.get("versions", []):
                                raise WorkflowConflict("ASSET_VERSION_INVALID", "请选择已有图片版本")
                            item["selected"] = values["selected"]
                        source = next((it for it in script.setdefault(kind, []) if (it.get(id_key) or it.get("id")) == item_id), None)
                        if source is None:
                            source = {id_key: item_id}; script[kind].append(source)
                        source.update(name=item["name"], description=item["description"])
                invalidate_confirmations(state, "character_design")
                state.error = None
                state.status["character_design"] = "waiting"
                state.updated_at = datetime.now()
                self._recalculate_all_statuses(state)
                self.save_session_to_disk(session_id)
            except Exception:
                state.__dict__.clear(); state.__dict__.update(previous)
                raise
            return {"artifact": copy.deepcopy(artifact), "status_map": copy.deepcopy(state.status)}

    def get_status_snapshot(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return a deep-copied session snapshot from the unified in-memory state."""
        with self._state_lock:
            state = self.get_state(session_id)
            return state.to_dict() if state else None

    def get_artifact_snapshot(self, session_id: str, stage: str) -> Optional[Any]:
        """Return a deep-copied artifact snapshot from the unified in-memory state."""
        with self._state_lock:
            state = self.get_state(session_id)
            if not state:
                raise KeyError(f"Session not found: {session_id}")
            artifact = state.artifacts.get(stage)
            return copy.deepcopy(artifact) if artifact is not None else None

    def update_session_meta(self, session_id: str, updates: Dict[str, Any], allowed_keys: tuple[str, ...]) -> Dict[str, Any]:
        """Update session-level generation settings through the engine-owned meta store."""
        with self._state_lock:
            state = self.get_state(session_id)
            if not state:
                raise KeyError(f"Session not found: {session_id}")
            if not state.meta:
                state.meta = {}
            for key in allowed_keys:
                if key in updates:
                    state.meta[key] = updates[key]
            state.updated_at = datetime.now()
            self.save_session_to_disk(session_id)
            return {"status": "ok", "meta": copy.deepcopy(state.meta)}

    def apply_creation_defaults(self, session_id, values, expected_revision):
        with self._state_lock:
            state = self.get_state(session_id)
            if not state:
                raise KeyError(session_id)
            self._require_idle(session_id)
            if state.meta.get("project_revision", 0) != expected_revision:
                raise WorkflowConflict("PROJECT_CHANGED", "项目已更新，请刷新后重试")
            previous = copy.deepcopy(state)
            changed = [key for key, value in values.items() if state.meta.get(key) != value]
            state.meta.update(values, project_revision=expected_revision + 1,
                              settings_applied_at=datetime.now().isoformat(),
                              settings_application={"revision": values["settings_revision"], "changed_fields": changed})
            invalidate_confirmations(state, "script_generation")
            state.updated_at = datetime.now()
            try:
                self.save_session_to_disk(session_id)
            except Exception:
                self.sessions[session_id] = previous
                raise
            return {"status": "ok", "meta": copy.deepcopy(state.meta), "changed_fields": changed}

    def prepare_stage_execution(self, session_id: str, stage: str, body: Dict[str, Any]) -> tuple[WorkflowState, Dict[str, Any]]:
        """Build stage input from current meta/artifacts without exposing mutable state to routers."""
        with self._state_lock:
            state = self.get_or_create_state(session_id)
            if state.meta.get("project_container"):
                raise WorkflowConflict("EPISODE_REQUIRED", "请先添加剧集，再进入制作")
            if stage == "script_generation" and state.meta.get("ip_binding"):
                raise WorkflowConflict("IP_ANALYSIS_PENDING", "请返回项目的小说改编，完成长篇分析、确认方向和大纲后生成选定一集，再进入制作")
            input_data = copy.deepcopy(body) if isinstance(body, dict) else {}
            input_data["session_id"] = session_id

            for key, value in copy.deepcopy(state.meta).items():
                if value is not None and (key not in input_data or not input_data[key]):
                    input_data[key] = value
            self._inject_user_selections(copy.deepcopy(state.artifacts), stage, input_data)
            return state, input_data

    def prepare_intervention_execution(
        self,
        session_id: str,
        stage: str,
        modifications: Dict[str, Any],
    ) -> tuple[WorkflowState, Dict[str, Any]]:
        """Build intervention input from the latest artifact/meta snapshot."""
        with self._state_lock:
            state = self.get_state(session_id)
            if not state:
                raise KeyError(f"Session not found: {session_id}")

            require_ip_script_access(state, stage, modifications)
            current_artifact = copy.deepcopy(state.artifacts.get(stage, {}))
            input_data = current_artifact if isinstance(current_artifact, dict) else {}
            input_data["session_id"] = session_id
            for key, value in copy.deepcopy(state.meta).items():
                if value is not None and key not in input_data:
                    input_data[key] = value
            self._inject_user_selections(copy.deepcopy(state.artifacts), stage, input_data)
            input_data.update(modifications or {})
            return state, input_data

    @staticmethod
    def _inject_user_selections(artifacts: Dict[str, Any], stage: str, data: Dict[str, Any]):
        """Inject persisted user choices into downstream stage input."""
        if stage == 'video_generation' and 'selected_images' not in data:
            ref_art = artifacts.get('reference_generation', {})
            if isinstance(ref_art, dict):
                scenes = ref_art.get('scenes', [])
                selected_images = {
                    s['id']: s['selected']
                    for s in scenes
                    if isinstance(s, dict) and s.get('id') and s.get('selected')
                }
                if selected_images:
                    data['selected_images'] = selected_images

        if stage == 'video_generation' and 'clips' not in data:
            vid_art = artifacts.get('video_generation', {})
            if isinstance(vid_art, dict):
                clips = vid_art.get('clips', [])
                if clips:
                    data['clips'] = clips

        if stage == 'post_production' and 'selected_clips' not in data:
            vid_art = artifacts.get('video_generation', {})
            if isinstance(vid_art, dict):
                clips = vid_art.get('clips', [])
                selected_clips = {
                    c['id']: c['selected']
                    for c in clips
                    if isinstance(c, dict) and c.get('id') and c.get('selected')
                }
                if selected_clips:
                    data['selected_clips'] = selected_clips

    def persist_session_snapshot(self, session_id: str) -> Dict[str, Any]:
        """Persist the latest engine-owned state and return a status snapshot."""
        with self._state_lock:
            state = self.get_state(session_id)
            if not state:
                raise KeyError(f"Session not found: {session_id}")
            self.save_session_to_disk(session_id)
            return copy.deepcopy(state.status)

    def stop_session(self, session_id: str):
        self.get_stop_event(session_id).set()
        with self._state_lock:
            state = self.get_state(session_id)
            if state and state.status.get(state.current_stage.value) == "running":
                state.status[state.current_stage.value] = "stopped"
                state.error = None  # 清除错误，因为是主动停止
                state.updated_at = datetime.now()
                current_progress = state.stage_progress.get(state.current_stage.value, {})
                state.stage_progress[state.current_stage.value] = {
                    **current_progress,
                    "step": "已停止",
                    "message": "已停止",
                    "updated_at": time.time(),
                }
                self.save_session_to_disk(session_id)
        logger.info(f"Session {session_id} stop signal sent")

    def reset_stop_event(self, session_id: str):
        with self._state_lock:
            if session_id in self._stop_events:
                self._stop_events[session_id].clear()

    def track_background_task(self, task: Any):
        """Keep detached workflow tasks alive after an SSE client disconnects."""
        with self._state_lock:
            self._background_tasks.add(task)

        def _cleanup(done_task: Any):
            with self._state_lock:
                self._background_tasks.discard(done_task)
            try:
                done_task.exception()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.error('Detached workflow task failed')

        task.add_done_callback(_cleanup)

    def _get_next_stage(self, current: WorkflowStage) -> Optional[WorkflowStage]:
        try:
            idx = STAGE_ORDER.index(current)
            if idx + 1 < len(STAGE_ORDER):
                return STAGE_ORDER[idx + 1]
        except ValueError:
            pass
        return None

    # ──────────── 跨阶段同步逻辑 ────────────
    def _sync_artifacts_cross_stages(self, state: WorkflowState, stage: WorkflowStage, payload: Dict):
        """
        跨阶段数据同步钩子：当某个阶段产生新数据时，自动推送到后续阶段。
        例如：剧本续写产生的新角色/分镜，自动同步到 Stage 2 和 Stage 3。
        """
        if not isinstance(payload, dict):
            return

        # 案例 1: 剧本续写确认 (Script Confirmation)
        if stage == WorkflowStage.SCRIPT_GENERATION:
            # 获取合并后的角色、场景和剧集
            new_chars = payload.get("new_characters", [])
            new_settings = payload.get("new_settings", [])
            new_episodes = payload.get("new_episodes", [])
            # 如果没有新增剧集数据，无需同步到 Stage 2 和 3
            if not new_episodes: 
                return 
            
            # (A) 同步到第二阶段 (角色设计)
            if new_chars or new_settings:
                # 注意：WorkflowStage.CHARACTER_DESIGN 是 Enum，这里需要使用 .value
                char_stage_key = WorkflowStage.CHARACTER_DESIGN.value
                char_art = state.artifacts.get(char_stage_key)
                if not isinstance(char_art, dict):
                    char_art = {"characters": [], "settings": [], "version": 1}
                
                existing_chars = char_art.get("characters", [])
                for nc in new_chars:
                    if not any(c.get("id") == nc.get("character_id") for c in existing_chars):
                        existing_chars.append({
                            "id": nc.get("character_id"), "name": nc.get("name"), "description": nc.get("description"),
                            "selected": "", "versions": []
                        })
                char_art["characters"] = existing_chars
                
                existing_sets = char_art.get("settings", [])
                for ns in new_settings:
                    if not any(s.get("id") == ns.get("setting_id") for s in existing_sets):
                        existing_sets.append({
                            "id": ns.get("setting_id"), "name": ns.get("name"), "description": ns.get("description"),
                            "selected": "", "versions": []
                        })
                char_art["settings"] = existing_sets
                state.artifacts[char_stage_key] = char_art

            # (B) 同步到第三阶段 (分镜设计)
            if new_episodes:
                story_stage_key = WorkflowStage.STORYBOARD.value
                story_art = state.artifacts.get(story_stage_key)
                if not isinstance(story_art, dict):
                    story_art = {"episodes": [], "version": 1}
                
                existing_eps = story_art.get("episodes", [])
                for ne in new_episodes:
                    ep_num = ne.get("episode_number")
                    if not any(e.get("episode_number") == ep_num for e in existing_eps):
                        existing_eps.append({
                            "episode_number": ep_num,
                            "episode_title": ne.get("act_title") or f"第{ep_num}集",
                            "segments": []
                        })
                existing_eps.sort(key=lambda x: x.get("episode_number", 0))
                story_art["episodes"] = existing_eps
                state.artifacts[story_stage_key] = story_art

        # 案例 2: 分镜生成或修改同步到第四、第五阶段 (Storyboard -> Ref/Video)
        if stage == WorkflowStage.STORYBOARD:
            episodes = payload.get("episodes", []) if isinstance(payload, dict) else payload
            if not isinstance(episodes, list):
                return
                
            all_sync_clips = []
            for ep in episodes:
                if not isinstance(ep, dict):
                    continue
                ep_n = ep.get("episode_number", 0)
                for s_i, seg in enumerate(ep.get("segments", []), 1):
                    if not isinstance(seg, dict):
                        continue
                    seg_id = seg.get("segment_id", f"seg_{ep_n:02d}_{s_i:02d}")
                    
                    # 汇总 segment 级别的描述和时长
                    shots = seg.get("shots", [])
                    desc_video = " ".join([sh.get("plot") or sh.get("content") or "" for sh in shots]).strip()
                    desc_ref = " ".join([sh.get("visual_prompt") or sh.get("plot") or sh.get("content") or "" for sh in shots]).strip()
                    total_dur = seg.get("total_duration") or sum([sh.get("duration", 0) for sh in shots]) or 10
                    
                    all_sync_clips.append({
                        "segment_id": seg_id,
                        "desc_video": desc_video,
                        "desc_ref": desc_ref,
                        "duration": total_dur,
                        "episode": ep_n,
                        "index": s_i,
                        "name": f"第{ep_n}集-片段{s_i}"
                    })
            
            if not all_sync_clips:
                return

            # (A) 同步到第四阶段 (参考图生成)
            ref_stage_key = WorkflowStage.REFERENCE_GENERATION.value
            ref_art = state.artifacts.get(ref_stage_key)
            if not isinstance(ref_art, dict):
                ref_art = {"scenes": [], "version": 1}
            
            existing_scenes = ref_art.get("scenes", [])
            for c_info in all_sync_clips:
                id = c_info["segment_id"]
                idx = next((i for i, s in enumerate(existing_scenes) if s.get("id") == id), -1)
                if idx == -1:
                    existing_scenes.append({
                        "id": id,
                        "name": c_info["name"],
                        "index": c_info["index"],
                        "description": c_info["desc_ref"],
                        "selected": "",
                        "versions": [],
                        "status": "pending",
                        "episode": c_info["episode"]
                    })
                else:
                    # 更新已有记录
                    existing_scenes[idx]["description"] = c_info["desc_ref"]
                    existing_scenes[idx]["episode"] = c_info["episode"]
                    existing_scenes[idx]["index"] = c_info["index"]
                    existing_scenes[idx]["name"] = c_info["name"]
            
            # 排序：确保片段显示顺序正确 (按 id 排序，例如 seg_01_01 < seg_07_01)
            existing_scenes.sort(key=lambda x: x.get("id", ""))
            ref_art["scenes"] = existing_scenes
            state.artifacts[ref_stage_key] = ref_art

            # (B) 同步到第五阶段 (视频生成)
            video_stage_key = WorkflowStage.VIDEO_GENERATION.value
            video_art = state.artifacts.get(video_stage_key)
            if not isinstance(video_art, dict):
                video_art = {"clips": [], "version": 1}
            
            existing_clips = video_art.get("clips", [])
            for c_info in all_sync_clips:
                id = c_info["segment_id"]
                idx = next((i for i, c in enumerate(existing_clips) if c.get("id") == id), -1)
                if idx == -1:
                    existing_clips.append({
                        "id": id,
                        "name": c_info["name"],
                        "index": c_info["index"],
                        "description": c_info["desc_video"],
                        "duration": c_info["duration"],
                        "selected": "",
                        "versions": [],
                        "status": "pending",
                        "episode": c_info["episode"]
                    })
                else:
                    existing_clips[idx]["description"] = c_info["desc_video"]
                    existing_clips[idx]["duration"] = c_info["duration"]
                    existing_clips[idx]["episode"] = c_info["episode"]
                    existing_clips[idx]["index"] = c_info["index"]
                    existing_clips[idx]["name"] = c_info["name"]
            
            # 排序：确保片段显示顺序正确
            existing_clips.sort(key=lambda x: x.get("id", ""))
            video_art["clips"] = existing_clips
            state.artifacts[video_stage_key] = video_art

        # 案例 3: 角色/场景描述修改同步回剧本元数据，保证后续阶段读到用户最新描述。
        if stage == WorkflowStage.CHARACTER_DESIGN:
            script_art = state.artifacts.get(WorkflowStage.SCRIPT_GENERATION.value)
            if not isinstance(script_art, dict):
                return

            char_by_id = {
                c.get("id"): c for c in payload.get("characters", [])
                if isinstance(c, dict) and c.get("id")
            }
            setting_by_id = {
                s.get("id"): s for s in payload.get("settings", [])
                if isinstance(s, dict) and s.get("id")
            }

            for char in script_art.get("characters", []):
                if not isinstance(char, dict):
                    continue
                source = char_by_id.get(char.get("character_id") or char.get("id"))
                if source:
                    for key in ("name", "description", "species"):
                        if source.get(key):
                            char[key] = source[key]

            for setting in script_art.get("settings", []):
                if not isinstance(setting, dict):
                    continue
                source = setting_by_id.get(setting.get("setting_id") or setting.get("id"))
                if source:
                    for key in ("name", "description"):
                        if source.get(key):
                            setting[key] = source[key]

    def _recalculate_all_statuses(self, state: WorkflowState):
        """Generation produces a reviewable artifact; only human confirmation completes it."""
        for stage in WorkflowStage:
            if stage in (WorkflowStage.INIT, WorkflowStage.COMPLETED):
                continue
            key = stage.value
            if state.status.get(key) in {'running', 'error', 'stopped'}:
                continue
            state.status[key] = ('completed' if key in state.confirmed_stages
                                 else 'waiting' if state.artifacts.get(key) else 'pending')

    @staticmethod
    def _is_background_item_regeneration(stage: WorkflowStage, intervention: Optional[Dict]) -> bool:
        if not isinstance(intervention, dict):
            return False
        if stage == WorkflowStage.CHARACTER_DESIGN:
            return isinstance(intervention.get("regenerate_characters"), list) or isinstance(intervention.get("regenerate_settings"), list) or isinstance(intervention.get("regenerate_props"), list)
        if stage == WorkflowStage.REFERENCE_GENERATION:
            return isinstance(intervention.get("regenerate_scenes"), list)
        if stage == WorkflowStage.VIDEO_GENERATION:
            return isinstance(intervention.get("regenerate_clips"), list)
        return False

    @staticmethod
    def _background_regeneration_targets(stage: WorkflowStage, intervention: Optional[Dict]) -> Dict[str, Set[str]]:
        if not isinstance(intervention, dict):
            return {}
        if stage == WorkflowStage.CHARACTER_DESIGN:
            return {
                "characters": set(intervention.get("regenerate_characters") or []),
                "settings": set(intervention.get("regenerate_settings") or []),
                "props": set(intervention.get("regenerate_props") or []),
            }
        if stage == WorkflowStage.REFERENCE_GENERATION:
            return {"scenes": set(intervention.get("regenerate_scenes") or [])}
        if stage == WorkflowStage.VIDEO_GENERATION:
            return {"clips": set(intervention.get("regenerate_clips") or [])}
        return {}

    @staticmethod
    def _merge_item_regeneration_payload(
        existing: Any,
        payload: Any,
        item_keys: List[str],
        target_ids_by_key: Optional[Dict[str, Set[str]]] = None,
    ) -> Dict:
        """Merge concurrent single-item regeneration results without clobbering fresher items."""
        if not isinstance(existing, dict):
            existing = {}
        if not isinstance(payload, dict):
            return copy.deepcopy(existing)

        merged = copy.deepcopy(existing)
        for key, value in payload.items():
            if key not in item_keys:
                merged[key] = copy.deepcopy(value)

        for key in item_keys:
            existing_items = merged.get(key, [])
            payload_items = payload.get(key, [])
            if not isinstance(existing_items, list):
                existing_items = []
            if not isinstance(payload_items, list):
                merged[key] = existing_items
                continue

            target_ids = (target_ids_by_key or {}).get(key)
            by_id = {
                item.get("id"): copy.deepcopy(item)
                for item in existing_items
                if isinstance(item, dict) and item.get("id")
            }
            order = [
                item.get("id")
                for item in existing_items
                if isinstance(item, dict) and item.get("id")
            ]

            for item in payload_items:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                item_id = item["id"]
                if target_ids is not None and item_id not in target_ids:
                    continue
                current = by_id.get(item_id)
                if item_id not in order:
                    order.append(item_id)
                if not current:
                    by_id[item_id] = copy.deepcopy(item)
                    continue
                current_versions = current.get("versions") if isinstance(current.get("versions"), list) else []
                item_versions = item.get("versions") if isinstance(item.get("versions"), list) else []
                merged_versions = WorkflowEngine._merge_asset_versions(current_versions, item_versions)
                if len(item_versions) > len(current_versions):
                    merged_item = {**current, **copy.deepcopy(item)}
                    merged_item["versions"] = merged_versions
                    if current.get("selected"):
                        merged_item["selected"] = current.get("selected")
                        if item.get("status") in {"done", "failed"}:
                            merged_item["status"] = "done"
                    by_id[item_id] = merged_item
                elif len(item_versions) == len(current_versions):
                    merged_item = {**current, **copy.deepcopy(item)}
                    if not item.get("selected") and current.get("selected"):
                        merged_item["selected"] = current.get("selected")
                    if current.get("selected"):
                        merged_item["selected"] = current.get("selected")
                        if item.get("status") in {"done", "failed"}:
                            merged_item["status"] = "done"
                    if merged_versions:
                        merged_item["versions"] = merged_versions
                    if (
                        current.get("status") == "failed"
                        and item.get("status") == "done"
                        and not current.get("selected")
                    ):
                        merged_item["status"] = "failed"
                    by_id[item_id] = merged_item

            merged[key] = [by_id[item_id] for item_id in order if item_id in by_id]
        return merged

    @staticmethod
    def _merge_asset_versions(current_versions: Any, new_versions: Any) -> List[str]:
        merged: List[str] = []
        for value in list(current_versions if isinstance(current_versions, list) else []) + list(new_versions if isinstance(new_versions, list) else []):
            if value and value not in merged:
                merged.append(value)
        return merged

    @staticmethod
    def _selected_after_asset_update(current_selected: Any, new_selected: Any) -> Any:
        return current_selected or new_selected or ""

    async def execute_stage(self,
                            state: WorkflowState,
                            stage: WorkflowStage,
                            input_data: Any,
                            cancellation_check: Optional[Callable] = None,
                            progress_callback: Optional[Callable] = None,
                            intervention: Optional[Dict] = None,
                            video_usage_guard: Optional[Callable] = None) -> Dict:
        import time

        if not isinstance(input_data, dict):
            input_data = {}
        else:
            input_data = copy.deepcopy(input_data)

        with self._state_lock:
            input_data["_session_meta"] = copy.deepcopy(state.meta)
            input_data["_session_artifacts"] = copy.deepcopy(state.artifacts)

        active_registered = False
        background_item_regeneration = self._is_background_item_regeneration(stage, intervention)

        # 合并会话级停止信号与请求级取消检查
        session_stop = self.get_stop_event(state.session_id)
        def combined_cancel_check():
            return session_stop.is_set() or (cancellation_check and cancellation_check())


        # Persist every progress checkpoint; callbacks may arrive from worker threads.
        def persist_stage_progress(phase: str, step: str, percent: float):
            stage_key = stage.value
            try:
                safe_percent = max(0, min(100, int(round(float(percent)))))
            except (TypeError, ValueError):
                safe_percent = 0
            message = f"{phase}: {step}" if phase and step else (step or phase or "")
            state.stage_progress[stage_key] = {
                "phase": phase,
                "step": step,
                "message": message,
                "percent": safe_percent,
                "updated_at": time.time(),
            }
            state.updated_at = datetime.now()

        def merge_progress_artifact(data: dict):
            if not isinstance(data, dict):
                return
            stage_key = stage.value
            if data.get("assets_preview"):
                preview = copy.deepcopy(data["assets_preview"])
                if stage_key == "character_design":
                    preview.setdefault("props", copy.deepcopy((state.artifacts.get(stage_key) or {}).get("props", [])))
                state.artifacts[stage_key] = preview

            if data.get("validation_warning"):
                artifact = state.artifacts.setdefault(stage_key, {})
                artifact.setdefault("validation_warnings", []).append(copy.deepcopy(data["validation_warning"]))

            if data.get("beat_sheet"):
                state.artifacts.setdefault(stage_key, {}).update(
                    phase="generating", beat_sheet=copy.deepcopy(data["beat_sheet"])
                )
            if data.get("act_complete"):
                artifact = state.artifacts.setdefault(stage_key, {})
                artifact.setdefault("completed_acts", []).append(copy.deepcopy(data["act_complete"]))
            if data.get("scene_shots_complete"):
                artifact = state.artifacts.setdefault(stage_key, {})
                artifact.setdefault("shots", []).extend(copy.deepcopy(data["scene_shots_complete"].get("shots", [])))

            asset_update = data.get("asset_complete") or data.get("asset_update")
            if not isinstance(asset_update, dict):
                return

            artifact = state.artifacts.setdefault(stage_key, {})
            item_type = asset_update.get("type")
            item_id = asset_update.get("id")
            if not item_type or not item_id:
                return

            items = artifact.setdefault(item_type, [])
            if not isinstance(items, list):
                items = []
                artifact[item_type] = items

            for item in items:
                if isinstance(item, dict) and item.get("id") == item_id:
                    next_status = asset_update.get("status", item.get("status"))
                    item["generation_status"] = next_status
                    if item.get("selected") and next_status in {"done", "failed"}:
                        next_status = "done"
                    item["status"] = next_status
                    if "selected" in asset_update:
                        item["selected"] = self._selected_after_asset_update(
                            item.get("selected"),
                            asset_update.get("selected"),
                        )
                    if "versions" in asset_update:
                        item["versions"] = self._merge_asset_versions(
                            item.get("versions"),
                            asset_update.get("versions"),
                        )
                    return

            items.append({
                "id": item_id,
                "status": asset_update.get("status", "done"),
                "generation_status": asset_update.get("status", "done"),
                "selected": asset_update.get("selected", ""),
                "versions": asset_update.get("versions", []),
            })

        failed_items = set()

        def wrapped_progress_callback(phase: str, step: str, percent: float, data: dict = None):
            with self._state_lock:
                persist_stage_progress(phase, step, percent)
                if data:
                    update = data.get('asset_complete') or data.get('asset_update') or {}
                    identity = (update.get('type'), update.get('id'))
                    if update.get('status') == 'failed':
                        failed_items.add(identity)
                    elif update.get('status') == 'done':
                        failed_items.discard(identity)
                    merge_progress_artifact(data)

                self.save_session_to_disk(state.session_id)
            if progress_callback:
                progress_callback(phase, step, percent, data)

        if not background_item_regeneration:
            with self._state_lock:
                if state.session_id in self._active_sessions:
                    raise RuntimeError(f"Session {state.session_id} is already running a stage.")
                self._active_sessions.add(state.session_id)
                active_registered = True
                state.current_stage = stage
                state.error = None
                state.status[stage.value] = "running"
                state.updated_at = datetime.now()
                state.stage_progress[stage.value] = {
                    "phase": stage.value,
                    "step": "启动中...",
                    "message": "启动中...",
                    "percent": 0,
                    "updated_at": time.time(),
                }
                try:
                    self.save_session_to_disk(state.session_id)
                except Exception:
                    self._active_sessions.discard(state.session_id)
                    active_registered = False
                    raise

        try:
            agent = self.agent_factories[stage]()
            agent.video_usage_guard = video_usage_guard
            agent.set_cancellation_check(combined_cancel_check)
            agent.set_progress_callback(wrapped_progress_callback)
            if combined_cancel_check():
                raise asyncio.CancelledError()
            result = await agent.process(input_data, intervention=intervention)
            if combined_cancel_check():
                raise asyncio.CancelledError()
            if not isinstance(result, dict):
                logger.error(f"[execute_stage] Agent {stage.value} returned non-dict result: {type(result)}")
                result = {"payload": result}
            
            payload = result.get("payload", {})
            generated_payload = copy.deepcopy(payload)
            requires_intervention = result.get("requires_intervention", False)

            with self._state_lock:
                # 根据阶段类型处理数据持久化和同步逻辑
                if stage == WorkflowStage.SCRIPT_GENERATION:
                    state.meta["source_changed"] = False
                    state.meta["project_revision"] = state.meta.get("project_revision", 0) + 1
                    if requires_intervention:
                        # 【续写预览状态】直接保存 payload 以保留 new_episodes 供前端显示。
                        # 此时千万不要跨阶段同步（避免向第二、三阶段注入用户未确认的数据）。
                        state.artifacts[stage.value] = copy.deepcopy(payload)
                    else:
                        # 【确定续写 / 正常生成状态】
                        # 先同步增量数据到第二、三阶段
                        self._sync_artifacts_cross_stages(state, stage, payload)
                        # 然后清理第一阶段内部的临时增量字段并保存
                        clean_art = copy.deepcopy(payload)
                        for key in ["new_episodes", "new_characters", "new_settings", "sequel_idea"]:
                            clean_art.pop(key, None)
                        state.artifacts[stage.value] = clean_art
                else:
                    # 其他阶段正常执行跨阶段同步和赋值
                    if background_item_regeneration:
                        if stage == WorkflowStage.CHARACTER_DESIGN:
                            keys = ["characters", "settings", "props"]
                        elif stage == WorkflowStage.REFERENCE_GENERATION:
                            keys = ["scenes"]
                        elif stage == WorkflowStage.VIDEO_GENERATION:
                            keys = ["clips"]
                        else:
                            keys = []
                        target_ids = self._background_regeneration_targets(stage, intervention)
                        payload = self._merge_item_regeneration_payload(state.artifacts.get(stage.value, {}), payload, keys, target_ids)
                    self._sync_artifacts_cross_stages(state, stage, payload)
                    if stage == WorkflowStage.CHARACTER_DESIGN:
                        payload.setdefault("props", copy.deepcopy((state.artifacts.get(stage.value) or {}).get("props", [])))
                    state.artifacts[stage.value] = payload

                # 调试日志
                logger.info(f"[execute_stage] stage={stage.value}, intervention={intervention is not None}, requires_intervention={result.get('requires_intervention')}, stage_completed={result.get('stage_completed')}")

                # 重新计算所有阶段的状态（基于 artifacts 里的数据完整性）
                if not background_item_regeneration:
                    state.status[stage.value] = "pending"
                self._recalculate_all_statuses(state)

                state.status[stage.value] = "waiting"
                result["requires_intervention"] = True

                state.updated_at = datetime.now()
                state.stage_progress[stage.value] = {
                    "phase": stage.value,
                    "step": "等待确认" if state.status.get(stage.value) == "waiting" else "已完成",
                    "message": "等待确认" if state.status.get(stage.value) == "waiting" else "已完成",
                    "percent": 100,
                    "updated_at": time.time(),
                }
                # 立即保存状态到磁盘，确保前端能获取到最新状态
                self.save_session_to_disk(state.session_id)
            if stage.value in {'character_design', 'reference_generation', 'video_generation', 'post_production'}:
                targets = self._background_regeneration_targets(stage, intervention)
                if background_item_regeneration:
                    output_complete = all(
                        any(item.get('id') == item_id and item.get('selected') and item.get('status') != 'failed'
                            for item in generated_payload.get(key, []))
                        for key, ids in targets.items() for item_id in ids
                    )
                else:
                    output_complete = stage_ready(state, stage.value) and not any(
                        item.get('status') == 'failed'
                        for key in ('characters', 'settings', 'scenes', 'clips')
                        for item in generated_payload.get(key, [])
                    )
                if not output_complete or failed_items:
                    raise WorkflowConflict('STAGE_OUTPUT_INCOMPLETE', '部分产物生成失败，已保留成功结果，请重试失败项')
            return result

        except asyncio.CancelledError:
            with self._state_lock:
                if not background_item_regeneration:
                    state.status[stage.value] = "stopped"
                state.error = None
                state.updated_at = datetime.now()
                state.stage_progress[stage.value] = {
                    "phase": stage.value,
                    "step": "已取消",
                    "message": "已取消",
                    "percent": state.stage_progress.get(stage.value, {}).get("percent", 0),
                    "updated_at": time.time(),
                }
                self.save_session_to_disk(state.session_id)
            raise

        except Exception as e:
            with self._state_lock:
                if not background_item_regeneration:
                    state.status[stage.value] = "stopped" if combined_cancel_check() else "error"
                state.error = None if combined_cancel_check() else (
                    e.message if isinstance(e, (ModelOutputError, QuotaExceeded, WorkflowConflict, ShotPlanError, VideoInputError)) else "阶段执行失败，请检查模型配置后重试"
                )
                logger.warning('Stage %s failed (%s)', stage.value, type(e).__name__)
                state.updated_at = datetime.now()
                state.stage_progress[stage.value] = {
                    "phase": stage.value,
                    "step": "执行失败",
                    "message": "执行失败",
                    "percent": state.stage_progress.get(stage.value, {}).get("percent", 0),
                    "updated_at": time.time(),
                }
                # 确保保存错误状态
                self.save_session_to_disk(state.session_id)
            raise
        finally:
            if background_item_regeneration:
                with self._state_lock:
                    targets = self._background_regeneration_targets(stage, intervention)
                    artifact = state.artifacts.get(stage.value, {})
                    for kind, ids in targets.items():
                        for item in artifact.get(kind, []):
                            if item.get("id") in ids and item.get("status") == "running":
                                item["status"] = "done" if item.get("selected") else "failed"
                            if item.get("id") in ids and item.get("generation_status") == "running":
                                item["generation_status"] = "failed"
                    self.save_session_to_disk(state.session_id)
            if active_registered:
                with self._state_lock:
                    self._active_sessions.discard(state.session_id)

    def _require_idle(self, session_id):
        if getattr(self, 'novel_busy', lambda _:False)(session_id):
            raise WorkflowConflict('TASK_CONFLICT', '小说改编任务正在执行，请等待完成或停止后再修改')
        if any(job['status'] in {'queued', 'running', 'stopping'} for job in self.store.jobs(session_id)):
            raise WorkflowConflict('TASK_CONFLICT', '项目仍有任务执行中，请等待结束后再修改或确认')

    async def continue_workflow(self, session_id: str, stage: str = None) -> Dict:
        with self._state_lock:
            state = self.get_state(session_id)
            if not state:
                raise KeyError(session_id)
            self._require_idle(session_id)
            stage = stage or state.current_stage.value
            require_predecessors(state, stage)
            if not stage_ready(state, stage):
                raise WorkflowConflict('STAGE_INCOMPLETE', '本阶段产物尚不完整，请补齐或重新生成后再确认')
            if stage == "script_generation" and state.meta.get("source_changed"):
                raise WorkflowConflict("SOURCE_CHANGED", "原始内容已变更，请重新改写或保存确认后的剧本")
            if stage not in state.confirmed_stages:
                state.confirmed_stages.append(stage)
            state.status[stage] = 'completed'
            state.error = None
            state.stage_progress.setdefault(stage, {}).update(step='已确认', message='已确认', percent=100)
            next_stage = self._get_next_stage(WorkflowStage(stage))
            self.save_session_to_disk(session_id)
            return {'status': 'ready' if next_stage else 'completed',
                    'next_stage': next_stage.value if next_stage else None,
                    'session_id': session_id, 'confirmed_stages': list(state.confirmed_stages),
                    'status_map': copy.deepcopy(state.status)}

    # ──────────── Artifact 统一管理 ────────────

    def update_artifact(self, session_id: str, stage: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a user edit to an artifact, then recalculate and persist state."""
        with self._state_lock:
            state = self.get_state(session_id)
            if not state:
                raise KeyError(f"Session not found: {session_id}")

            self._require_idle(session_id)
            if stage == "script_generation":
                # Validate before touching state; legacy partial artifacts remain editable.
                try:
                    if not isinstance(body, dict) or not body:
                        raise ValueError()
                    current = state.artifacts.get(stage) or {}
                    candidate = {**current, **body}
                    if "title" in candidate and (not isinstance(candidate["title"], str) or not candidate["title"].strip()):
                        raise ValueError()
                    if "episodes" in body:
                        body = {**body, "episodes": validate_episodes(body["episodes"])}
                    if any(key in body for key in ("characters", "settings", "genre", "mood", "logline")):
                        ScriptMetadata.model_validate({"title": "未命名剧本", "characters": [], "settings": [], **candidate})
                    if "scenes" in body and (not isinstance(body["scenes"], list) or any(not isinstance(s, dict) for s in body["scenes"])):
                        raise ValueError()
                except (ValueError, ValidationError, ModelOutputError):
                    raise WorkflowConflict("SCRIPT_INVALID", "剧本格式无效，请检查标题、角色和分集正文") from None
            if stage == "storyboard" and isinstance(body, dict):
                segments = list(body.get("segments") or [])
                for episode in body.get("episodes") or []:
                    segments.extend(episode.get("segments") or [])
                available = {item.get("id") for kind in ("characters", "settings", "props")
                             for item in (state.artifacts.get("character_design") or {}).get(kind, []) if item.get("selected")}
                for segment in segments:
                    bindings = segment.get("reference_asset_ids")
                    if bindings is not None and (not isinstance(bindings, list) or len(bindings) > 10
                            or any(not isinstance(key, str) or key not in available for key in bindings)
                            or len(set(bindings)) != len(bindings)):
                        raise WorkflowConflict("REFERENCE_BINDING_INVALID", "参考素材已失效，请重新绑定本集图片")
                    if segment.get("shot_control") is not None:
                        validate_shot_control(segment)
            previous = copy.deepcopy(state.__dict__)
            try:
                state.updated_at = datetime.now()
                self._apply_artifact_update(state, stage, body if isinstance(body, dict) else {})
                if stage == 'script_generation' and (previous['artifacts'].get(stage) or {}).get('source_map') and any(key in body for key in ('episodes','characters','settings')):
                    state.artifacts[stage]['source_map']['manually_edited'] = True
                if stage == "script_generation":
                    state.meta["source_changed"] = False
                    state.meta["project_revision"] = state.meta.get("project_revision", 0) + 1
                invalidate_confirmations(state, stage)
                state.status[stage] = "pending"
                state.error = None
                state.stage_progress.setdefault(stage, {}).update(step="等待确认", message="等待确认")
                self._recalculate_all_statuses(state)
                self.save_session_to_disk(session_id)
            except Exception:
                state.__dict__.clear()
                state.__dict__.update(previous)
                raise
            return {
                "status": "ok",
                "status_map": copy.deepcopy(state.status),
                "artifact": copy.deepcopy(state.artifacts.get(stage)),
            }

    def _apply_artifact_update(self, state: WorkflowState, stage: str, body: Dict[str, Any]):
        """Apply a user edit to in-memory artifacts before the session is persisted."""
        merge_keys_by_stage = {
            "character_design": ("characters", "settings", "props"),
            "reference_generation": ("scenes",),
            "video_generation": ("clips",),
        }
        if stage in merge_keys_by_stage:
            current_art = state.artifacts.get(stage, {})
            if isinstance(current_art, dict):
                for list_key in merge_keys_by_stage[stage]:
                    if list_key not in body:
                        continue
                    current_items = current_art.get(list_key, [])
                    incoming_items = body.get(list_key, [])
                    if not isinstance(current_items, list) or not isinstance(incoming_items, list):
                        continue
                    current_by_id = {
                        item.get("id"): item
                        for item in current_items
                        if isinstance(item, dict) and item.get("id")
                    }
                    merged_items = []
                    seen_ids = set()
                    for incoming in incoming_items:
                        if not isinstance(incoming, dict) or not incoming.get("id"):
                            continue
                        item_id = incoming["id"]
                        current = current_by_id.get(item_id, {})
                        merged = {**current, **incoming}
                        if current.get("selected") and not incoming.get("selected"):
                            merged["selected"] = current.get("selected")
                        merged["versions"] = self._merge_asset_versions(
                            current.get("versions"),
                            incoming.get("versions"),
                        )
                        if current.get("selected") and incoming.get("status") in {"failed", "pending"}:
                            merged["status"] = current.get("status", "done")
                        merged_items.append(merged)
                        seen_ids.add(item_id)
                    for current in current_items:
                        if isinstance(current, dict) and current.get("id") not in seen_ids:
                            merged_items.append(current)
                    body[list_key] = merged_items

        if stage == "storyboard" and any(k in body for k in ("episodes", "segments", "shots")):
            for shot in body.get('shots', []):
                if isinstance(shot, dict) and 'is_new' in shot:
                    shot['is_new'] = False

            input_segments = list(body.get('segments', []))
            for ep in body.get('episodes', []):
                if isinstance(ep, dict):
                    input_segments.extend(seg for seg in ep.get('segments', []) if isinstance(seg, dict))

            previous_segments = {seg.get('segment_id'): seg for ep in (state.artifacts.get('storyboard') or {}).get('episodes', [])
                                 for seg in ep.get('segments', [])}
            seg_info_list = []
            for seg in input_segments:
                if seg == previous_segments.get(seg.get('segment_id')):
                    continue
                seg_id = seg.get('segment_id')
                if not seg_id:
                    continue
                shots = seg.get('shots', [])
                desc_video = " ".join([sh.get("plot") or sh.get("content") or "" for sh in shots]).strip()
                total_dur = seg.get("total_duration") or sum([sh.get("duration", 0) for sh in shots]) or 10
                seg_info_list.append({
                    "segment_id": seg_id,
                    "desc": seg.get("video_prompt") or desc_video,
                    "duration": total_dur,
                    "visual_prompt": seg.get("visual_prompt", ""),
                    "has_visual_prompt": "visual_prompt" in seg,
                })

            video_art = state.artifacts.get('video_generation', {})
            if isinstance(video_art, dict) and isinstance(video_art.get('clips'), list):
                for clip in video_art['clips']:
                    target = next((item for item in seg_info_list if item["segment_id"] == clip.get('id')), None)
                    if target:
                        clip['duration'] = target['duration']
                        clip['description'] = target['desc']

            ref_art = state.artifacts.get('reference_generation', {})
            if isinstance(ref_art, dict) and isinstance(ref_art.get('scenes'), list):
                for scene in ref_art['scenes']:
                    target = next((item for item in seg_info_list if item["segment_id"] == scene.get('id')), None)
                    if target and target.get("has_visual_prompt"):
                        scene['description'] = target['visual_prompt']
                        scene['visual_prompt'] = target['visual_prompt']

            if "segments" in body and "episodes" not in body:
                body = {k: v for k, v in body.items() if k != "segments"}
            body.pop('new_shot_ids', None)

        elif stage == "reference_generation":
            if "segments" in body:
                seg_id_to_prompt = {
                    s['segment_id']: s.get('visual_prompt', '')
                    for s in body['segments']
                    if isinstance(s, dict) and 'segment_id' in s
                }

                storyboard_art = state.artifacts.get('storyboard', {})
                if isinstance(storyboard_art, dict):
                    for ep in storyboard_art.get('episodes', []):
                        if not isinstance(ep, dict):
                            continue
                        for seg in ep.get('segments', []):
                            if isinstance(seg, dict) and seg.get('segment_id') in seg_id_to_prompt:
                                seg['visual_prompt'] = seg_id_to_prompt[seg.get('segment_id')]

                ref_art = state.artifacts.get('reference_generation', {})
                if isinstance(ref_art, dict):
                    for scene in ref_art.get('scenes', []):
                        if isinstance(scene, dict) and scene.get('id') in seg_id_to_prompt:
                            scene['description'] = seg_id_to_prompt[scene.get('id')]

                body = {k: v for k, v in body.items() if k != "segments"}

            ref_art = state.artifacts.get('reference_generation', {})
            if isinstance(ref_art, dict):
                scenes = ref_art.get('scenes', [])
                is_selection_format = any(isinstance(k, str) and not isinstance(v, (list, dict)) for k, v in body.items())
                if is_selection_format and scenes:
                    for scene in scenes:
                        if isinstance(scene, dict) and scene.get('id') in body:
                            scene['selected'] = body[scene.get('id')]
                    body = {}

        elif stage == "video_generation":
            clip_id_to_duration = {}
            clip_id_to_description = {}
            for clip_id, value in body.items():
                if isinstance(value, dict):
                    if 'duration' in value:
                        clip_id_to_duration[clip_id] = value['duration']
                    if 'description' in value:
                        clip_id_to_description[clip_id] = value['description']

            if clip_id_to_duration or clip_id_to_description:
                storyboard_art = state.artifacts.get('storyboard', {})
                if isinstance(storyboard_art, dict):
                    for ep in storyboard_art.get('episodes', []):
                        if not isinstance(ep, dict):
                            continue
                        for seg in ep.get('segments', []):
                            if isinstance(seg, dict) and seg.get('segment_id') in clip_id_to_duration:
                                seg['total_duration'] = clip_id_to_duration[seg.get('segment_id')]

                vid_art = state.artifacts.get('video_generation', {})
                if isinstance(vid_art, dict):
                    for clip in vid_art.get('clips', []):
                        if not isinstance(clip, dict):
                            continue
                        clip_id = clip.get('id')
                        if clip_id in clip_id_to_duration:
                            clip['duration'] = clip_id_to_duration[clip_id]
                        if clip_id in clip_id_to_description:
                            clip['description'] = clip_id_to_description[clip_id]

            vid_art = state.artifacts.get('video_generation', {})
            if isinstance(vid_art, dict):
                clips = vid_art.get('clips', [])
                is_selection_format = any(isinstance(k, str) and not isinstance(v, (list, dict)) for k, v in body.items())
                if is_selection_format and clips:
                    for clip in clips:
                        if isinstance(clip, dict) and clip.get('id') in body:
                            clip['selected'] = body[clip.get('id')]
                    body = {}

        current = state.artifacts.get(stage)
        if current is None:
            state.artifacts[stage] = body
        elif isinstance(current, dict):
            current.update(body)
        else:
            state.artifacts[stage] = body
        if stage in ("storyboard", "video_generation"):
            # Edited segment durations must be reflected in the episode estimate.
            scene_merge.refresh_duration_summary(state.artifacts.get("storyboard"))

    def upload_artifact_image(
        self,
        session_id: str,
        stage: str,
        item_type: str,
        item_id: str,
        file_obj: Any,
        filename: str = "",
    ) -> Dict[str, Any]:
        """Save a user-provided image and attach it to the target artifact item."""
        allowed_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
        ext = os.path.splitext(filename or "")[1].lower() or ".png"
        if ext not in allowed_exts:
            raise ValueError(f"仅支持 {', '.join(sorted(allowed_exts))} 格式的图片")

        with self._state_lock:
            state = self.get_state(session_id)
            if not state:
                raise KeyError(f"Session not found: {session_id}")

            self._require_idle(session_id)
            cfg = self._upload_item_config(stage, item_type, item_id)
            absolute_path, relative_path = self._next_upload_path(session_id, cfg, ext)
            try:
                written = 0
                with open(absolute_path, "wb") as buffer:
                    while chunk := file_obj.read(UPLOAD_CHUNK_SIZE):
                        written += len(chunk)
                        if written > ARTIFACT_IMAGE_LIMIT:
                            raise ValueError("图片大小不能超过 25 MB")
                        buffer.write(chunk)
                validate_upload(absolute_path, ext)
            except ValueError:
                if os.path.exists(absolute_path):
                    os.remove(absolute_path)
                raise
            except Exception:
                if os.path.exists(absolute_path):
                    os.remove(absolute_path)
                raise RuntimeError("图片保存失败，请稍后重试") from None

            artifact = state.artifacts.setdefault(stage, {})
            items = artifact.setdefault(cfg["list_key"], [])
            if not isinstance(items, list):
                items = []
                artifact[cfg["list_key"]] = items

            target = next((item for item in items if isinstance(item, dict) and item.get("id") == item_id), None)
            if target is None:
                target = {"id": item_id, "name": item_id, "description": "", "versions": []}
                items.append(target)

            versions = target.get("versions")
            if not isinstance(versions, list):
                versions = []
            if relative_path not in versions:
                versions.append(relative_path)
            target["versions"] = versions
            target["selected"] = relative_path
            target["status"] = "done"
            invalidate_confirmations(state, stage)

            self._recalculate_all_statuses(state)
            self.save_session_to_disk(session_id)
            return {
                "status": "ok",
                "path": relative_path,
                "item_id": item_id,
                "item_type": item_type,
                "artifact": copy.deepcopy(state.artifacts.get(stage)),
                "status_map": copy.deepcopy(state.status),
            }

    @staticmethod
    def _upload_item_config(stage: str, item_type: str, item_id: str) -> Dict[str, str]:
        if not SAFE_ARTIFACT_ID.fullmatch(item_id or ""):
            raise ValueError("素材 ID 只能包含字母、数字、点、下划线和连字符")
        if stage == "character_design" and item_type == "characters":
            base = item_id if item_id.startswith("char_") else f"char_{item_id}"
            return {"list_key": "characters", "dir": os.path.join("Assets", "characters"), "base": base}
        if stage == "character_design" and item_type == "settings":
            base = item_id if item_id.startswith("set_") else f"set_{item_id}"
            return {"list_key": "settings", "dir": os.path.join("Assets", "settings"), "base": base}
        if stage == "character_design" and item_type == "props":
            return {"list_key": "props", "dir": os.path.join("Assets", "props"), "base": item_id}
        if stage == "reference_generation" and item_type == "scenes":
            return {"list_key": "scenes", "dir": "Scenes", "base": item_id}
        raise ValueError("Unsupported upload target")

    @staticmethod
    def _next_upload_path(session_id: str, cfg: Dict[str, str], ext: str) -> tuple[str, str]:
        from config import settings

        save_dir = os.path.join(settings.RESULT_DIR, "image", str(session_id), cfg["dir"])
        os.makedirs(save_dir, exist_ok=True)

        pattern = re.compile(rf"^{re.escape(cfg['base'])}_upload_v(\d+)\.", re.IGNORECASE)
        max_version = 0
        for name in os.listdir(save_dir):
            match = pattern.match(name)
            if match:
                max_version = max(max_version, int(match.group(1)))
        version = f"v{max_version + 1}"
        upload_filename = f"{cfg['base']}_upload_{version}{ext}"
        absolute_path = os.path.join(save_dir, upload_filename)
        relative_path = os.path.relpath(absolute_path, settings.BASE_DIR)
        return absolute_path, relative_path

    # ──────────── 会话持久化 ────────────

    def save_session_to_disk(self, session_id: str, meta: Dict = None):
        """Compatibility name: SQLite is now the authoritative snapshot store."""
        with self._state_lock:
            state = self.sessions.get(session_id)
            if state is None:
                raise KeyError(session_id)
            if meta:
                state.meta.update({k: _normalize_meta_value(v, k) for k, v in meta.items() if v is not None})
            data = state.to_dict()
            data["schema_version"] = 1
            data["updated_at"] = state.updated_at.timestamp() if isinstance(state.updated_at, datetime) else state.updated_at
            data["started_at"] = state.started_at.timestamp() if isinstance(state.started_at, datetime) else state.started_at
            self.store.save_session(data)

    def _load_sessions_from_disk(self):
        records = []
        for filename in os.listdir(self._session_dir):
            if not filename.endswith('.json'):
                continue
            try:
                with open(os.path.join(self._session_dir, filename), encoding='utf-8') as handle:
                    data = json.load(handle)
                if not isinstance(data, dict) or not SAFE_ARTIFACT_ID.fullmatch(data.get("session_id", "")):
                    raise ValueError("Invalid session")
                records.append(data)
            except (ValueError, OSError, TypeError):
                logger.warning("Skipping invalid legacy session file")
        self.store.import_legacy(records)
        for data in self.store.sessions():
            sid = data["session_id"]
            state = WorkflowState(sid)
            try:
                state.current_stage = WorkflowStage(data.get("current_stage", "init"))
            except ValueError:
                state.current_stage = WorkflowStage.INIT
            old_status = data.get("status", {})
            if isinstance(old_status, str):
                old_status = "waiting" if old_status == "waiting_intervention" else old_status
                for stage in state.status:
                    if stage in data.get("stages_completed", []):
                        state.status[stage] = "completed"
                    elif stage == state.current_stage.value:
                        state.status[stage] = old_status
            elif isinstance(old_status, dict):
                state.status.update(old_status)
            state.artifacts = data.get("artifacts", {})
            state.stage_progress = data.get("stage_progress", {})
            state.confirmed_stages = [s for s in data.get("confirmed_stages", []) if s in state.status]
            self._recalculate_all_statuses(state)
            state.error = data.get("error")
            state.updated_at = data.get("updated_at", 0)
            state.started_at = data.get("started_at", data.get("created_at"))
            state.meta = _extract_session_meta(data)
            self.sessions[sid] = state
            # Never silently re-submit a possibly billed provider operation after a crash.
            changed = False
            for stage, status in state.status.items():
                if status in {"running", "queued"}:
                    state.status[stage] = "error"
                    state.error = "服务重启，任务已中断；已保存产物，可手动重试"
                    progress = state.stage_progress.setdefault(stage, {})
                    progress.update(step=state.error, message=state.error)
                    changed = True
            def recover_items(value):
                nonlocal changed
                if isinstance(value, dict):
                    if value.get("status") == "running":
                        value["status"] = "done" if value.get("selected") else "failed"
                        changed = True
                    if value.get("generation_status") == "running":
                        value["generation_status"] = "failed"
                        changed = True
                    for child in value.values():
                        recover_items(child)
                elif isinstance(value, list):
                    for child in value:
                        recover_items(child)
            recover_items(state.artifacts)
            if changed:
                self.save_session_to_disk(sid)

    def delete_session(self, session_id: str) -> bool:
        """删除指定会话（内存 + 磁盘 + 结果文件）"""
        import shutil

        from config import settings

        with self._state_lock:
            if getattr(self, 'novel_busy', lambda _:False)(session_id) or session_id in self._active_sessions or any(
                job["status"] in {"queued", "running", "stopping"}
                for job in self.store.jobs(session_id)
            ):
                logger.warning(f"Refusing to delete active session: {session_id}")
                return False

            children = [sid for sid, item in self.sessions.items() if item.meta.get("parent_project_id") == session_id]
            for child in children:
                try:
                    self._require_idle(child)
                except WorkflowConflict:
                    return False
            for child in children:
                if not self.delete_session(child):
                    return False
            path = os.path.join(self._session_dir, f"{session_id}.json")
            exists = session_id in self.sessions or os.path.exists(path)
            if not exists:
                return False

            self.store.delete_session(session_id)

            # 从内存中移除
            self.sessions.pop(session_id, None)
            self._stop_events.pop(session_id, None)

            # 1. 删除会话元数据文件
            if os.path.exists(path):
                os.remove(path)

            # 2. 删除结果文件（剧本、图片、视频）
            result_base = settings.RESULT_DIR

            # 删除剧本文件
            script_file = os.path.join(result_base, 'script', f'{session_id}.json')
            if os.path.exists(script_file):
                os.remove(script_file)

            # 删除图片目录
            image_dir = os.path.join(result_base, 'image', session_id)
            if os.path.exists(image_dir):
                shutil.rmtree(image_dir)

            # 删除视频目录
            video_dir = os.path.join(result_base, 'video', session_id)
            if os.path.exists(video_dir):
                shutil.rmtree(video_dir)

        logger.info(f"Session and results deleted: {session_id}")
        return True

    def cleanup_orphan_results(self) -> Dict[str, Any]:
        """Remove result files whose session no longer exists."""
        from config import settings

        with self._state_lock:
            session_ids = set(self.sessions.keys())
            if os.path.isdir(self._session_dir):
                for filename in os.listdir(self._session_dir):
                    if filename.endswith('.json'):
                        session_ids.add(filename[:-5])

            cleaned = {"scripts": [], "images": [], "videos": []}
            result_base = settings.RESULT_DIR

            script_dir = os.path.join(result_base, 'script')
            if os.path.isdir(script_dir):
                for filename in os.listdir(script_dir):
                    if not filename.endswith('.json'):
                        continue
                    sid = filename[:-5]
                    if sid not in session_ids:
                        os.remove(os.path.join(script_dir, filename))
                        cleaned["scripts"].append(sid)

            image_dir = os.path.join(result_base, 'image')
            if os.path.isdir(image_dir):
                for dirname in os.listdir(image_dir):
                    if dirname != 'test_avail' and dirname not in session_ids:
                        shutil.rmtree(os.path.join(image_dir, dirname))
                        cleaned["images"].append(dirname)

            video_dir = os.path.join(result_base, 'video')
            if os.path.isdir(video_dir):
                for dirname in os.listdir(video_dir):
                    if dirname != 'test_avail' and dirname not in session_ids:
                        shutil.rmtree(os.path.join(video_dir, dirname))
                        cleaned["videos"].append(dirname)

            return {"status": "cleaned", "cleaned": cleaned}

    def get_scene_asset_counts(self, session_id: str, scene_number: int) -> Dict[str, Any]:
        """Count generated reference images/videos from the current artifact state only."""
        from config import settings

        with self._state_lock:
            state = self.get_state(session_id)
            if not state:
                raise KeyError(f"Session not found: {session_id}")
            artifacts = copy.deepcopy(state.artifacts)

        storyboard = artifacts.get('storyboard', {})
        segment_ids = self._segment_ids_for_scene(storyboard, scene_number)

        ref_artifact = artifacts.get('reference_generation', {})
        ref_scenes = ref_artifact.get('scenes', []) if isinstance(ref_artifact, dict) else []
        ref_image_count = self._count_existing_assets(ref_scenes, segment_ids, settings.CODE_DIR, include_versions=True)

        video_artifact = artifacts.get('video_generation', {})
        video_clips = video_artifact.get('clips', []) if isinstance(video_artifact, dict) else []
        video_count = self._count_existing_assets(video_clips, segment_ids, settings.CODE_DIR, include_versions=False)

        return {
            "scene_number": scene_number,
            "reference_images": ref_image_count,
            "videos": video_count,
            "shot_count": len(segment_ids),
        }

    @staticmethod
    def _segment_ids_for_scene(storyboard: Any, scene_number: int) -> List[str]:
        if not isinstance(storyboard, dict):
            return []

        ids: List[str] = []
        for shot in storyboard.get('shots', []):
            if isinstance(shot, dict) and shot.get('scene_number') == scene_number and shot.get('shot_id'):
                ids.append(shot['shot_id'])

        for episode in storyboard.get('episodes', []):
            if not isinstance(episode, dict):
                continue
            for segment in episode.get('segments', []):
                if not isinstance(segment, dict):
                    continue
                segment_scene = segment.get('scene_number') or segment.get('segment_number')
                if segment_scene == scene_number and segment.get('segment_id'):
                    ids.append(segment['segment_id'])

        return list(dict.fromkeys(ids))

    @staticmethod
    def _asset_exists(code_dir: str, path: str) -> bool:
        if not path:
            return False
        candidate = path if os.path.isabs(path) else os.path.join(code_dir, path.lstrip('/'))
        return os.path.exists(candidate)

    @classmethod
    def _count_existing_assets(
        cls,
        items: List[Any],
        target_ids: List[str],
        code_dir: str,
        *,
        include_versions: bool,
    ) -> int:
        count = 0
        target_set = set(target_ids)
        for item in items:
            if not isinstance(item, dict) or item.get('id') not in target_set:
                continue
            selected = item.get('selected')
            if selected and cls._asset_exists(code_dir, selected):
                count += 1
            if include_versions:
                versions = item.get('versions', [])
                for version in versions if isinstance(versions, list) else []:
                    if version and version != selected and cls._asset_exists(code_dir, version):
                        count += 1
        return count

    def list_saved_sessions(self) -> List[Dict]:
        """列出所有已保存的会话概要"""
        with self._state_lock:
            sessions: List[Dict] = []
            for sid, state in self.sessions.items():
                try:
                    meta = copy.deepcopy(state.meta)
                    artifacts = copy.deepcopy(state.artifacts)
                    script_artifact = artifacts.get("script_generation", {})
                    title = (
                        meta.get("title")
                        or script_artifact.get("title")
                        or meta.get("idea")
                        or meta.get("user_textbox_input")
                        or ""
                    )
                    updated_at = state.updated_at
                    if isinstance(updated_at, datetime):
                        date_value = updated_at.timestamp()
                    else:
                        date_value = updated_at or 0
                    sessions.append({
                        "id": sid,
                        "title": title,
                        "idea": meta.get("idea") or meta.get("user_textbox_input") or "",
                        "style": meta.get("style") or "",
                        "date": date_value,
                        "status": copy.deepcopy(state.status),
                        "current_stage": state.current_stage.value,
                        "source_type": meta.get("source_type", "legacy"),
                        "confirmed_stages": list(state.confirmed_stages),
                        "meta": meta,
                        "stage_progress": copy.deepcopy(state.stage_progress),
                    })
                except Exception:
                    continue
            sessions.sort(key=lambda x: x.get("date", 0), reverse=True)
            return sessions

import asyncio
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError

from api.dependencies import task_manager, workflow_engine
from api.routers.files import merge_uploaded_file_into_idea
from api.schemas.project import (InterventionRequest, ProjectStartRequest, ProjectDraftRequest,
                                 ProjectDraftUpdate, ProjectDraftResponse, ProjectErrorResponse, EpisodeCreateRequest, SourceUpdateRequest, ScriptContentRequest, AssetMutationRequest, ProjectOverviewResponse, StudioAssetsResponse, ScriptContentResponse, StudioSegmentRequest, StudioAssetImportRequest)
from api.schemas.quota import VideoQuotaSnapshot
from api.schemas.task import AcceptedTask, TaskList, TaskSnapshot
from api.services.project_helpers import build_openclaw_message
from config import settings
from core.tasks import TERMINAL, TaskConflict
from core.workflow_rules import WorkflowConflict
from core.shot_control import ShotPlanError

router = APIRouter(tags=["Workflow"])

REQUIRED_MODEL_FIELDS = (
    "llm_model",
    "vlm_model",
    "image_t2i_model",
    "image_it2i_model",
)

VIDEO_MODE_TO_MODEL_FIELD = {
    "first_frame": "video_first_frame_model",
    "start_end_frame": "video_start_end_model",
    "reference": "video_reference_model",
    "text": "video_text_model",
}


def _active_video_model(values: dict) -> str:
    mode = values.get("video_generation_mode") or "first_frame"
    model_field = VIDEO_MODE_TO_MODEL_FIELD.get(mode, "video_first_frame_model")
    default_attr = {
        "video_first_frame_model": "VIDEO_FIRST_FRAME_MODEL",
        "video_start_end_model": "VIDEO_START_END_MODEL",
        "video_reference_model": "VIDEO_REFERENCE_MODEL",
        "video_text_model": "VIDEO_TEXT_MODEL",
    }.get(model_field, "VIDEO_MODEL")
    # Legacy session compatibility: fall back to the old single video_model field when mode-specific fields are absent.
    return values.get(model_field) or values.get("video_model") or getattr(settings, default_attr, "") or ""


def _require_model_fields(values: dict, stage: str | None = None) -> None:
    required = {
        "script_generation": ("llm_model",),
        "character_design": ("image_t2i_model", "vlm_model"),
        "storyboard": ("llm_model",),
        "reference_generation": REQUIRED_MODEL_FIELDS,
        "video_generation": (),
        "post_production": (),
    }.get(stage, REQUIRED_MODEL_FIELDS)
    # Single-version runs deliberately skip VLM review. A retired reviewer
    # must not block the image provider that this stage actually uses.
    if (values.get("pilot_mode") or values.get("controlled_episode")) and stage in ("character_design", "reference_generation"):
        required = tuple(field for field in required if field != "vlm_model")
    missing = [field for field in required if not values.get(field)]
    if stage in (None, "video_generation") and not _active_video_model(values):
        missing.append("video_model")
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"请先配置本阶段需要的模型：{', '.join(missing)}",
        )
    from core.service_settings import service_settings, resolve_service, SettingsError
    runtime = None
    selected = [values.get(field) for field in required]
    if stage in (None, "video_generation"):
        selected.append(_active_video_model(values))
    for model in selected:
        if not model or not model.startswith("svc/"):
            continue
        if runtime is None:
            runtime = service_settings.runtime()
        try:
            conn = resolve_service(model, runtime)
            if not conn["credentials"] or not all(conn["credentials"].values()):
                raise SettingsError("SERVICE_NOT_CONFIGURED", "本阶段使用的服务尚未配置完整凭据，请先到设置填写")
        except SettingsError as exc:
            raise HTTPException(400, exc.message) from None


@router.post("/api/projects/{session_id}/assets", response_model=StudioAssetsResponse, responses={code: {"model": ProjectErrorResponse} for code in (404, 409, 422, 500)})
async def manage_project_assets(session_id: str, req: AssetMutationRequest):
    try:
        return workflow_engine.manage_assets(session_id, req.model_dump(exclude_none=True))
    except KeyError:
        return task_error(404, "SESSION_NOT_FOUND", "剧集不存在")
    except WorkflowConflict as exc:
        return task_error(409, exc.code, exc.message)
    except (OSError, sqlite3.Error):
        return task_error(500, "STORAGE_UNAVAILABLE", "素材保存失败，请重试")


@router.post("/api/projects", response_model=ProjectDraftResponse, status_code=201,
             responses={200: {"model": ProjectDraftResponse}, **{code: {"model": ProjectErrorResponse} for code in (409, 422, 500)}},
             openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": ProjectDraftRequest.model_json_schema()}}},
                            "parameters": [{"in": "header", "name": "Idempotency-Key", "required": True, "schema": {"type": "string", "format": "uuid"}}]})
async def create_project_draft(request: Request):
    try:
        req = ProjectDraftRequest.model_validate(await request.json())
        key = str(uuid.UUID(request.headers.get("idempotency-key", "")))
        raw = req.model_dump()
        digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        from core.service_settings import project_defaults
        inherited = project_defaults()
        raw = {**raw, **{k: v for k, v in inherited.items() if k not in req.model_fields_set}}
        final_idea = merge_uploaded_file_into_idea(req.idea, req.file_path)
        meta = {**raw, "idea": final_idea, "user_textbox_input": req.idea,
                "title": req.title or req.idea[:40] or "未命名项目",
                "style": raw.get("style") or "realistic",
                "draft_attachment_text": final_idea[len(req.idea):] if req.file_path else ""}
        if req.project_container:
            for field, attr in (("llm_model", "LLM_MODEL"), ("vlm_model", "VLM_MODEL"),
                                ("image_t2i_model", "IMAGE_T2I_MODEL"), ("image_it2i_model", "IMAGE_IT2I_MODEL"),
                                ("video_model", "VIDEO_MODEL"), ("video_first_frame_model", "VIDEO_FIRST_FRAME_MODEL"),
                                ("video_start_end_model", "VIDEO_START_END_MODEL"), ("video_reference_model", "VIDEO_REFERENCE_MODEL"),
                                ("video_text_model", "VIDEO_TEXT_MODEL")):
                meta[field] = meta.get(field) or getattr(settings, attr, "") or ""
        meta.pop("file_path", None)
        result, created = workflow_engine.create_draft(meta, key, digest)
        return JSONResponse(status_code=201 if created else 200, content=ProjectDraftResponse(**result).model_dump())
    except WorkflowConflict as exc:
        return task_error(409, exc.code, exc.message)
    except (ValueError, ValidationError):
        return task_error(422, "INVALID_PROJECT", "项目参数无效；请检查标题、集数、目标时长及创建请求标识")
    except HTTPException as exc:
        return task_error(exc.status_code, "INVALID_FILE", "导入文件无效，请重新上传")
    except (OSError, sqlite3.Error):
        return task_error(500, "STORAGE_UNAVAILABLE", "本地保存失败，请检查磁盘空间后重试")


@router.patch("/api/projects/{session_id}", response_model=ProjectDraftResponse,
              responses={code: {"model": ProjectErrorResponse} for code in (404, 409, 422, 500)},
              openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": ProjectDraftUpdate.model_json_schema()}}}})
async def update_project_draft(session_id: str, request: Request):
    try:
        req = ProjectDraftUpdate.model_validate(await request.json())
        values = req.model_dump(exclude_unset=True, exclude_none=True, exclude={"expected_revision"})
        if not values:
            return task_error(422, "INVALID_PROJECT", "没有可保存的项目修改")
        return workflow_engine.update_draft(session_id, values, req.expected_revision)
    except KeyError:
        return task_error(404, "SESSION_NOT_FOUND", "项目不存在")
    except WorkflowConflict as exc:
        return task_error(409, exc.code, exc.message)
    except (ValueError, ValidationError):
        return task_error(422, "INVALID_PROJECT", "项目参数无效，请检查标题、集数与目标时长")
    except (OSError, sqlite3.Error):
        return task_error(500, "STORAGE_UNAVAILABLE", "本地保存失败，修改尚未保存，请重试")


@router.get("/api/projects/{project_id}", response_model=ProjectOverviewResponse, responses={404: {"model": ProjectErrorResponse}})
async def project_detail(project_id: str):
    try:
        return workflow_engine.project_detail(project_id)
    except KeyError:
        return task_error(404, "PROJECT_NOT_FOUND", "项目不存在")


@router.post("/api/projects/{project_id}/episodes", response_model=ProjectDraftResponse, status_code=201)
async def create_episode(project_id: str, req: EpisodeCreateRequest, request: Request):
    try:
        key = str(uuid.UUID(request.headers.get("idempotency-key", "")))
        values = req.model_dump()
        digest = hashlib.sha256(json.dumps({"parent": project_id, **values}, sort_keys=True).encode()).hexdigest()
        parent = workflow_engine.get_state(project_id)
        if parent and "target_duration" not in req.model_fields_set:
            values["target_duration"] = parent.meta.get("target_duration", 60)
        if parent and "video_resolution" not in req.model_fields_set:
            values["video_resolution"] = parent.meta.get("video_resolution", "720P")
        result, created = workflow_engine.create_episode(project_id, values, key, digest)
        return JSONResponse(status_code=201 if created else 200, content=result)
    except KeyError:
        return task_error(404, "PROJECT_NOT_FOUND", "项目不存在")
    except WorkflowConflict as exc:
        return task_error(409, exc.code, exc.message)
    except ValueError:
        return task_error(422, "INVALID_EPISODE", "剧集参数或创建标识无效")
    except (OSError, sqlite3.Error):
        return task_error(500, "STORAGE_UNAVAILABLE", "剧集保存失败，请重试")


@router.patch("/api/projects/{session_id}/source", response_model=ProjectDraftResponse)
async def update_source(session_id: str, req: SourceUpdateRequest):
    try:
        content = merge_uploaded_file_into_idea(req.content, req.file_path) if req.file_path else req.content
        if len(content) > 200000:
            return task_error(422, "SOURCE_TOO_LARGE", "原始内容超过 20 万字符，请拆分到不同剧集")
        return workflow_engine.update_source(session_id, content, req.expected_revision)
    except KeyError:
        return task_error(404, "SESSION_NOT_FOUND", "剧集不存在")
    except WorkflowConflict as exc:
        return task_error(409, exc.code, exc.message)
    except HTTPException as exc:
        return task_error(exc.status_code, "INVALID_FILE", "导入文件无效，请重新上传")
    except (OSError, sqlite3.Error):
        return task_error(500, "STORAGE_UNAVAILABLE", "本地保存失败，输入已保留")


@router.patch("/api/projects/{session_id}/script-content", response_model=ScriptContentResponse, responses={code: {"model": ProjectErrorResponse} for code in (404, 409, 422, 500)})
async def save_script_content(session_id: str, req: ScriptContentRequest):
    try:
        return workflow_engine.save_script_content(session_id, req.content, req.expected_revision, req.use_original)
    except KeyError:
        return task_error(404, "SESSION_NOT_FOUND", "剧集不存在")
    except WorkflowConflict as exc:
        return task_error(409, exc.code, exc.message)
    except (OSError, sqlite3.Error):
        return task_error(500, "STORAGE_UNAVAILABLE", "本地保存失败，输入已保留")


@router.post("/api/project/start")
async def start_project(req: ProjectStartRequest):
    final_idea = merge_uploaded_file_into_idea(req.idea, req.file_path)
    _require_model_fields(req.model_dump())

    session_id = uuid.uuid4().hex
    meta = {
        "idea": final_idea,
        "user_textbox_input": req.idea,
        "style": req.style or getattr(settings, "STYLE", None) or "realistic",
        "video_ratio": req.video_ratio or "9:16",
        "video_resolution": req.video_resolution or "720P",
        "expand_idea": req.expand_idea if req.expand_idea is not None else True,
        "llm_model": req.llm_model,
        "vlm_model": req.vlm_model,
        "image_t2i_model": req.image_t2i_model,
        "image_it2i_model": req.image_it2i_model,
        # Legacy request/session compatibility: clients created before the split only send video_model.
        "video_first_frame_model": req.video_first_frame_model or req.video_model or getattr(settings, "VIDEO_FIRST_FRAME_MODEL", ""),
        "video_start_end_model": req.video_start_end_model or getattr(settings, "VIDEO_START_END_MODEL", ""),
        "video_reference_model": req.video_reference_model or getattr(settings, "VIDEO_REFERENCE_MODEL", ""),
        "video_text_model": req.video_text_model or getattr(settings, "VIDEO_TEXT_MODEL", ""),
        "video_generation_mode": req.video_generation_mode or getattr(settings, "VIDEO_GENERATION_MODE", "first_frame"),
        "video_model": req.video_model,
        "enable_concurrency": req.enable_concurrency if req.enable_concurrency is not None else True,
        "web_search": req.web_search if req.web_search is not None else False,
        "episodes": req.episodes if req.episodes is not None else 4,
        "pilot_mode": bool(req.pilot_mode),
        "controlled_episode": bool(req.controlled_episode),
    }
    meta["video_model"] = _active_video_model(meta)
    session = workflow_engine.create_session(session_id, meta)

    return {
        "session_id": session_id,
        "status": session["status"],
        "params": {
            "idea": final_idea,
            "file_path": req.file_path,
            "style": req.style,
            "llm_model": meta["llm_model"],
            "vlm_model": meta["vlm_model"],
            "image_t2i_model": meta["image_t2i_model"],
            "image_it2i_model": meta["image_it2i_model"],
            "video_first_frame_model": meta["video_first_frame_model"],
            "video_start_end_model": meta["video_start_end_model"],
            "video_reference_model": meta["video_reference_model"],
            "video_text_model": meta["video_text_model"],
            "video_generation_mode": meta["video_generation_mode"],
            "video_model": meta["video_model"],
            "episodes": meta["episodes"],
            "video_ratio": meta["video_ratio"],
            "video_resolution": meta["video_resolution"],
            "pilot_mode": meta["pilot_mode"],
            "controlled_episode": meta["controlled_episode"],
        }
    }


def task_error(status, code, message):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def task_response(job, request):
    """Existing streaming callers remain compatible; new clients receive a short 202."""
    session_id, task_id = job['session_id'], job['task_id']
    status_url = f"/api/project/{session_id}/tasks/{task_id}"
    if 'respond-async' in request.headers.get('prefer', ''):
        return JSONResponse(status_code=202, content=AcceptedTask(
            task_id=task_id, session_id=session_id, stage=job['stage'],
            status=job['status'], status_url=status_url,
        ).model_dump(), headers={'Location': status_url})

    async def events():
        sequence = 0
        while True:
            current = task_manager.get(session_id, task_id, sequence)
            for event in current['events']:
                sequence = event['seq']
                yield json.dumps(event) + '\n'
            if current['status'] in TERMINAL:
                if current['status'] == 'completed':
                    result = current['result'] or {}
                    yield json.dumps({
                        'type': 'stage_complete', 'stage': current['stage'],
                        'status': workflow_engine.get_status_snapshot(session_id)['status'],
                        'requires_intervention': result.get('requires_intervention', False),
                        'payload_summary': result.get('payload'),
                        'openclaw': build_openclaw_message(current['stage'], result),
                    }) + '\n'
                    yield json.dumps({'type': 'done', 'task_id': task_id}) + '\n'
                else:
                    error = current['error'] or {'code': 'TASK_FAILED', 'message': '任务失败'}
                    yield json.dumps({'type': 'error', 'error': error, 'content': error['message']}) + '\n'
                return
            if await request.is_disconnected():
                return
            yield json.dumps({'type': 'heartbeat'}) + '\n'
            await asyncio.sleep(1)
    return StreamingResponse(events(), media_type='application/x-ndjson', headers={
        'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no',
    })


@router.post("/api/project/{session_id}/execute/{stage}")
async def execute_stage(session_id: str, stage: str, request: Request):
    try:
        if stage not in workflow_engine.agent_factories:
            return task_error(400, 'INVALID_STAGE', '无效的生成阶段')
        if not workflow_engine.get_state(session_id):
            return task_error(404, 'SESSION_NOT_FOUND', '项目不存在')
        raw = await request.body()
        body = json.loads(raw) if raw else {}
        if not isinstance(body, dict):
            return task_error(422, 'INVALID_INPUT', '请求内容必须是 JSON 对象')
        _, input_data = workflow_engine.prepare_stage_execution(session_id, stage, body)
        _require_model_fields(input_data, stage)
        # New draft contract; legacy stream clients may resume from their own artifacts.
        if stage == "script_generation" and input_data.get("source_type") == "original" and not str(input_data.get("idea") or "").strip():
            return task_error(422, "IDEA_REQUIRED", "请先填写创作想法或导入剧本，再生成剧本")
        job = task_manager.submit(session_id, stage, input_data,
                                  idempotency_key=request.headers.get('idempotency-key'))
        return task_response(job, request)
    except WorkflowConflict as exc:
        return task_error(409, exc.code, exc.message)
    except TaskConflict as exc:
        return task_error(409, 'TASK_CONFLICT', str(exc))
    except (ValueError, json.JSONDecodeError):
        return task_error(422, 'INVALID_INPUT', '请求内容无效')
    except HTTPException as exc:
        return task_error(exc.status_code, 'INVALID_MODEL_CONFIG', exc.detail)


@router.get("/api/project/{session_id}/tasks/{task_id}", response_model=TaskSnapshot)
async def get_task(session_id: str, task_id: str, after: int = 0):
    try:
        return task_manager.get(session_id, task_id, after)
    except KeyError:
        return task_error(404, 'TASK_NOT_FOUND', '任务不存在')


@router.get("/api/project/{session_id}/tasks", response_model=TaskList)
async def list_tasks(session_id: str):
    if not workflow_engine.get_state(session_id):
        return task_error(404, 'SESSION_NOT_FOUND', '项目不存在')
    return {'tasks': [task_manager.public(job) for job in task_manager.store.jobs(session_id)]}


@router.post("/api/project/{session_id}/tasks/{task_id}/retry")
async def retry_task(session_id: str, task_id: str, request: Request):
    try:
        job = task_manager.retry(session_id, task_id, request.headers.get('idempotency-key'))
        return task_response(job, request)
    except KeyError:
        return task_error(404, 'TASK_NOT_FOUND', '任务不存在')
    except WorkflowConflict as exc:
        return task_error(409, exc.code, exc.message)
    except TaskConflict as exc:
        return task_error(409, 'TASK_CONFLICT', str(exc))


@router.get("/api/project/{session_id}/quota", response_model=VideoQuotaSnapshot)
async def get_video_quota(session_id: str):
    if not workflow_engine.get_state(session_id):
        return task_error(404, 'SESSION_NOT_FOUND', '项目不存在')
    return task_manager.quota.snapshot(session_id)


@router.get("/api/project/{session_id}/status")
async def get_project_status(session_id: str):
    snapshot = workflow_engine.get_status_snapshot(session_id)
    if not snapshot:
        raise HTTPException(404, "Session not found")
    return snapshot


@router.get("/api/project/{session_id}/status/from_disk")
async def get_project_status_from_disk(session_id: str):
    # 兼容旧前端路由名；实际读取统一走 WorkflowEngine 的内存状态入口。
    snapshot = workflow_engine.get_status_snapshot(session_id)
    if not snapshot:
        raise HTTPException(404, "Session not found")
    return snapshot


@router.get("/api/project/{session_id}/artifact/{stage}")
async def get_artifact(session_id: str, stage: str):
    try:
        artifact = workflow_engine.get_artifact_snapshot(session_id, stage)
    except KeyError:
        raise HTTPException(404, "Session not found")

    if artifact is not None:
        return {"stage": stage, "artifact": artifact}

    raise HTTPException(404, f"Artifact for stage '{stage}' not found")


@router.patch("/api/project/{session_id}/models")
async def update_models(session_id: str, request: Request):
    body = await request.json()
    allowed_keys = (
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
        "video_ratio",
        "video_resolution",
        "style",
        "enable_concurrency",
        "pilot_mode",
        "controlled_episode",
    )
    try:
        return workflow_engine.update_session_meta(session_id, body if isinstance(body, dict) else {}, allowed_keys)
    except KeyError:
        raise HTTPException(404, "Session not found")


@router.post("/api/project/{session_id}/artifact/{stage}/upload_image")
async def upload_artifact_image(
    session_id: str,
    stage: str,
    item_type: str = Form(...),
    item_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Upload a user-provided image into a stage artifact and persist the session."""
    try:
        return await asyncio.to_thread(workflow_engine.upload_artifact_image,
            session_id=session_id,
            stage=stage,
            item_type=item_type,
            item_id=item_id,
            file_obj=file.file,
            filename=file.filename or "",
        )
    except KeyError:
        raise HTTPException(404, "Session not found")
    except WorkflowConflict as exc:
        return task_error(409, exc.code, exc.message)
    except ValueError as exc:
        return task_error(400, "INVALID_UPLOAD", str(exc))
    except RuntimeError:
        return task_error(500, "UPLOAD_FAILED", "图片保存失败，请稍后重试")


@router.patch("/api/project/{session_id}/artifact/{stage}")
async def update_artifact(session_id: str, stage: str, request: Request):
    """保存用户在某阶段的选择/修改，同时更新内存状态和磁盘快照。"""
    try:
        if stage not in workflow_engine.agent_factories:
            return task_error(400, "INVALID_STAGE", "无效的生成阶段")
        body = await request.json()
        if not isinstance(body, dict):
            return task_error(422, "INVALID_INPUT", "请求内容必须是 JSON 对象")
        return workflow_engine.update_artifact(session_id, stage, body)
    except KeyError:
        raise HTTPException(404, "Session not found")
    except WorkflowConflict as exc:
        return task_error(422 if exc.code == "SCRIPT_INVALID" else 409, exc.code, exc.message)
    except ShotPlanError as exc:
        return task_error(400, exc.code, exc.message)
    except ValueError:
        return task_error(422, "INVALID_INPUT", "请求内容无效")
    except (OSError, sqlite3.Error):
        return task_error(500, "STORAGE_UNAVAILABLE", "本地保存失败，修改尚未保存，请重试")


@router.post("/api/project/{session_id}/intervene")
async def intervene(session_id: str, req: InterventionRequest, request: Request):
    try:
        state, input_data = workflow_engine.prepare_intervention_execution(
            session_id=session_id,
            stage=req.stage,
            modifications=req.modifications,
        )
    except KeyError:
        raise HTTPException(404, "Session not found")
    except WorkflowConflict as exc:
        return task_error(409, exc.code, exc.message)
    except ValueError:
        return task_error(400, "INVALID_INPUT", "阶段或素材提取参数无效")

    try:
        job = task_manager.submit(session_id, req.stage, input_data, req.modifications,
                                  request.headers.get('idempotency-key'))
        return task_response(job, request)
    except WorkflowConflict as exc:
        return task_error(409, exc.code, exc.message)
    except TaskConflict as exc:
        return task_error(409, 'TASK_CONFLICT', str(exc))
    except ValueError:
        return task_error(400, 'INVALID_INPUT', '阶段或重生成素材编号无效')


@router.post("/api/project/{session_id}/continue")
async def continue_workflow(session_id: str, request: Request):
    try:
        raw = await request.body()
        body = json.loads(raw) if raw else {}
        if not isinstance(body, dict):
            return task_error(422, 'INVALID_INPUT', '请求内容必须是 JSON 对象')
        if body.get('stage') is not None and (not isinstance(body['stage'], str) or not body['stage']):
            return task_error(422, 'INVALID_STAGE', '无效的确认阶段')
        return await workflow_engine.continue_workflow(session_id, body.get('stage'))
    except KeyError:
        return task_error(404, 'SESSION_NOT_FOUND', '项目不存在')
    except WorkflowConflict as exc:
        return task_error(409, exc.code, exc.message)
    except ValueError:
        return task_error(422, 'INVALID_STAGE', '无效的确认阶段')


@router.get("/api/project/{session_id}/export/{episode}")
async def download_episode(session_id: str, episode: int):
    snapshot = workflow_engine.get_status_snapshot(session_id)
    if not snapshot:
        return task_error(404, 'SESSION_NOT_FOUND', '项目不存在')
    artifact = snapshot['artifacts'].get('post_production') or {}
    videos = artifact.get('final_videos') or []
    if not videos and artifact.get('final_video'):
        videos = [{'episode': 1, 'path': artifact['final_video']}]
    video = next((item for item in videos if item.get('episode') == episode), None)
    if not video or not isinstance(video.get('path'), str):
        return task_error(404, 'EXPORT_NOT_FOUND', '该集尚未生成成片')
    path = Path(video['path']).resolve()
    root = (Path(settings.CODE_DIR) / 'result/video' / session_id / 'output').resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() != '.mp4':
        return task_error(404, 'EXPORT_NOT_FOUND', '成片文件缺失，请重新导出')
    return FileResponse(path, media_type='video/mp4', filename=f'episode-{episode}.mp4')


@router.post("/api/project/{session_id}/stop")
async def stop_project(session_id: str):
    try:
        task_manager.stop(session_id)
    except KeyError:
        return task_error(404, 'SESSION_NOT_FOUND', '项目不存在')
    return {"status": "stopped", "session_id": session_id}


@router.get("/api/project/{session_id}/scene/{scene_number}/assets")
async def check_scene_assets(session_id: str, scene_number: int):
    try:
        return workflow_engine.get_scene_asset_counts(session_id, scene_number)
    except KeyError:
        raise HTTPException(404, "Session not found")


@router.patch("/api/projects/{session_id}/segments/{segment_id}", response_model=StudioAssetsResponse)
async def save_studio_segment(session_id: str, segment_id: str, body: StudioSegmentRequest):
    try:
        return await asyncio.to_thread(workflow_engine.save_studio_segment, session_id, segment_id, body.model_dump())
    except KeyError:
        return task_error(404, 'SESSION_NOT_FOUND', '项目不存在')
    except WorkflowConflict as exc:
        return task_error(409, exc.code, exc.message)
    except ShotPlanError as exc:
        return task_error(422, exc.code, exc.message)
    except Exception:
        return task_error(500, 'SAVE_FAILED', '分镜保存失败，输入已保留，请重试')


@router.post("/api/projects/{session_id}/assets/import", response_model=StudioAssetsResponse)
async def import_studio_asset(session_id: str, body: StudioAssetImportRequest):
    try:
        return await asyncio.to_thread(workflow_engine.import_studio_asset, session_id, body.model_dump())
    except KeyError:
        return task_error(404, 'SESSION_NOT_FOUND', '项目不存在')
    except WorkflowConflict as exc:
        return task_error(409, exc.code, exc.message)
    except Exception:
        return task_error(500, 'SAVE_FAILED', '素材导入失败，请重试')

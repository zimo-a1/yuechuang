import shutil
import sqlite3
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from config import Config
from core.service_settings import service_settings, catalog, PROVIDERS, SettingsError, project_defaults
from api.schemas.settings import (ConnectionRequest, RevisionRequest, DefaultModelRequest, DefaultsRequest,
                                  AdvancedRequest, ApplyDefaultsRequest)


class SettingsRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def wrapped(request: Request):
            try:
                return await handler(request)
            except SettingsError as exc:
                return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})
            except RequestValidationError:
                return JSONResponse(status_code=422, content={"error": {"code": "INVALID_SETTINGS", "message": "设置格式无效，请检查必填项、地址和数字范围"}})
            except (OSError, sqlite3.Error):
                return JSONResponse(status_code=500, content={"error": {"code": "SETTINGS_STORAGE_FAILED", "message": "设置保存失败，请检查本地磁盘后重试"}})
        return wrapped


router = APIRouter(tags=["Settings"], route_class=SettingsRoute)


@router.get("/api/settings")
def get_settings():
    cfg = Config.as_dict()
    return {**service_settings.public(), "catalog": catalog(),
            "providers": [{"id": p, "label": label, "base_url": cfg["api_providers"][p]["base_url"]} for p, label in PROVIDERS.items()],
            "advanced": {"server": cfg["server"], "common": cfg["api_providers"]["common"]}}


@router.post("/api/settings/connections", status_code=201)
def add_connection(req: ConnectionRequest):
    return service_settings.save_connection(req.revision, req.model_dump(exclude={"revision"}))


@router.put("/api/settings/connections/{connection_id}")
def update_connection(connection_id: str, req: ConnectionRequest):
    return service_settings.save_connection(req.revision, req.model_dump(exclude={"revision"}), connection_id)


@router.post("/api/settings/connections/{connection_id}/default")
def set_default(connection_id: str, req: DefaultModelRequest):
    return service_settings.set_default(req.revision, connection_id, req.model)


@router.post("/api/settings/connections/{connection_id}/test")
def test_connection(connection_id: str, req: RevisionRequest):
    return service_settings.probe(req.revision, connection_id)


@router.put("/api/settings/defaults")
def save_defaults(req: DefaultsRequest):
    return service_settings.save_defaults(req.revision, req.defaults.model_dump())


@router.get("/api/settings/project-defaults")
def get_project_defaults():
    return project_defaults()


@router.put("/api/settings/advanced")
def save_advanced(req: AdvancedRequest):
    from api.logging_config import apply_access_log_setting, apply_log_level_setting
    def change(doc):
        Config.update_config({"server": req.server.model_dump(), "api_providers": {"common": req.common.model_dump()}})
    service_settings.mutate(req.revision, change)
    apply_log_level_setting()
    apply_access_log_setting()
    return get_settings()


@router.get("/api/settings/storage")
def get_storage():
    from api.dependencies import workflow_engine, task_manager
    from api.routers.ip_library import get_library
    space = shutil.disk_usage(Config.BASE_DIR)
    jobs = sorted(workflow_engine.store.jobs(), key=lambda j: j["created_at"], reverse=True)
    return {"paths": {"项目数据": str(workflow_engine.store.db_path), "生成素材": Config.RESULT_DIR, "IP 正文与原文件": str(get_library().path),
                      "配置版本": str(service_settings.directory / "settings.sqlite3"), "提示词模板": str(Path(Config.BASE_DIR) / "prompts")},
            "disk": {"total": space.total, "free": space.free, "used": space.used},
            "worker_concurrency": task_manager.concurrency, "task_count": len(jobs),
            "tasks": [{k: j.get(k) for k in ("task_id", "session_id", "stage", "status", "created_at", "config_version")} for j in jobs[:30]]}


@router.post("/api/settings/apply/{session_id}")
def apply_defaults(session_id: str, req: ApplyDefaultsRequest):
    from api.dependencies import workflow_engine
    from core.workflow_rules import WorkflowConflict
    values = project_defaults()
    if values["settings_revision"] != req.revision:
        raise SettingsError("SETTINGS_CHANGED", "默认设置已变化，请重新加载后应用", 409)
    try:
        return workflow_engine.apply_creation_defaults(session_id, values, req.expected_project_revision)
    except KeyError:
        raise SettingsError("PROJECT_NOT_FOUND", "项目不存在", 404)
    except WorkflowConflict as exc:
        raise SettingsError(exc.code, exc.message, 409)

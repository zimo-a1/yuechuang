import logging
import os
import sys
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from api.logging_config import setup_concurrent_logging
from config import settings

setup_concurrent_logging()

logger = logging.getLogger(__name__)

from api.routers import (
    configuration_router,
    files_router,
    health_router,
    models_router,
    sessions_router,
    stages_router,
    workflow_router,
)

DEFAULT_CORS_ORIGINS = ["http://127.0.0.1:3000", "http://localhost:3000"]


def _cors_origins() -> list[str]:
    configured = []
    for raw_origin in os.getenv("YC_CORS_ORIGINS", "").split(","):
        origin = raw_origin.strip().rstrip("/")
        parsed = urlparse(origin)
        if (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.netloc
            and not parsed.path
            and not parsed.params
            and not parsed.query
            and not parsed.fragment
        ):
            configured.append(origin)
    return list(dict.fromkeys(DEFAULT_CORS_ORIGINS + configured))


@asynccontextmanager
async def lifespan(app: FastAPI):
    from api.routers.novel import get_novel
    novel = get_novel()
    logger.info("Starting YueChuang API")
    logger.info("Code directory mounted at /code: %s", settings.CODE_DIR)
    yield
    await novel.shutdown()
    from api.dependencies import task_manager
    await task_manager.shutdown()
    logger.info("YueChuang API shutdown complete")


app = FastAPI(title="YueChuang", version="2.0.0", lifespan=lifespan)

cors_origins = _cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info("CORS enabled for origins: %s", cors_origins)

os.makedirs(settings.CODE_DIR, exist_ok=True)
app.mount("/code", StaticFiles(directory=settings.CODE_DIR), name="code")

app.include_router(health_router)
app.include_router(files_router)
app.include_router(workflow_router)
app.include_router(sessions_router)
app.include_router(stages_router)
app.include_router(models_router)
app.include_router(configuration_router)
from api.routers.settings import router as settings_router
app.include_router(settings_router)
logger.info("API routers registered")


@app.get("/")
async def root():
    return {"service": "YueChuang", "version": "2.0.0", "health": "/api/health"}

from api.routers.ip_library import router as ip_library_router
app.include_router(ip_library_router)
from api.routers.novel import router as novel_router
app.include_router(novel_router)

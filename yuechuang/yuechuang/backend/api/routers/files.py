import asyncio
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from config import settings
from models.file_reader import FileReader
from path_security import resolve_allowed_local_file
from upload_validation import InvalidUpload, safe_display_name, validate_upload

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Files"])

DOCUMENT_UPLOAD_LIMIT = 20 * 1024 * 1024
MEDIA_UPLOAD_LIMIT = 500 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 1024 * 1024


def upload_error(status, code, message):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


async def _save_upload(file: UploadFile, destination: str, max_bytes: int) -> int:
    written = 0
    try:
        with open(destination, "wb") as buffer:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail=f"文件大小不能超过 {max_bytes // 1024 // 1024} MB")
                buffer.write(chunk)
        await asyncio.to_thread(validate_upload, destination)
    except (Exception, asyncio.CancelledError):
        if os.path.exists(destination):
            os.remove(destination)
        raise
    return written


def _safe_upload_name(filename: str, fallback: str = "upload") -> str:
    original_name = safe_display_name(filename or fallback)
    return f"{uuid.uuid4().hex}_{original_name}"


def _path_size(path: str) -> int:
    if os.path.isfile(path) or os.path.islink(path):
        return os.path.getsize(path)
    total = 0
    for root, dirs, files in os.walk(path):
        for name in files:
            item = os.path.join(root, name)
            try:
                total += os.path.getsize(item)
            except OSError:
                continue
        for name in dirs:
            item = os.path.join(root, name)
            if os.path.islink(item):
                try:
                    total += os.path.getsize(item)
                except OSError:
                    continue
    return total


@router.post("/api/upload_file")
async def upload_file(file: UploadFile = File(...)):
    allowed_exts = [".docx", ".doc", ".txt", ".md", ".pdf"]
    filename = safe_display_name(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in allowed_exts:
        return upload_error(400, "INVALID_UPLOAD", f"仅支持 {', '.join(allowed_exts)} 格式的文件")

    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    safe_filename = _safe_upload_name(filename)
    file_path = os.path.join(settings.TEMP_DIR, safe_filename)
    try:
        await _save_upload(file, file_path, DOCUMENT_UPLOAD_LIMIT)
    except InvalidUpload as exc:
        return upload_error(400, "INVALID_UPLOAD", str(exc))
    except HTTPException as exc:
        return upload_error(exc.status_code, "UPLOAD_TOO_LARGE", exc.detail)
    except Exception as e:
        logger.error("保存上传文件失败 (%s)", type(e).__name__)
        return upload_error(500, "UPLOAD_FAILED", "文件保存失败，请稍后重试")

    return {"filename": filename, "file_path": safe_filename}


@router.post("/api/upload_media")
async def upload_media(file: UploadFile = File(...)):
    allowed_exts = [
        ".jpg", ".jpeg", ".png", ".webp", ".bmp",
        ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ]
    filename = safe_display_name(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in allowed_exts:
        return upload_error(400, "INVALID_UPLOAD", f"仅支持 {', '.join(allowed_exts)} 格式的媒体文件")

    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    safe_filename = _safe_upload_name(filename)
    file_path = os.path.join(settings.TEMP_DIR, safe_filename)
    try:
        await _save_upload(file, file_path, MEDIA_UPLOAD_LIMIT)
    except InvalidUpload as exc:
        return upload_error(400, "INVALID_UPLOAD", str(exc))
    except HTTPException as exc:
        return upload_error(exc.status_code, "UPLOAD_TOO_LARGE", exc.detail)
    except Exception as e:
        logger.error("保存上传媒体失败 (%s)", type(e).__name__)
        return upload_error(500, "UPLOAD_FAILED", "媒体保存失败，请稍后重试")

    return {
        "filename": filename,
        "file_path": str(Path(file_path).resolve()),
    }


@router.delete("/api/cache/temp")
async def clear_temp_cache():
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    deleted = 0
    freed_bytes = 0
    errors = []
    for entry in os.scandir(settings.TEMP_DIR):
        try:
            freed_bytes += _path_size(entry.path)
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.remove(entry.path)
            deleted += 1
        except Exception as exc:
            logger.warning("Failed to delete temp cache item (%s)", type(exc).__name__)
            errors.append({"path": entry.name, "error": "文件清理失败"})
    return {
        "status": "ok",
        "deleted": deleted,
        "freed_bytes": freed_bytes,
        "freed_mb": round(freed_bytes / 1024 / 1024, 2),
        "errors": errors,
    }


def merge_uploaded_file_into_idea(idea: str, file_path: Optional[str]) -> str:
    if not file_path:
        return idea

    try:
        full_path = resolve_allowed_local_file(file_path, [settings.TEMP_DIR])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    content = FileReader.extract_text(full_path)
    if content:
        original_filename = "_".join(os.path.basename(file_path).split("_")[1:])
        prompt_fragment = FileReader.format_as_prompt(original_filename, content)
        idea = f"{idea}\n\n{prompt_fragment}"
    logger.info("成功处理上传文件，提取 %d 字符", len(content))
    return idea

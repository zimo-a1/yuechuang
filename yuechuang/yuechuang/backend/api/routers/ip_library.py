"""Local IP catalog. Import, preview and binding never dispatch generation jobs."""
import asyncio
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import ValidationError

from api.dependencies import workflow_engine
from api.schemas.ip_library import (ImportMetadata, MetadataUpdate, ChaptersUpdate, AdaptProject,
                                    IPDetail, IPList, VersionInfo, ChapterText, LibraryErrorResponse)
from api.schemas.project import ProjectDraftResponse
from core.ip_library import IPLibrary, LibraryError, digest
from core.ip_parser import parse_novel
from core.workflow_rules import WorkflowConflict
from core.output_validation import ModelOutputError
from core.service_settings import SettingsError
from upload_validation import InvalidUpload, safe_display_name

LIMIT = 20 * 1024 * 1024


class LibraryRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe_handler(request: Request):
            try:
                return await handler(request)
            except LibraryError as exc:
                code, message, status = exc.code, exc.message, exc.status
            except SettingsError as exc:
                code, message, status = exc.code, exc.message, exc.status
            except ModelOutputError as exc:
                code, message, status = exc.code, exc.message, 422
            except WorkflowConflict as exc:
                code, message, status = exc.code, exc.message, 409
            except InvalidUpload as exc:
                code, message, status = 'INVALID_IMPORT', str(exc), 422
            except (ValidationError, RequestValidationError, ValueError):
                code, message, status = 'INVALID_IP_REQUEST', '请检查必填资料、文件、章节范围和操作标识', 422
            except (OSError, sqlite3.Error):
                code, message, status = 'STORAGE_UNAVAILABLE', '本地读写失败，请检查磁盘空间后重试，已有版本保留', 500
            except Exception:
                code, message, status = 'IMPORT_FAILED', '正文处理失败，请检查文件并重试，已有版本保留', 500
            return JSONResponse(status_code=status, content={'error':{'code':code, 'message':message}})
        return safe_handler


router = APIRouter(prefix='/api/ip', tags=['IP Library'], route_class=LibraryRoute,
                   responses={n:{'model':LibraryErrorResponse} for n in (404,409,413,422,500)})


def get_library():
    from config import settings
    default = Path(os.environ.get('YC_STATE_DIR', Path(settings.BASE_DIR) / '.runtime')) / 'ip_library'
    return IPLibrary(os.environ.get('YC_IP_DIR', default))


def operation_key(value):
    return str(uuid.UUID(value))


def details(library, ip_id):
    data = library.detail(ip_id)
    for project in workflow_engine.list_saved_sessions():
        meta = project.get('meta', {})
        if meta.get('ip_binding', {}).get('ip_id') == ip_id and not meta.get('parent_project_id'):
            binding = meta['ip_binding']
            data['projects'].append({'id':project['id'], 'title':project['title'], 'version_number':binding['version_number'],
                                     'version_id':binding['version_id'], 'first_chapter':binding['first_chapter'], 'last_chapter':binding['last_chapter']})
    return data


async def read_novel(file):
    filename = safe_display_name(file.filename)
    extension = Path(filename).suffix.lower()
    if extension not in {'.txt', '.docx'}:
        raise InvalidUpload('IP 正文仅支持 TXT、DOCX，单个文件不超过 20 MB')
    # A private temporary file, removed on success, validation failure or disconnect.
    with tempfile.TemporaryDirectory(prefix='yc-ip-import-') as directory:
        path = Path(directory) / ('source' + extension)
        size = 0
        try:
            with path.open('wb') as stream:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > LIMIT:
                        raise LibraryError('UPLOAD_TOO_LARGE', '文件超过 20 MB，本次未导入', 413)
                    stream.write(chunk)
            parsed = await asyncio.to_thread(parse_novel, path)
            return parsed, path.read_bytes(), filename
        finally:
            await file.close()


@router.get('', response_model=IPList)
def list_ips(q: str = Query(default='', max_length=200), status: Literal['', 'ready', 'missing', 'restricted'] = '',
             page: int = Query(default=1, ge=1, le=100000), genre: str = Query(default='', max_length=80)):
    return get_library().list(q, status, page, genre)


@router.post('', response_model=IPDetail, status_code=201, responses={200:{'model':IPDetail}})
async def import_ip(metadata: str = Form(..., max_length=20000), file: UploadFile | None = File(default=None), idempotency_key: str = Header(...)):
    values = ImportMetadata.model_validate_json(metadata).model_dump()
    key = operation_key(idempotency_key)
    parsed, raw, filename = await read_novel(file) if file else (None, b'', '')
    library = get_library()
    ip_id, created = library.import_file(values, parsed, raw, filename, key)
    return JSONResponse(status_code=201 if created else 200, content=details(library, ip_id))


@router.get('/{ip_id}', response_model=IPDetail)
def ip_detail(ip_id: str):
    return details(get_library(), ip_id)


@router.patch('/{ip_id}', response_model=IPDetail)
def update_ip(ip_id: str, req: MetadataUpdate):
    library = get_library()
    library.update(ip_id, req.model_dump(exclude={'expected_revision'}), req.expected_revision)
    return details(library, ip_id)


@router.post('/{ip_id}/versions', response_model=IPDetail)
async def import_version(ip_id: str, file: UploadFile = File(...), expected_revision: int = Form(..., ge=1),
                         content_scope: Literal['full', 'excerpt', 'unknown'] = Form(...), idempotency_key: str = Header(...)):
    key = operation_key(idempotency_key)
    parsed, raw, filename = await read_novel(file)
    library = get_library()
    library.import_file({'content_scope':content_scope}, parsed, raw, filename, key, ip_id, expected_revision)
    return details(library, ip_id)


@router.get('/{ip_id}/versions/{version_id}', response_model=VersionInfo)
def version_detail(ip_id: str, version_id: str):
    return {k:v for k,v in get_library().version(ip_id, version_id).items() if k != 'text'}


@router.get('/{ip_id}/versions/{version_id}/chapters/{chapter_id}', response_model=ChapterText)
def chapter_text(ip_id: str, version_id: str, chapter_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=6000, ge=100, le=20000)):
    version = get_library().version(ip_id, version_id)
    chapter = next((c for c in version['chapters'] if c['id'] == chapter_id), None)
    if not chapter:
        raise LibraryError('CHAPTER_NOT_FOUND', '找不到此章节', 404)
    total = chapter['characters']
    if offset > total:
        raise LibraryError('INVALID_OFFSET', '阅读位置超出章节范围', 422)
    end = min(offset + limit, total)
    return {'chapter':chapter, 'text':version['text'][chapter['start']+offset:chapter['start']+end], 'offset':offset,
            'first_line':version['text'].count('\n', 0, chapter['start']+offset)+1,
            'next_offset':end if end < total else None, 'total':total}


@router.get('/{ip_id}/versions/{version_id}/original', response_class=Response, responses={200:{'content':{'application/octet-stream':{}}}})
def download_original(ip_id: str, version_id: str):
    raw, name = get_library().original(ip_id, version_id)
    return Response(raw, media_type='application/octet-stream', headers={'Content-Disposition':"attachment; filename*=UTF-8''" + quote(name), 'X-Content-Type-Options':'nosniff', 'Cache-Control':'no-store'})


@router.post('/{ip_id}/chapters', response_model=IPDetail)
def edit_chapters(ip_id: str, req: ChaptersUpdate, idempotency_key: str = Header(...)):
    library = get_library()
    library.edit_chapters(ip_id, req.model_dump(), operation_key(idempotency_key))
    return details(library, ip_id)


@router.post('/{ip_id}/projects', response_model=ProjectDraftResponse, status_code=201, responses={200:{'model':ProjectDraftResponse}})
def create_adaptation(ip_id: str, req: AdaptProject, idempotency_key: str = Header(...)):
    from core.service_settings import project_defaults
    key = operation_key(idempotency_key)
    # Omitted optional presentation fields keep legacy idempotency fingerprints.
    request_hash = digest({'ip_id':ip_id, **req.model_dump(exclude_none=True)})
    # Replays remain valid even if the IP was subsequently edited.
    existing = workflow_engine.get_state(uuid.uuid5(uuid.NAMESPACE_URL, f'yuechuang:project:{key}').hex)
    if existing:
        result, _ = workflow_engine.create_draft({}, key, request_hash)
        return JSONResponse(status_code=200, content=result)
    binding = get_library().project_binding(ip_id, req.model_dump())
    meta = {**project_defaults(), 'title':req.title, 'idea':'', 'user_textbox_input':'', 'source_type':'ip',
            'ip_binding':binding, 'project_container':True, 'episodes':req.episodes, 'target_duration':req.target_duration,
            'expand_idea':False, 'enable_concurrency':False}
    if req.style is not None:
        meta['style'] = req.style
    if req.video_ratio is not None:
        meta['video_ratio'] = req.video_ratio
    result, created = workflow_engine.create_draft(meta, key, request_hash)
    return JSONResponse(status_code=201 if created else 200, content=result)

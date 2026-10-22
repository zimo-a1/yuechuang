from functools import lru_cache
from fastapi import APIRouter, Header
from api.dependencies import workflow_engine
from api.routers.ip_library import LibraryRoute, get_library, operation_key
from api.schemas.ip_library import LibraryErrorResponse
from api.schemas.novel import NovelDetail, NovelPlan, NovelJob, PublishedEpisode
from core.novel_contracts import Action, Start, DirectionSave, OutlineSave, Revision
from core.novel_adaptation import NovelAdaptation

router = APIRouter(prefix='/api/projects/{project_id}/adaptation', tags=['Novel Adaptation'], route_class=LibraryRoute,
    responses={n:{'model':LibraryErrorResponse} for n in (404,409,422,500)})


@lru_cache(maxsize=1)
def get_novel():
    return NovelAdaptation(workflow_engine, get_library())


@router.get('', response_model=NovelDetail)
def detail(project_id: str):
    return get_novel().detail(project_id)


@router.post('/plan', response_model=NovelPlan)
def plan(project_id: str, body: Action):
    return get_novel().plan(project_id, body.model_dump())


@router.post('/tasks', response_model=NovelJob, status_code=202)
async def start(project_id: str, body: Start, idempotency_key: str = Header(...)):
    return get_novel().submit(project_id, body.model_dump(), operation_key(idempotency_key))


@router.post('/tasks/{task_id}/stop', response_model=NovelJob)
def stop(project_id: str, task_id: str):
    return get_novel().stop(project_id, task_id)


@router.put('/direction', response_model=NovelDetail)
def direction(project_id: str, body: DirectionSave):
    return get_novel().review(project_id, 'direction', body.value.model_dump(), body.expected_revision, body.confirm)


@router.put('/outline', response_model=NovelDetail)
def outline(project_id: str, body: OutlineSave):
    # Omitted episode fields stay omitted, so an older outline can be reconfirmed
    # without the newer hook fields. A client that sends them is checked.
    return get_novel().review(project_id, 'outline', body.value.model_dump(exclude_unset=True), body.expected_revision, body.confirm)


@router.post('/episodes/{number}/publish', response_model=PublishedEpisode)
def publish(project_id: str, number: int, body: Revision):
    return get_novel().publish(project_id, number, body.expected_revision)

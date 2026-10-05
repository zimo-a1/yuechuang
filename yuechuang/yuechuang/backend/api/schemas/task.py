"""Public task contracts; inputs and provider configuration are never returned."""
from typing import Any, Literal

from pydantic import BaseModel, Field

TaskStatus = Literal['queued', 'running', 'stopping', 'completed', 'error', 'stopped', 'interrupted']


class TaskError(BaseModel):
    code: str
    message: str


class AcceptedTask(BaseModel):
    task_id: str
    session_id: str
    stage: str
    status: TaskStatus
    status_url: str


class TaskSnapshot(BaseModel):
    task_id: str
    session_id: str
    stage: str
    status: TaskStatus
    config_version: str | None = None
    created_at: float
    updated_at: float
    sequence: int
    events: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    error: TaskError | None = None


class TaskList(BaseModel):
    tasks: list[TaskSnapshot]

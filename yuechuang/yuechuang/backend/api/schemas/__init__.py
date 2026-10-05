"""Pydantic request/response schemas.

仅保留 IP 短剧主流程所需的模型。
已移除：sandbox（临时工作台）、pipelines（文艺短视频 / 动作迁移 / 数字人口播）。
"""

from .project import ProjectStartRequest, InterventionRequest

__all__ = [
    "ProjectStartRequest",
    "InterventionRequest",
]

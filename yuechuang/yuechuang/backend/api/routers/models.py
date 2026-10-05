# -*- coding: utf-8 -*-
"""模型清单接口。

原先该接口定义在 `api/routers/pipelines.py` 中，与三个专项流水线
（文艺短视频 / 动作迁移 / 数字人口播）耦合。专项流水线移除后，
将本接口独立出来，只依赖模型注册表，不再依赖 pipelines 包。

契约保持不变（前端 `lib/workflowApi.ts` 与 `lib/modelRegistry.ts` 依赖）：
  GET /api/models
    ?media_type=image|video      按媒体类型筛选（image/video 分支）
    ?model_type=llm|vlm|t2i|i2i|video   按模型类型筛选（文本/多模态分支）
    ?ability=<能力标签>           按适配器能力筛选（仅 media_type 分支）
    ?verified_only=true           仅返回 API 契约已校验的模型
"""

from typing import Optional

from fastapi import APIRouter, Query

from models.config_model import (
    get_models_by_type,
    list_api_models,
    model_type_capabilities,
)

router = APIRouter(tags=["Models"])


@router.get("/api/models")
async def get_api_models(
    media_type: Optional[str] = Query(None, pattern="^(image|video)$"),
    model_type: Optional[str] = Query(None, pattern="^(llm|vlm|t2i|i2i|video)$"),
    ability: Optional[str] = Query(None),
    verified_only: bool = False,
):
    # 分支一：按模型类型（llm / vlm / t2i / i2i / video）返回
    if model_type:
        models = []
        for model in get_models_by_type(model_type):
            capabilities = model_type_capabilities(model_type, model)
            models.append({
                "id": model["id"],
                "label": model.get("name") or model["id"],
                "provider": model.get("provider"),
                "family": model.get("family"),
                "model_type": model_type,
                "type": model.get("type", []),
                "concurrency": model.get("concurrency"),
                "ability_type": capabilities.get("ability_type"),
                "ability_types": capabilities.get("ability_types", []),
                "adapter_ability_types": capabilities.get("adapter_ability_types", []),
                "input_modalities": capabilities.get("input_modalities", []),
                "adapter_input_modalities": capabilities.get("adapter_input_modalities", []),
                "api_contract_verified": capabilities.get("api_contract_verified", False),
                "capabilities": capabilities,
            })
        return {"models": models}

    # 分支二：按媒体类型（image / video）返回，供工作流选择器使用
    required = [ability] if ability else None
    workflows = list_api_models(
        media_type=media_type,
        required_adapter_abilities=required,
        verified_only=verified_only,
    )
    return {
        "models": [
            {
                "id": workflow["model"],
                "label": workflow.get("display_name") or workflow["model"],
                "provider": workflow.get("provider"),
                "family": workflow.get("family"),
                "media_type": workflow.get("media_type"),
                "ability_type": workflow.get("ability_type"),
                "ability_types": workflow.get("ability_types", []),
                "adapter_ability_types": workflow.get("adapter_ability_types", []),
                "input_modalities": workflow.get("input_modalities", []),
                "adapter_input_modalities": workflow.get("adapter_input_modalities", []),
                "api_contract_verified": workflow.get("api_contract_verified", False),
                "capabilities": workflow.get("capabilities", {}),
            }
            for workflow in workflows
        ]
    }

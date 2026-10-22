# -*- coding: utf-8 -*-
"""图片 VLM 审核结果的解析与记录（角色/场景图、首帧参考图共用）。

规则：
- 审核调用失败、返回无法解析，都不再默认“合格”，而是记为“审核未完成”。
- 审核未完成时保留图片，不自动重画（避免额外扣费），由用户人工检查。
- 审核结果（分数、理由或“未完成”）写进产物，任务事件里也有一份，事后可查。
"""
import copy
import json
import re
from typing import Any, Iterable, Optional

REVIEW_UNAVAILABLE = "VLM_REVIEW_UNAVAILABLE"
UNAVAILABLE_MESSAGE = "图片已生成，但自动审核未完成，请人工检查后再继续"
SKIPPED_MESSAGE = "受控试跑未做自动审核"


def _text(result: Any) -> str:
    if isinstance(result, list):
        result = result[0] if result else ""
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    return "" if result is None else str(result)


def first_json_object(text: str) -> Optional[dict]:
    """Return the first JSON object in a model reply (fences and prose tolerated)."""
    text = re.sub(r"```(?:json)?", "", text or "", flags=re.I)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _score(value: Any):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if number != number or not 0 <= number <= 10:
        return None
    return int(number) if number.is_integer() else number


def unavailable(reason: str) -> dict:
    """Review could not be completed. Not a pass, not a fail: the user must check."""
    return {
        "score": None,
        "hard_failures": [],
        "issues": [],
        "is_acceptable": None,
        "review_unavailable": True,
        "review_error": str(reason)[:60],
    }


def parse_review(result: Any) -> dict:
    """Parse a VLM review reply. Never defaults to pass."""
    data = first_json_object(_text(result))
    if data is None:
        return unavailable("unparseable")
    score = _score(data.get("score"))
    if score is None and not isinstance(data.get("is_acceptable"), bool):
        return unavailable("missing_score")
    data["score"] = score
    return data


def _short_list(values: Any, limit: int = 5) -> list:
    if not isinstance(values, list):
        values = [values] if values else []
    return [str(item)[:120] for item in values if item][:limit]


def summarize(eval_result: Any, image_path: str = "") -> dict:
    """Small record that is safe to keep in the artifact and in task events."""
    if not isinstance(eval_result, dict) or eval_result.get("review_unavailable"):
        return {"status": "unavailable", "verified": False, "score": None,
                "reason": UNAVAILABLE_MESSAGE, "issues": [], "image": image_path or ""}
    if eval_result.get("pilot_unreviewed"):
        return {"status": "skipped", "verified": False, "score": None,
                "reason": SKIPPED_MESSAGE, "issues": [], "image": image_path or ""}
    hard_failures = _short_list(eval_result.get("hard_failures"))
    score = _score(eval_result.get("score"))
    if isinstance(eval_result.get("is_acceptable"), bool):
        passed = eval_result["is_acceptable"] and not hard_failures
    else:
        passed = not hard_failures and score is not None and score >= 7
    issues = hard_failures + _short_list(eval_result.get("issues"))
    reason = str(eval_result.get("reason") or "；".join(issues) or ("审核通过" if passed else "审核未通过"))[:300]
    return {"status": "passed" if passed else "failed", "verified": True, "score": score,
            "reason": reason, "issues": issues[:5], "image": image_path or ""}


def warning(item_type: str, item_id: str, name: str = "") -> dict:
    return {"code": REVIEW_UNAVAILABLE, "message": f"{name or item_id}：{UNAVAILABLE_MESSAGE}",
            "item_type": item_type, "item_id": item_id}


def carried_review(previous_item: Any, selected: str) -> Optional[dict]:
    """Keep an earlier review only while the same image is still selected."""
    review = previous_item.get("review") if isinstance(previous_item, dict) else None
    if isinstance(review, dict) and selected and review.get("image") == selected:
        return copy.deepcopy(review)
    return None


def warnings_for(item_type: str, items: Iterable[dict]) -> list:
    return [warning(item_type, item.get("id", ""), item.get("name", ""))
            for item in items
            if isinstance(item, dict) and (item.get("review") or {}).get("status") == "unavailable"]

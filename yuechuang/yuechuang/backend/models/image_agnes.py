# -*- coding: utf-8 -*-
"""Agnes AI 图像生成客户端

平台：https://agnes-ai.cn   （OpenAI 兼容网关）
接口：POST {base_url}/images/generations
能力：文生图 / 图生图 / 多图合成

与 ARK(Seedream) 的差异：
  - size 使用档位 1K/2K/3K/4K，并配合 ratio 参数（不是 "1920*1080"）。
  - response_format 必须放在 extra_body 里，不能放顶层。
  - 图生图的参考图放在 extra_body.image，支持 http URL 或 Data URI(Base64)。
"""

import os
import sys
import time
import base64
import logging
import mimetypes
from math import gcd
from typing import List, Optional

import requests

models_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(models_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from config import Config

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.agnes-ai.cn/v1"
SUPPORTED_RATIOS = ("1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9")


def _parse_size(size: str):
    """把 '1920*1080' / '1920x1080' 解析成 (w, h)，失败返回 None。"""
    if not size:
        return None
    try:
        parts = str(size).replace("x", "*").replace("X", "*").split("*")
        w, h = int(parts[0]), int(parts[1])
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    return None


def _tier_from_size(size: str) -> str:
    """映射到 Agnes 的分辨率档位。"""
    wh = _parse_size(size)
    if not wh:
        return "2K"
    longest = max(wh)
    if longest <= 1400:
        return "1K"
    if longest <= 2800:
        return "2K"
    if longest <= 4000:
        return "3K"
    return "4K"


def _ratio_from_size(size: str) -> str:
    """映射到 Agnes 支持的宽高比。"""
    wh = _parse_size(size)
    if not wh:
        return "16:9"
    w, h = wh
    g = gcd(w, h)
    exact = f"{w // g}:{h // g}"
    if exact in SUPPORTED_RATIOS:
        return exact
    target = w / h
    best, best_delta = "16:9", float("inf")
    for ratio in SUPPORTED_RATIOS:
        a, b = (int(x) for x in ratio.split(":"))
        delta = abs(a / b - target)
        if delta < best_delta:
            best_delta, best = delta, ratio
    return best


def _to_data_uri(path: str) -> str:
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    mime = mimetypes.guess_type(path)[0] or "image/png"
    return f"data:{mime};base64,{encoded}"


class AgnesImageClient:
    """Agnes AI 图像客户端（OpenAI 兼容 /images/generations）。"""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, timeout: int = 300):
        self.api_key = api_key or Config.AGNES_API_KEY
        self.base_url = (base_url or Config.AGNES_BASE_URL or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_attempts = 5
        if not self.api_key:
            logger.warning("AgnesImageClient: AGNES_API_KEY 未设置")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def generate_image(
        self,
        prompt: str,
        session_id: str = "default",
        model: str = "agnes-image-2.5-flash",
        size: str = "1920*1080",
        image_paths: Optional[List[str]] = None,
        **kwargs,
    ) -> List[str]:
        if not self.api_key:
            raise RuntimeError("AGNES_API_KEY not set.")

        extra_body = {"response_format": "url"}

        if image_paths:
            refs = []
            for ref in image_paths:
                ref = str(ref)
                if ref.startswith("data:"):
                    refs.append(ref)
                elif ref.startswith("http"):
                    refs.append(ref)
                elif os.path.exists(ref):
                    refs.append(_to_data_uri(ref))
                else:
                    logger.warning("AgnesImageClient: 跳过无效参考图 %s", ref)
            if refs:
                extra_body["image"] = refs

        payload = {
            "model": model,
            "prompt": prompt,
            "size": _tier_from_size(size),
            "ratio": _ratio_from_size(size),
            "extra_body": extra_body,
        }

        logger.info(
            "AgnesImageClient: model=%s size=%s tier=%s ratio=%s refs=%d",
            model, size, payload["size"], payload["ratio"], len(extra_body.get("image", [])),
        )

        last_resp = None
        for attempt in range(self.max_attempts):
            resp = requests.post(
                f"{self.base_url}/images/generations",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
                proxies=Config.requests_proxies("agnes") or None,
            )
            if resp.ok:
                last_resp = resp
                break
            last_resp = resp
            # 429 限流 / 5xx 过载（如 "生图队列已满" 503）时退避重试
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_attempts - 1:
                wait = min(5 * (attempt + 1), 30)
                logger.warning(
                    "Agnes 图像接口 %s，%ss 后重试 (%d/%d)",
                    resp.status_code, wait, attempt + 1, self.max_attempts,
                )
                time.sleep(wait)
                continue
            break
        resp = last_resp
        if resp is None or not resp.ok:
            raise RuntimeError(f"Agnes image API {resp.status_code}: {resp.text[:500]}")
            
        data = resp.json()
        items = data.get("data") or []
        paths: List[str] = []
        for idx, item in enumerate(items):
            local = self._save_item(item, session_id, idx)
            if local:
                paths.append(local)
        if not paths:
            logger.error("AgnesImageClient: 响应中没有可用图片")
        return paths

    def _save_item(self, item: dict, session_id: str, idx: int) -> Optional[str]:
        result_dir = os.path.join(backend_dir, "code", "result", "image", str(session_id))
        os.makedirs(result_dir, exist_ok=True)
        file_path = os.path.join(result_dir, f"agnes_{int(time.time())}_{idx}.png")

        b64 = item.get("b64_json")
        if b64:
            try:
                with open(file_path, "wb") as f:
                    f.write(base64.b64decode(b64))
                return file_path
            except Exception as exc:
                logger.error('AgnesImageClient: base64 保存失败: %s', type(exc).__name__)
                return None

        url = item.get("url")
        if url:
            try:
                resp = requests.get(
                    url, timeout=self.timeout,
                    proxies=Config.requests_proxies("agnes") or None,
                )
                resp.raise_for_status()
                with open(file_path, "wb") as f:
                    f.write(resp.content)
                return file_path
            except Exception as exc:
                logger.error("AgnesImageClient: 下载失败 (%s)", type(exc).__name__)
                return None
        return None
